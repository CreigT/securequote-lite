"""Application-specific contracts for SecureQuote Lite."""

import re
from enum import StrEnum

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class SecureQuoteState(StrEnum):
    NEW = "NEW"
    ANALYZING = "ANALYZING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    REJECTED = "REJECTED"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class SecureQuoteAuditEvent(StrEnum):
    REQUEST_CREATED = "SECUREQUOTE_REQUEST_CREATED"
    ANALYSIS_REQUESTED = "SECUREQUOTE_ANALYSIS_REQUESTED"
    VALIDATION_FAILED = "SECUREQUOTE_VALIDATION_FAILED"
    ANALYSIS_STARTED = "SECUREQUOTE_ANALYSIS_STARTED"
    ANALYSIS_COMPLETED = "SECUREQUOTE_ANALYSIS_COMPLETED"
    ANALYSIS_FAILED = "SECUREQUOTE_ANALYSIS_FAILED"
    RISK_FLAGGED = "SECUREQUOTE_RISK_FLAGGED"
    EDITED = "SECUREQUOTE_EDITED"
    APPROVED = "SECUREQUOTE_APPROVED"
    REJECTED = "SECUREQUOTE_REJECTED"
    MORE_INFO_REQUESTED = "SECUREQUOTE_MORE_INFO_REQUESTED"
    STATE_CHANGED = "SECUREQUOTE_STATE_CHANGED"


class QuoteLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: str = Field(min_length=2, max_length=240)
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input_field: str = Field(
        pattern="^(job_type|service_location|urgency|property_type|preferred_timing|scope_summary|notes|uploads)$"
    )
    fact: str = Field(min_length=1, max_length=500)


class SecureQuoteAIRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=10, max_length=1500)
    line_items: list[QuoteLineItem] = Field(min_length=1, max_length=20)
    price_min: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    price_max: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    confidence: int = Field(ge=0, le=100)
    risk_flags: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_price_range(self) -> "SecureQuoteAIRecommendation":
        if self.price_max < self.price_min:
            raise ValueError("price_max must be greater than or equal to price_min")
        return self


class HumanQuoteVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=10, max_length=1500)
    line_items: list[QuoteLineItem] = Field(min_length=1, max_length=20)
    final_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    assumptions: list[str] = Field(default_factory=list, max_length=20)


class UploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(pattern="^(photo|document)$")
    content_type: str = Field(min_length=3, max_length=150)
    size_bytes: int = Field(gt=0)


class JobDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    property_type: str = Field(min_length=2, max_length=80)
    scope_summary: str = Field(min_length=10, max_length=2000)
    preferred_timing: str = Field(min_length=2, max_length=120)


class SecureQuoteIntake(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(min_length=7, max_length=30)
    job_type: str = Field(min_length=2, max_length=120)
    service_location: str = Field(min_length=5, max_length=240)
    urgency: str = Field(pattern="^(flexible|standard|urgent)$")
    job_details: JobDetails
    notes: str = Field(default="", max_length=3000)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not re.fullmatch(r"[+()\-\.\s0-9]+", value):
            raise ValueError("Phone contains unsupported characters")
        digits = re.sub(r"\D", "", value)
        if not 7 <= len(digits) <= 15:
            raise ValueError("Phone must contain 7 to 15 digits")
        return value


class SecureQuoteDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    state: SecureQuoteState
    intake: SecureQuoteIntake
    uploads: list[UploadMetadata] = Field(default_factory=list, max_length=2)
    original_ai_recommendation: SecureQuoteAIRecommendation | None = None
    human_version: HumanQuoteVersion | None = None
    risk_flags: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None

    @property
    def upload_count(self) -> int:
        return len(self.uploads)
