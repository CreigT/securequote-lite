import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx

from applications.securequote_lite import app as securequote_module
from applications.securequote_lite.business.schemas import SecureQuoteState
from applications.securequote_lite.config import DATA_NAMESPACE, ROUTE_PREFIX
from applications.securequote_lite.workflow import SecureQuoteDraftStore
from src.ai.gateway import OpenAIResponsesProvider
from src.security.audit import AuditLogger


VALID = {
    "full_name": "Jordan Ellis",
    "email": "jordan@example.com",
    "phone": "+1 (555) 010-2020",
    "job_type": "Commercial HVAC repair",
    "service_location": "410 Market Street, Oakland, CA",
    "urgency": "standard",
    "property_type": "Office building",
    "scope_summary": "Two rooftop units are not cooling the east wing.",
    "preferred_timing": "Weekday mornings",
    "notes": "Call on arrival.",
}


class SecureQuoteIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.audit_path = Path(self.temp.name) / "securequote.jsonl"
        self.original_audit = securequote_module.audit
        self.original_drafts = securequote_module.drafts
        securequote_module.audit = AuditLogger(self.audit_path)
        securequote_module.drafts = SecureQuoteDraftStore()
        self.client = TestClient(securequote_module.app)

    def tearDown(self):
        securequote_module.audit = self.original_audit
        securequote_module.drafts = self.original_drafts
        self.temp.cleanup()

    def test_successful_valid_intake(self):
        response = self.client.post(
            f"{ROUTE_PREFIX}/api/intakes",
            data=VALID,
            files={
                "photo": ("site.jpg", b"valid-image-bytes", "image/jpeg"),
                "document": ("scope.pdf", b"valid-document-bytes", "application/pdf"),
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["state"], SecureQuoteState.NEW)
        self.assertFalse(body["ai_invoked"])
        self.assertIsNotNone(securequote_module.drafts.get(body["request_id"]))
        events = [json.loads(line)["event"] for line in self.audit_path.read_text().splitlines()]
        self.assertEqual(events, ["SECUREQUOTE_REQUEST_CREATED", "SECUREQUOTE_ANALYSIS_REQUESTED"])

    def test_missing_required_fields(self):
        response = self.client.post(f"{ROUTE_PREFIX}/api/intakes", data={"full_name": "Jordan"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "Invalid or incomplete intake request")

    def test_invalid_email(self):
        response = self.client.post(f"{ROUTE_PREFIX}/api/intakes", data={**VALID, "email": "invalid"})
        self.assertEqual(response.status_code, 422)

    def test_invalid_phone(self):
        response = self.client.post(f"{ROUTE_PREFIX}/api/intakes", data={**VALID, "phone": "call-me"})
        self.assertEqual(response.status_code, 422)

    def test_invalid_upload_type(self):
        response = self.client.post(
            f"{ROUTE_PREFIX}/api/intakes",
            data=VALID,
            files={"photo": ("payload.exe", b"not-an-image", "application/octet-stream")},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "Unsupported photo upload type")

    def test_upload_too_large(self):
        with patch("applications.securequote_lite.security.validation.MAX_UPLOAD_BYTES", 8):
            response = self.client.post(
                f"{ROUTE_PREFIX}/api/intakes",
                data=VALID,
                files={"document": ("scope.pdf", b"123456789", "application/pdf")},
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "Document upload exceeds the size limit")

    def test_securequote_route_isolation(self):
        securequote_paths = {route.path for route in securequote_module.app.routes}
        product_paths = {path for path in securequote_paths if path != "/openapi.json"}
        self.assertTrue(all(path.startswith(ROUTE_PREFIX) for path in product_paths))
        self.assertNotIn("/api/inquiries", product_paths)
        self.assertEqual(DATA_NAMESPACE, "securequote_lite_drafts")

    def test_no_business_intake_dependency(self):
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path("applications/securequote_lite").rglob("*.py")
            if path.name != "test_securequote.py"
        )
        self.assertNotIn("src.business.intake", sources)
        self.assertNotIn("src.intake_workflow", sources)
        self.assertNotIn("import app as business_intake_app", sources)


class RecordingClient:
    def __init__(self, output):
        self.output = output

    def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(self.output)}]}]},
            request=request,
        )


ANALYSIS = {
    "summary": "Service inspection and repair assessment based on the submitted HVAC symptoms.",
    "line_items": [{"description": "Diagnostic inspection", "amount": "175.00"}],
    "price_min": "175.00",
    "price_max": "450.00",
    "assumptions": ["Final parts requirements depend on onsite diagnosis."],
    "confidence": 76,
    "risk_flags": [],
    "evidence": [
        {"input_field": "job_type", "fact": "Commercial HVAC repair requested."},
        {"input_field": "scope_summary", "fact": "Two rooftop units are not cooling the east wing."},
    ],
}


class SecureQuoteReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.audit_path = Path(self.temp.name) / "review.jsonl"
        self.original_audit, self.original_drafts = securequote_module.audit, securequote_module.drafts
        securequote_module.audit = AuditLogger(self.audit_path)
        securequote_module.drafts = SecureQuoteDraftStore()
        self.client = TestClient(securequote_module.app)
        response = self.client.post(f"{ROUTE_PREFIX}/api/intakes", data=VALID)
        self.assertEqual(response.status_code, 201)
        self.request_id = response.json()["request_id"]

    def tearDown(self):
        securequote_module.audit, securequote_module.drafts = self.original_audit, self.original_drafts
        self.temp.cleanup()

    def provider(self, output=ANALYSIS):
        return OpenAIResponsesProvider(
            api_key="test-only-contract-key",
            model="configured-test-model",
            client=RecordingClient(output),
        )

    def analyze(self, output=ANALYSIS):
        with patch.object(securequote_module, "create_ai_provider", return_value=self.provider(output)):
            return self.client.post(f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/analyze")

    def test_real_screen_one_intake_loads_and_missing_is_rejected(self):
        loaded = self.client.get(f"{ROUTE_PREFIX}/api/intakes/{self.request_id}")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["intake"]["email"], VALID["email"])
        self.assertEqual(self.client.get(f"{ROUTE_PREFIX}/api/intakes/missing").status_code, 404)

    def test_missing_provider_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post(f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/analyze")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["state"], "FAILED")
        self.assertNotIn("original_ai_recommendation", response.json())

    def test_valid_structured_result_and_risk_policy(self):
        response = self.analyze()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["state"], "AWAITING_APPROVAL")
        self.assertIn("LOW_CONFIDENCE", body["risk_flags"])
        self.assertIn("ELEVATED_APPROVAL", body["risk_flags"])
        self.assertIn("HUMAN_APPROVAL_REQUIRED", body["risk_flags"])

    def test_malformed_provider_output_is_rejected(self):
        response = self.analyze({"summary": "incomplete"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["state"], "FAILED")

    def test_approval_requires_analysis_and_final_price(self):
        premature = self.client.post(
            f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/approve", data={"final_price": "200"}
        )
        self.assertEqual(premature.status_code, 409)
        self.analyze()
        missing = self.client.post(f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/approve", data={})
        self.assertEqual(missing.status_code, 422)

    def test_edit_preserves_original_and_approval_is_internal(self):
        analyzed = self.analyze().json()
        original = analyzed["original_ai_recommendation"]
        edit = {
            "summary": "Human-reviewed HVAC diagnostic scope with customer confirmation still required.",
            "line_items": [{"description": "Human-reviewed diagnostic", "amount": "200.00"}],
            "final_price": "250.00",
            "assumptions": ["Parts require separate approval."],
        }
        edited = self.client.post(
            f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/edit", json=edit
        ).json()
        self.assertEqual(edited["original_ai_recommendation"], original)
        self.assertNotEqual(edited["human_version"]["summary"], original["summary"])
        approved = self.client.post(
            f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/approve", data={"final_price": "250.00"}
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["state"], "APPROVED")
        self.assertIsNone(approved.json()["approved_by"])

    def test_reject_and_more_info_transitions(self):
        self.analyze()
        rejected = self.client.post(f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/reject")
        self.assertEqual(rejected.json()["state"], "REJECTED")
        second = self.client.post(f"{ROUTE_PREFIX}/api/intakes", data=VALID).json()["request_id"]
        with patch.object(securequote_module, "create_ai_provider", return_value=self.provider()):
            self.client.post(f"{ROUTE_PREFIX}/api/intakes/{second}/analyze")
        more = self.client.post(f"{ROUTE_PREFIX}/api/intakes/{second}/request-more-info")
        self.assertEqual(more.json()["state"], "NEEDS_INFORMATION")

    def test_invalid_transition_audits_and_screen_three_is_absent(self):
        self.analyze()
        self.client.post(f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/reject")
        repeated = self.client.post(f"{ROUTE_PREFIX}/api/intakes/{self.request_id}/reject")
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(self.client.get(f"{ROUTE_PREFIX}/quote/{self.request_id}").status_code, 404)
        events = [json.loads(line)["event"] for line in self.audit_path.read_text().splitlines()]
        self.assertIn("SECUREQUOTE_ANALYSIS_COMPLETED", events)
        self.assertIn("SECUREQUOTE_RISK_FLAGGED", events)
        self.assertIn("SECUREQUOTE_REJECTED", events)
        self.assertIn("SECUREQUOTE_STATE_CHANGED", events)


if __name__ == "__main__":
    unittest.main()
