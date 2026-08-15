"""Safe local draft workflow for SecureQuote Lite Screen 1."""

from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from applications.securequote_lite.business.schemas import (
    HumanQuoteVersion,
    SecureQuoteAIRecommendation,
    SecureQuoteDraft,
    SecureQuoteIntake,
    SecureQuoteState,
    UploadMetadata,
)


class InvalidStateTransition(ValueError):
    pass


ALLOWED_TRANSITIONS = {
    SecureQuoteState.NEW: {SecureQuoteState.ANALYZING},
    SecureQuoteState.ANALYZING: {SecureQuoteState.AWAITING_APPROVAL, SecureQuoteState.FAILED},
    SecureQuoteState.AWAITING_APPROVAL: {
        SecureQuoteState.APPROVED,
        SecureQuoteState.REJECTED,
        SecureQuoteState.NEEDS_INFORMATION,
    },
}


class SecureQuoteDraftStore:
    """Process-local Screen 1 drafts; no durable or cross-application storage."""

    def __init__(self) -> None:
        self._drafts: dict[str, SecureQuoteDraft] = {}
        self._lock = RLock()

    def create(self, intake: SecureQuoteIntake, uploads: list[UploadMetadata]) -> SecureQuoteDraft:
        draft = SecureQuoteDraft(request_id=str(uuid4()), state=SecureQuoteState.NEW, intake=intake, uploads=uploads)
        with self._lock:
            self._drafts[draft.request_id] = draft
        return draft

    def get(self, request_id: str) -> SecureQuoteDraft | None:
        with self._lock:
            return self._drafts.get(request_id)

    def transition(self, request_id: str, new_state: SecureQuoteState) -> tuple[SecureQuoteState, SecureQuoteDraft]:
        with self._lock:
            draft = self._required(request_id)
            previous = draft.state
            if new_state not in ALLOWED_TRANSITIONS.get(previous, set()):
                raise InvalidStateTransition(f"Transition {previous} to {new_state} is not allowed")
            draft.state = new_state
            return previous, draft

    def record_analysis(
        self,
        request_id: str,
        recommendation: SecureQuoteAIRecommendation,
        risks: list[str],
        provider: str,
        model: str,
    ) -> SecureQuoteDraft:
        with self._lock:
            draft = self._required(request_id)
            if draft.state != SecureQuoteState.ANALYZING:
                raise InvalidStateTransition("Analysis result requires ANALYZING state")
            draft.original_ai_recommendation = recommendation.model_copy(deep=True)
            draft.risk_flags = list(risks)
            draft.provider = provider
            draft.model = model
            draft.state = SecureQuoteState.AWAITING_APPROVAL
            return draft

    def edit(self, request_id: str, version: HumanQuoteVersion) -> SecureQuoteDraft:
        with self._lock:
            draft = self._required(request_id)
            self._require_reviewable(draft)
            draft.human_version = version.model_copy(deep=True)
            return draft

    def approve(self, request_id: str, final_price, actor: str | None = None) -> SecureQuoteDraft:
        with self._lock:
            draft = self._required(request_id)
            self._require_reviewable(draft)
            recommendation = draft.original_ai_recommendation
            if recommendation is None:
                raise InvalidStateTransition("Valid AI analysis is required")
            version = draft.human_version or HumanQuoteVersion(
                summary=recommendation.summary,
                line_items=recommendation.line_items,
                final_price=final_price,
                assumptions=recommendation.assumptions,
            )
            version.final_price = final_price
            draft.human_version = version
            draft.state = SecureQuoteState.APPROVED
            draft.approved_by = actor
            draft.approved_at = datetime.now(UTC)
            return draft

    def _required(self, request_id: str) -> SecureQuoteDraft:
        draft = self._drafts.get(request_id)
        if draft is None:
            raise KeyError(request_id)
        return draft

    @staticmethod
    def _require_reviewable(draft: SecureQuoteDraft) -> None:
        if draft.state != SecureQuoteState.AWAITING_APPROVAL or draft.original_ai_recommendation is None:
            raise InvalidStateTransition("Quote is not awaiting approval")
