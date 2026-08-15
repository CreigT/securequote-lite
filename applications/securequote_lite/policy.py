"""Deterministic SecureQuote Lite risk and approval policy."""

from applications.securequote_lite.business.schemas import SecureQuoteAIRecommendation, SecureQuoteIntake

HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
ELEVATED_APPROVAL = "ELEVATED_APPROVAL"
MISSING_INFORMATION = "MISSING_INFORMATION"


def evaluate_risks(intake: SecureQuoteIntake, recommendation: SecureQuoteAIRecommendation) -> list[str]:
    risks = [HUMAN_APPROVAL_REQUIRED]
    if recommendation.confidence < 80:
        risks.append(LOW_CONFIDENCE)
    if recommendation.price_max > 300:
        risks.append(ELEVATED_APPROVAL)
    critical_values = (
        intake.job_type,
        intake.service_location,
        intake.job_details.property_type,
        intake.job_details.preferred_timing,
        intake.job_details.scope_summary,
    )
    if any(not value.strip() for value in critical_values) or recommendation.risk_flags:
        risks.append(MISSING_INFORMATION)
    return list(dict.fromkeys([*risks, *recommendation.risk_flags]))
