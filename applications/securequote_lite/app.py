"""Standalone SecureQuote Lite Screens 1 and 2 application."""

from pathlib import Path
from decimal import Decimal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError

from applications.securequote_lite.business.schemas import (
    JobDetails,
    HumanQuoteVersion,
    SecureQuoteAuditEvent,
    SecureQuoteIntake,
    SecureQuoteState,
    UploadMetadata,
)
from applications.securequote_lite.ai import analyze_draft
from applications.securequote_lite.config import AI_PROVIDER, AUDIT_NAMESPACE, AUDIT_PATH, ROUTE_PREFIX
from applications.securequote_lite.security.validation import (
    SecureQuoteValidationError,
    sanitize_text,
    validate_upload,
)
from applications.securequote_lite.policy import evaluate_risks
from applications.securequote_lite.workflow import InvalidStateTransition, SecureQuoteDraftStore
from src.ai.gateway import AIGatewayError, AIProviderNotConfigured, create_ai_provider
from src.security.audit import AuditLogger

APP_ROOT = Path(__file__).resolve().parent

app = FastAPI(
    title="SecureQuote Lite",
    description="Secure service quote intake with human-controlled next steps.",
    docs_url=None,
    redoc_url=None,
)
audit = AuditLogger(AUDIT_PATH)
drafts = SecureQuoteDraftStore()


@app.middleware("http")
async def secure_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.exception_handler(RequestValidationError)
async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    audit.write(
        workflow=AUDIT_NAMESPACE,
        event=SecureQuoteAuditEvent.VALIDATION_FAILED,
        validation_result="failed",
        error_count=len(exc.errors()),
    )
    return JSONResponse({"error": "Invalid or incomplete intake request"}, status_code=422)


@app.get(ROUTE_PREFIX, include_in_schema=False)
def intake_page() -> FileResponse:
    return FileResponse(APP_ROOT / "web" / "index.html")


@app.get(f"{ROUTE_PREFIX}/assets/styles.css", include_in_schema=False)
def styles() -> FileResponse:
    return FileResponse(APP_ROOT / "web" / "styles.css", media_type="text/css")


@app.get(f"{ROUTE_PREFIX}/assets/app.js", include_in_schema=False)
def javascript() -> FileResponse:
    return FileResponse(APP_ROOT / "web" / "app.js", media_type="text/javascript")


@app.get(f"{ROUTE_PREFIX}/review/{{request_id}}", include_in_schema=False)
def review_page(request_id: str) -> Response:
    if drafts.get(request_id) is None:
        return JSONResponse({"error": "SecureQuote intake not found"}, status_code=404)
    return FileResponse(APP_ROOT / "web" / "review.html")


@app.get(f"{ROUTE_PREFIX}/assets/review.js", include_in_schema=False)
def review_javascript() -> FileResponse:
    return FileResponse(APP_ROOT / "web" / "review.js", media_type="text/javascript")


@app.get(f"{ROUTE_PREFIX}/assets/review.css", include_in_schema=False)
def review_styles() -> FileResponse:
    return FileResponse(APP_ROOT / "web" / "review.css", media_type="text/css")


@app.get(f"{ROUTE_PREFIX}/health")
def health() -> dict[str, object]:
    try:
        ai_enabled = bool(getattr(create_ai_provider(AI_PROVIDER), "configured", False))
    except AIProviderNotConfigured:
        ai_enabled = False
    return {"status": "available", "application": "securequote_lite", "ai_enabled": ai_enabled}


@app.post(f"{ROUTE_PREFIX}/api/intakes")
async def create_intake(
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    job_type: str = Form(...),
    service_location: str = Form(...),
    urgency: str = Form(...),
    property_type: str = Form(...),
    scope_summary: str = Form(...),
    preferred_timing: str = Form(...),
    notes: str = Form(""),
    photo: UploadFile | None = File(None),
    document: UploadFile | None = File(None),
) -> JSONResponse:
    try:
        intake = SecureQuoteIntake(
            full_name=sanitize_text(full_name),
            email=sanitize_text(email),
            phone=sanitize_text(phone),
            job_type=sanitize_text(job_type),
            service_location=sanitize_text(service_location),
            urgency=sanitize_text(urgency),
            job_details=JobDetails(
                property_type=sanitize_text(property_type),
                scope_summary=sanitize_text(scope_summary),
                preferred_timing=sanitize_text(preferred_timing),
            ),
            notes=sanitize_text(notes),
        )
        validated_uploads = [
            item
            for item in (await validate_upload(photo, "photo"), await validate_upload(document, "document"))
            if item is not None
        ]
    except (ValidationError, SecureQuoteValidationError) as exc:
        audit.write(
            workflow=AUDIT_NAMESPACE,
            event=SecureQuoteAuditEvent.VALIDATION_FAILED,
            validation_result="failed",
            error_type=type(exc).__name__,
        )
        message = str(exc) if isinstance(exc, SecureQuoteValidationError) else "Invalid or incomplete intake request"
        return JSONResponse({"error": message}, status_code=422)

    upload_metadata = [UploadMetadata(**item.__dict__) for item in validated_uploads]
    draft = drafts.create(intake, upload_metadata)
    audit.write(
        workflow=AUDIT_NAMESPACE,
        event=SecureQuoteAuditEvent.REQUEST_CREATED,
        request_id=draft.request_id,
        state=draft.state,
        upload_count=draft.upload_count,
    )
    audit.write(
        workflow=AUDIT_NAMESPACE,
        event=SecureQuoteAuditEvent.ANALYSIS_REQUESTED,
        request_id=draft.request_id,
        decision="DRAFT_ONLY",
        ai_invoked=False,
    )
    return JSONResponse(
        {
            "request_id": draft.request_id,
            "state": draft.state,
            "message": "Intake validated and saved as a local server draft. Continue to internal review for analysis.",
            "ai_invoked": False,
            "review_url": f"{ROUTE_PREFIX}/review/{draft.request_id}",
        },
        status_code=201,
    )


@app.get(f"{ROUTE_PREFIX}/api/intakes/{{request_id}}")
def get_intake(request_id: str) -> JSONResponse:
    draft = drafts.get(request_id)
    if draft is None:
        return JSONResponse({"error": "SecureQuote intake not found"}, status_code=404)
    return JSONResponse(draft.model_dump(mode="json"))


@app.post(f"{ROUTE_PREFIX}/api/intakes/{{request_id}}/analyze")
def analyze_intake(request_id: str) -> JSONResponse:
    draft = drafts.get(request_id)
    if draft is None:
        return JSONResponse({"error": "SecureQuote intake not found"}, status_code=404)
    try:
        previous, draft = drafts.transition(request_id, SecureQuoteState.ANALYZING)
    except InvalidStateTransition as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    _audit_state(request_id, previous, draft.state)
    audit.write(
        workflow=AUDIT_NAMESPACE,
        application_namespace=AUDIT_NAMESPACE,
        event=SecureQuoteAuditEvent.ANALYSIS_STARTED,
        resource_type="securequote",
        resource_id=request_id,
        previous_state=previous,
        new_state=draft.state,
        outcome="started",
    )
    try:
        provider = create_ai_provider(AI_PROVIDER)
        result = analyze_draft(draft, provider)
        risks = evaluate_risks(draft.intake, result.output)
        completed = drafts.record_analysis(
            request_id,
            result.output,
            risks,
            str(result.metadata.provider),
            result.metadata.model,
        )
        audit.write(
            workflow=AUDIT_NAMESPACE,
            application_namespace=AUDIT_NAMESPACE,
            event=SecureQuoteAuditEvent.ANALYSIS_COMPLETED,
            resource_type="securequote",
            resource_id=request_id,
            previous_state=SecureQuoteState.ANALYZING,
            new_state=completed.state,
            outcome="completed",
            provider=result.metadata.provider,
            model=result.metadata.model,
            confidence=result.output.confidence,
            latency_ms=result.metadata.latency_ms,
            risk_categories=risks,
        )
        _audit_state(request_id, SecureQuoteState.ANALYZING, completed.state)
        if risks:
            audit.write(
                workflow=AUDIT_NAMESPACE,
                application_namespace=AUDIT_NAMESPACE,
                event=SecureQuoteAuditEvent.RISK_FLAGGED,
                resource_type="securequote",
                resource_id=request_id,
                risk_categories=risks,
                outcome="review_required",
            )
        return JSONResponse(completed.model_dump(mode="json"))
    except AIGatewayError as exc:
        _, failed = drafts.transition(request_id, SecureQuoteState.FAILED)
        audit.write(
            workflow=AUDIT_NAMESPACE,
            application_namespace=AUDIT_NAMESPACE,
            event=SecureQuoteAuditEvent.ANALYSIS_FAILED,
            resource_type="securequote",
            resource_id=request_id,
            previous_state=SecureQuoteState.ANALYZING,
            new_state=failed.state,
            outcome="failed",
            error_category=exc.category,
        )
        _audit_state(request_id, SecureQuoteState.ANALYZING, failed.state)
        status_code = 503 if isinstance(exc, AIProviderNotConfigured) else 502
        message = (
            "AI provider is not configured. Set SECUREQUOTE_LITE_AI_PROVIDER and the selected provider's server credentials/model."
            if isinstance(exc, AIProviderNotConfigured)
            else f"AI analysis failed ({exc.category}). No recommendation was created."
        )
        return JSONResponse({"error": message, "state": failed.state}, status_code=status_code)


@app.post(f"{ROUTE_PREFIX}/api/intakes/{{request_id}}/edit")
def edit_quote(request_id: str, version: HumanQuoteVersion) -> JSONResponse:
    try:
        draft = drafts.edit(request_id, version)
    except KeyError:
        return JSONResponse({"error": "SecureQuote intake not found"}, status_code=404)
    except InvalidStateTransition as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    audit.write(
        workflow=AUDIT_NAMESPACE,
        application_namespace=AUDIT_NAMESPACE,
        event=SecureQuoteAuditEvent.EDITED,
        resource_type="securequote",
        resource_id=request_id,
        actor=None,
        outcome="saved",
        original_preserved=True,
    )
    return JSONResponse(draft.model_dump(mode="json"))


@app.post(f"{ROUTE_PREFIX}/api/intakes/{{request_id}}/approve")
def approve_quote(request_id: str, final_price: Decimal = Form(...)) -> JSONResponse:
    try:
        if final_price < 0:
            raise ValueError("Final price must be non-negative")
        previous = SecureQuoteState.AWAITING_APPROVAL
        draft = drafts.approve(request_id, final_price, actor=None)
    except KeyError:
        return JSONResponse({"error": "SecureQuote intake not found"}, status_code=404)
    except (InvalidStateTransition, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    audit.write(
        workflow=AUDIT_NAMESPACE,
        application_namespace=AUDIT_NAMESPACE,
        event=SecureQuoteAuditEvent.APPROVED,
        resource_type="securequote",
        resource_id=request_id,
        actor=None,
        previous_state=previous,
        new_state=draft.state,
        outcome="approved_internal_only",
        final_price=str(final_price),
        original_preserved=True,
    )
    _audit_state(request_id, previous, draft.state)
    return JSONResponse(draft.model_dump(mode="json"))


@app.post(f"{ROUTE_PREFIX}/api/intakes/{{request_id}}/reject")
def reject_quote(request_id: str) -> JSONResponse:
    return _review_transition(request_id, SecureQuoteState.REJECTED, SecureQuoteAuditEvent.REJECTED)


@app.post(f"{ROUTE_PREFIX}/api/intakes/{{request_id}}/request-more-info")
def request_more_info(request_id: str) -> JSONResponse:
    return _review_transition(
        request_id, SecureQuoteState.NEEDS_INFORMATION, SecureQuoteAuditEvent.MORE_INFO_REQUESTED
    )


def _review_transition(request_id: str, state: SecureQuoteState, event: SecureQuoteAuditEvent) -> JSONResponse:
    try:
        previous, draft = drafts.transition(request_id, state)
    except KeyError:
        return JSONResponse({"error": "SecureQuote intake not found"}, status_code=404)
    except InvalidStateTransition as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    audit.write(
        workflow=AUDIT_NAMESPACE,
        application_namespace=AUDIT_NAMESPACE,
        event=event,
        resource_type="securequote",
        resource_id=request_id,
        actor=None,
        previous_state=previous,
        new_state=draft.state,
        outcome="completed",
    )
    _audit_state(request_id, previous, draft.state)
    return JSONResponse(draft.model_dump(mode="json"))


def _audit_state(request_id: str, previous: SecureQuoteState, new: SecureQuoteState) -> None:
    audit.write(
        workflow=AUDIT_NAMESPACE,
        application_namespace=AUDIT_NAMESPACE,
        event=SecureQuoteAuditEvent.STATE_CHANGED,
        resource_type="securequote",
        resource_id=request_id,
        previous_state=previous,
        new_state=new,
        outcome="transitioned",
    )
