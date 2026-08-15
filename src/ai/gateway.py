"""Application-neutral, fail-closed structured AI provider gateway."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Any, Generic, Mapping, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class AIProviderName(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"


class AIErrorCategory(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    REFUSAL = "refusal"
    INVALID_OUTPUT = "invalid_output"
    NETWORK = "network"
    PROVIDER = "provider"


class AIGatewayError(RuntimeError):
    """Safe provider failure that never includes credentials or request content."""

    def __init__(self, message: str, category: AIErrorCategory) -> None:
        super().__init__(message)
        self.category = category


class AIProviderNotConfigured(AIGatewayError):
    def __init__(self, message: str = "AI provider is not configured") -> None:
        super().__init__(message, AIErrorCategory.CONFIGURATION)


class AIProviderOperationError(AIGatewayError):
    metadata: ProviderOperationMetadata


class StructuredGenerationRequest(BaseModel, Generic[StructuredOutput]):
    """Provider-neutral request. Applications own the instructions, input, and output model."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    instructions: str = Field(min_length=1, max_length=12_000)
    input_data: Mapping[str, Any]
    output_model: type[StructuredOutput]
    schema_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ProviderOperationMetadata(BaseModel):
    """Non-sensitive metadata safe to pass to foundation audit logging."""

    model_config = ConfigDict(extra="forbid")

    provider: AIProviderName
    model: str = Field(min_length=1, max_length=200)
    operation_status: str = Field(pattern=r"^(completed|failed)$")
    timestamp: datetime
    latency_ms: int = Field(ge=0)
    error_category: AIErrorCategory | None = None


class StructuredGenerationResult(BaseModel, Generic[StructuredOutput]):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    output: StructuredOutput
    metadata: ProviderOperationMetadata


class StructuredAIProvider(Protocol):
    def generate(self, request: StructuredGenerationRequest[StructuredOutput]) -> StructuredGenerationResult[StructuredOutput]: ...


class HTTPClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> httpx.Response: ...


class OpenAIResponsesProvider:
    """Real OpenAI Responses API adapter with strict structured-output validation."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        client: HTTPClient | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.model = model if model is not None else os.getenv("OPENAI_MODEL", "")
        configured_timeout = timeout_seconds if timeout_seconds is not None else os.getenv("AI_GATEWAY_TIMEOUT_SECONDS", "30")
        try:
            self.timeout_seconds = float(configured_timeout)
        except (TypeError, ValueError) as exc:
            raise AIProviderNotConfigured("AI gateway timeout is invalid") from exc
        if not 1 <= self.timeout_seconds <= 300:
            raise AIProviderNotConfigured("AI gateway timeout must be between 1 and 300 seconds")
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._api_key.strip() and self.model.strip())

    def generate(
        self, request: StructuredGenerationRequest[StructuredOutput]
    ) -> StructuredGenerationResult[StructuredOutput]:
        if not self.configured:
            raise AIProviderNotConfigured()

        started = monotonic()
        timestamp = datetime.now(UTC)
        schema = request.output_model.model_json_schema()
        payload = {
            "model": self.model,
            "store": False,
            "instructions": request.instructions,
            "input": json.dumps(request.input_data, ensure_ascii=True, separators=(",", ":")),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        owned_client: httpx.Client | None = None
        try:
            if self._client is None:
                owned_client = httpx.Client(timeout=self.timeout_seconds)
            client = self._client or owned_client
            if client is None:
                raise AIProviderOperationError("AI provider client is unavailable", AIErrorCategory.PROVIDER)
            response = client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            output_text = self._extract_output_text(response.json())
            validated = request.output_model.model_validate_json(output_text)
            return StructuredGenerationResult(
                output=validated,
                metadata=self._metadata("completed", timestamp, started),
            )
        except AIGatewayError:
            raise
        except httpx.TimeoutException as exc:
            raise self._operation_error(AIErrorCategory.TIMEOUT, "AI provider request timed out") from exc
        except httpx.HTTPStatusError as exc:
            category = self._http_error_category(exc.response.status_code)
            raise self._operation_error(category, "AI provider rejected the request") from exc
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise self._operation_error(AIErrorCategory.INVALID_OUTPUT, "AI provider returned invalid structured output") from exc
        except httpx.HTTPError as exc:
            raise self._operation_error(AIErrorCategory.PROVIDER, "AI provider request failed") from exc
        finally:
            if owned_client is not None:
                owned_client.close()

    def _extract_output_text(self, body: Any) -> str:
        if not isinstance(body, dict):
            raise ValueError("Invalid provider response")
        for item in body.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "refusal":
                    raise self._operation_error(AIErrorCategory.REFUSAL, "AI provider refused the request")
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str) and text:
                        return text
        raise ValueError("Missing structured output")

    def _metadata(
        self,
        status: str,
        timestamp: datetime,
        started: float,
        category: AIErrorCategory | None = None,
    ) -> ProviderOperationMetadata:
        return ProviderOperationMetadata(
            provider=AIProviderName.OPENAI,
            model=self.model,
            operation_status=status,
            timestamp=timestamp,
            latency_ms=max(0, round((monotonic() - started) * 1000)),
            error_category=category,
        )

    def _operation_error(self, category: AIErrorCategory, message: str) -> AIProviderOperationError:
        error = AIProviderOperationError(message, category)
        error.metadata = ProviderOperationMetadata(
            provider=AIProviderName.OPENAI,
            model=self.model,
            operation_status="failed",
            timestamp=datetime.now(UTC),
            latency_ms=0,
            error_category=category,
        )
        return error

    @staticmethod
    def _http_error_category(status_code: int) -> AIErrorCategory:
        if status_code in {401, 403}:
            return AIErrorCategory.AUTHENTICATION
        if status_code == 429:
            return AIErrorCategory.RATE_LIMIT
        return AIErrorCategory.PROVIDER


class GeminiStructuredProvider:
    """Official Google GenAI SDK adapter for provider-neutral structured generation."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        self.model = model if model is not None else os.getenv("GEMINI_MODEL", "")
        configured_timeout = timeout_seconds if timeout_seconds is not None else os.getenv("AI_GATEWAY_TIMEOUT_SECONDS", "30")
        try:
            self.timeout_seconds = float(configured_timeout)
        except (TypeError, ValueError) as exc:
            raise AIProviderNotConfigured("AI gateway timeout is invalid") from exc
        if not 1 <= self.timeout_seconds <= 300:
            raise AIProviderNotConfigured("AI gateway timeout must be between 1 and 300 seconds")
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._api_key.strip() and self.model.strip())

    def generate(
        self, request: StructuredGenerationRequest[StructuredOutput]
    ) -> StructuredGenerationResult[StructuredOutput]:
        if not self.configured:
            raise AIProviderNotConfigured()

        started = monotonic()
        timestamp = datetime.now(UTC)
        owned_client: Any | None = None
        try:
            if self._client is None:
                owned_client = self._create_client()
            client = self._client or owned_client
            if client is None:
                raise self._operation_error(
                    AIErrorCategory.PROVIDER, "AI provider client is unavailable", timestamp, started
                )
            response = client.models.generate_content(
                model=self.model,
                contents=json.dumps(request.input_data, ensure_ascii=True, separators=(",", ":")),
                config={
                    "system_instruction": request.instructions,
                    "response_mime_type": "application/json",
                    "response_json_schema": request.output_model.model_json_schema(),
                },
            )
            output_text = getattr(response, "text", None)
            if not isinstance(output_text, str) or not output_text:
                raise ValueError("Missing structured output")
            validated = request.output_model.model_validate_json(output_text)
            return StructuredGenerationResult(
                output=validated,
                metadata=self._metadata("completed", timestamp, started),
            )
        except AIGatewayError:
            raise
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise self._operation_error(
                AIErrorCategory.INVALID_OUTPUT,
                "AI provider returned invalid structured output",
                timestamp,
                started,
            ) from exc
        except Exception as exc:
            category = self._exception_category(exc)
            message = "AI provider request timed out" if category == AIErrorCategory.TIMEOUT else "AI provider request failed"
            raise self._operation_error(category, message, timestamp, started) from exc
        finally:
            if owned_client is not None:
                owned_client.close()

    def _create_client(self) -> Any:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise AIProviderNotConfigured("Gemini provider dependency is unavailable") from exc
        return genai.Client(
            api_key=self._api_key,
            http_options=types.HttpOptions(timeout=round(self.timeout_seconds * 1000)),
        )

    def _metadata(
        self,
        status: str,
        timestamp: datetime,
        started: float,
        category: AIErrorCategory | None = None,
    ) -> ProviderOperationMetadata:
        return ProviderOperationMetadata(
            provider=AIProviderName.GEMINI,
            model=self.model,
            operation_status=status,
            timestamp=timestamp,
            latency_ms=max(0, round((monotonic() - started) * 1000)),
            error_category=category,
        )

    def _operation_error(
        self,
        category: AIErrorCategory,
        message: str,
        timestamp: datetime,
        started: float,
    ) -> AIProviderOperationError:
        error = AIProviderOperationError(message, category)
        error.metadata = self._metadata("failed", timestamp, started, category)
        return error

    @staticmethod
    def _exception_category(exc: Exception) -> AIErrorCategory:
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return AIErrorCategory.TIMEOUT
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        name = type(exc).__name__.lower()
        safe_detail = str(exc).lower()
        if (
            status in {401, 403}
            or "authentication" in name
            or "permissiondenied" in name
            or (status == 400 and ("api key" in safe_detail or "api_key_invalid" in safe_detail))
        ):
            return AIErrorCategory.AUTHENTICATION
        if status == 429 or "ratelimit" in name or "resourceexhausted" in name:
            return AIErrorCategory.RATE_LIMIT
        if status in {400, 422}:
            return AIErrorCategory.INVALID_OUTPUT
        if isinstance(exc, httpx.NetworkError) or "connection" in name or "network" in name:
            return AIErrorCategory.NETWORK
        return AIErrorCategory.PROVIDER


def create_ai_provider(provider_name: str | None = None, **kwargs: Any) -> StructuredAIProvider:
    """Select an approved provider and fail closed for missing or unknown names."""

    selected = (provider_name if provider_name is not None else os.getenv("AI_GATEWAY_PROVIDER", "")).strip().lower()
    if not selected:
        raise AIProviderNotConfigured("AI gateway provider is not selected")
    if selected == AIProviderName.OPENAI:
        return OpenAIResponsesProvider(**kwargs)
    if selected == AIProviderName.GEMINI:
        return GeminiStructuredProvider(**kwargs)
    raise AIProviderNotConfigured("AI gateway provider is not approved")
