from __future__ import annotations

import copy
import concurrent.futures
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "shopping_memory", SKILL_ROOT / "scripts" / "shopping_memory.py"
)
shopping_memory = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(shopping_memory)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_record() -> dict:
    checked = now()
    gates = {
        name: {"status": "PASS", "reason": "evidence recorded"}
        for name in shopping_memory.GATE_NAMES
    }
    return {
        "schema_version": 3,
        "session_id": "session-valid-1",
        "researched_at": checked,
        "category": "apparel_textiles",
        "branded_or_modelled": True,
        "request": {
            "summary": "buy a gift",
            "use_case": "gift",
            "region": "city-level only",
            "delivery_deadline": "five days",
            "hard_constraints": ["warm"],
            "risk_tolerance": "low",
            "budget_flexibility": "moderate",
        },
        "queries": [
            {"role": "transaction", "platform": "shop-a", "query": "item", "status": "success", "url": "https://a.example/item"},
            {"role": "transaction", "platform": "shop-b", "query": "item", "status": "success", "url": "https://b.example/item"},
            {"role": "independent_experience", "platform": "forum", "query": "item review", "status": "success", "url": "https://forum.example/review"},
            {"role": "complaint_rights", "platform": "complaints", "query": "item complaint", "status": "success", "url": "https://complaint.example/item"},
            {"role": "official", "platform": "brand", "query": "model", "status": "success", "url": "https://brand.example/model"},
        ],
        "gates": gates,
        "candidates": [
            {
                "id": "candidate-1",
                "name": "specific item",
                "finalist": True,
                "conditional_basis": "Variant, size and returns verified; remaining review restrictions would reduce confidence.",
                "purchase_checks": {
                    key: {"status": "PASS", "reason": "specific evidence checked", "source_urls": ["https://a.example/item"]}
                    for key in ("variant", "seller_and_returns", "hard_constraints", "safety")
                },
                "identity": {"verdict": "matched", "evidence": ["model and specification match"]},
                "review_coverage": {
                    "access_mode": "public_http",
                    "access_result": "complete",
                    "access_attempts": [
                        {
                            "mode": "public_http",
                            "result": "complete",
                            "source_url": "https://a.example/reviews",
                            "detail": "negative review filter was readable",
                        }
                    ],
                    "reported_negative_count": 1,
                    "accessible_negative_count": 1,
                    "read_negative_count": 1,
                    "negative_reviews": [
                        {
                            "source_url": "https://a.example/reviews/1",
                            "date": "2026-10-13",
                            "variant": "blue-m",
                            "stars": 2,
                            "summary": "one loose thread",
                            "follow_up": None,
                            "seller_response": "replacement offered",
                        }
                    ],
                    "reader_evidence": {
                        "backend": "opencli",
                        "command": "shop-a reviews candidate-1",
                        "captured_at": checked,
                        "source_url": "https://a.example/reviews",
                    },
                    "filter_verified": True,
                    "pages_read": 1,
                    "exhausted": True,
                    "adapter_version": "safe-review-reader/1.0.0",
                    "error_code": None,
                    "limitations": [],
                    "source_urls": ["https://a.example/reviews", "https://forum.example/review"],
                },
                "authenticity": {"verdict": "credible", "evidence": ["detailed dated reviews"]},
                "seller_risk": {
                    "verdict": "acceptable",
                    "evidence": ["identity and after-sales checked"],
                    "source_urls": ["https://a.example/store"],
                    "complaint_coverage": {
                        "reported_count": 0,
                        "accessible_count": 0,
                        "read_count": 0,
                        "limitations": [],
                    },
                },
                "complaint_coverage": {
                    "reported_count": 1,
                    "accessible_count": 1,
                    "read_count": 1,
                    "limitations": [],
                },
                "complaints": [
                    {
                        "source_url": "https://a.example/complaint/1",
                        "date": "2026-10-13",
                        "variant": "blue-m",
                        "summary": "one loose thread",
                        "evidence": "photo",
                        "seller_response": "replacement offered",
                        "resolved": True,
                        "severity": "ordinary_defect",
                        "scope": "product",
                    }
                ],
            }
        ],
        "price_analysis": {
            "market_has_three_bands": True,
            "bands": [
                {"name": "entry", "audited_candidate_ids": ["candidate-1"], "evidence_summary": "entry evidence"},
                {"name": "steady", "audited_candidate_ids": ["candidate-1"], "evidence_summary": "steady evidence"},
                {"name": "premium", "audited_candidate_ids": ["candidate-1"], "evidence_summary": "premium evidence"},
            ],
            "conclusion": "steady tier has the best verified value",
        },
        "dynamic_facts": [
            {"kind": kind, "status": "verified", "verified_at": checked,
             "candidate_id": "candidate-1", "source_url": "https://a.example/item",
             "value": "current fact", "amount": 150, "currency": "CNY",
             "price_type": "listed", "current_offer": True}
            for kind in ("price", "inventory", "shipping", "store_status")
        ],
        "recommendation": {"mode": "strong", "candidate_ids": ["candidate-1"], "conditions": ["confirm final checkout amount"]},
        "preference_events": [
            {"key": "risk_tolerance", "value": "low", "evidence_type": "explicit"}
        ],
        "category_findings": [
            {
                "statement": "A repeated category issue has cross-source support",
                "source_platforms": ["shop-a", "forum"],
                "source_urls": ["https://a.example/item", "https://forum.example/review"],
                "confidence": "high",
                "evidence_dates": [checked],
            }
        ],
        "pending_category": None,
    }


class ValidationTests(unittest.TestCase):
    def test_valid_record_passes_and_can_promote(self):
        result = shopping_memory.validate_record(valid_record())
        self.assertTrue(result["valid"])
        self.assertEqual("PASS", result["publication"])

    def test_one_transaction_platform_blocks_publication(self):
        record = valid_record()
        record["queries"] = [q for q in record["queries"] if q.get("platform") != "shop-b"]
        record["recommendation"]["mode"] = "conditional"
        result = shopping_memory.validate_record(record)
        self.assertEqual("BLOCKED", result["effective_gates"]["web_coverage"])
        self.assertEqual("BLOCKED", result["publication"])

    def test_two_platform_labels_on_one_domain_do_not_pass_coverage(self):
        record = valid_record()
        record["queries"][1]["platform"] = "different-label"
        record["queries"][1]["url"] = "https://a.example/another-item"
        record["recommendation"]["mode"] = "conditional"
        result = shopping_memory.validate_record(record)
        self.assertEqual("BLOCKED", result["effective_gates"]["web_coverage"])

    def test_unread_accessible_review_blocks_publication(self):
        record = valid_record()
        record["candidates"][0]["review_coverage"]["read_negative_count"] = 0
        record["recommendation"]["mode"] = "conditional"
        result = shopping_memory.validate_record(record)
        self.assertEqual("BLOCKED", result["effective_gates"]["review_completeness"])

    def test_unlogged_product_complaint_blocks_publication(self):
        record = valid_record()
        record["candidates"][0]["complaints"] = []
        record["recommendation"]["mode"] = "conditional"
        result = shopping_memory.validate_record(record)
        self.assertEqual("BLOCKED", result["effective_gates"]["complaint_severity"])

    def test_unread_seller_complaint_blocks_seller_gate(self):
        record = valid_record()
        coverage = record["candidates"][0]["seller_risk"]["complaint_coverage"]
        coverage.update({"reported_count": 1, "accessible_count": 1, "read_count": 0})
        record["recommendation"]["mode"] = "conditional"
        result = shopping_memory.validate_record(record)
        self.assertEqual("BLOCKED", result["effective_gates"]["seller"])

    def test_manipulated_reviews_fail_publication(self):
        record = valid_record()
        record["candidates"][0]["authenticity"]["verdict"] = "manipulated"
        record["recommendation"]["mode"] = "none"
        result = shopping_memory.validate_record(record)
        self.assertEqual("FAIL", result["effective_gates"]["manipulation"])
        self.assertEqual("FAIL", result["publication"])

    def test_high_risk_seller_fails(self):
        record = valid_record()
        record["candidates"][0]["seller_risk"]["verdict"] = "high"
        record["recommendation"]["mode"] = "none"
        result = shopping_memory.validate_record(record)
        self.assertEqual("FAIL", result["effective_gates"]["seller"])

    def test_identity_conflict_fails(self):
        record = valid_record()
        record["candidates"][0]["identity"]["verdict"] = "conflict"
        record["recommendation"]["mode"] = "none"
        result = shopping_memory.validate_record(record)
        self.assertEqual("FAIL", result["effective_gates"]["identity"])

    def test_old_dynamic_evidence_blocks_current_recommendation(self):
        record = valid_record()
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        for fact in record["dynamic_facts"]:
            fact["verified_at"] = old
        record["recommendation"]["mode"] = "conditional"
        result = shopping_memory.validate_record(record)
        self.assertEqual("BLOCKED", result["publication"])


class PersistenceTests(unittest.TestCase):
    def test_same_session_changed_content_is_new_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "state"
            first = valid_record()
            shopping_memory.commit_record(first, store)
            revised = copy.deepcopy(first)
            revised["candidates"][0]["fit_note"] = "new size chart evidence"
            receipt = shopping_memory.commit_record(revised, store)
            self.assertTrue(receipt["raw_written"])
            self.assertEqual(2, receipt["revision"])
            duplicate = shopping_memory.commit_record(revised, store)
            self.assertTrue(duplicate["raw_duplicate"])
            self.assertEqual(2, len((store / "raw-research.jsonl").read_text(encoding="utf-8").splitlines()))

    def test_context_never_creates_store_or_write_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "absent"
            with patch.object(shopping_memory, "_write_lock", side_effect=AssertionError("must remain read-only")):
                context = shopping_memory.load_context("apparel_textiles", store)
            self.assertEqual([], context["trusted_findings"])
            self.assertFalse(store.exists())

    def test_commit_deduplicates_and_preserves_conflicting_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "state"
            first = valid_record()
            receipt1 = shopping_memory.commit_record(first, store)
            self.assertTrue(receipt1["raw_written"])
            self.assertEqual(1, receipt1["trusted_findings"])
            receipt2 = shopping_memory.commit_record(first, store)
            self.assertTrue(receipt2["raw_duplicate"])
            self.assertEqual(0, receipt2["preferences_written"])

            second = copy.deepcopy(first)
            second["session_id"] = "session-valid-2"
            second["preference_events"][0]["value"] = "high"
            shopping_memory.commit_record(second, store)
            events = [json.loads(line) for line in (store / "preference-events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual({"low", "high"}, {item["value"] for item in events})

    def test_sensitive_values_are_redacted(self):
        value = {"phone": "13800138000", "note": "mail me at person@example.com"}
        clean = shopping_memory.redact_sensitive(value)
        self.assertEqual("[REDACTED]", clean["phone"])
        self.assertNotIn("person@example.com", clean["note"])

    def test_write_path_inside_shared_skills_tree_is_rejected(self):
        inside = SKILL_ROOT / "state-test"
        with self.assertRaises(ValueError):
            shopping_memory._ensure_store(inside)

    def test_write_path_inside_repository_or_configuration_root_is_rejected(self):
        inside = shopping_memory.CONTAINER_ROOT / "shopping-state-test"
        with self.assertRaises(ValueError):
            shopping_memory._ensure_store(inside)

    def test_commit_creates_owned_external_state_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "state"
            shopping_memory.commit_record(valid_record(), store)
            marker = json.loads((store / shopping_memory.STATE_MARKER).read_text(encoding="utf-8"))
            self.assertEqual("shopping-research-assistant", marker["owner"])
            self.assertEqual(str(store.resolve()), marker["directory"])

    def test_nonempty_unowned_state_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "state"
            store.mkdir()
            (store / "unrelated.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                shopping_memory.commit_record(valid_record(), store)

    def test_symlinked_state_root_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            with self.assertRaises(ValueError):
                shopping_memory._ensure_store(link)

    def test_windows_reparse_attribute_is_rejected(self):
        class FakePath:
            def is_symlink(self):
                return False

            def lstat(self):
                return type("Stat", (), {
                    "st_file_attributes": getattr(shopping_memory.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                })()

        if not getattr(shopping_memory.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            self.skipTest("reparse-point attributes are not available on this platform")
        self.assertTrue(shopping_memory._is_link_or_reparse(FakePath()))

    def test_cli_persistence_has_no_default_state_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {shopping_memory.STATE_ENV: ""}, clear=False):
                self.assertIsNone(shopping_memory._configured_store(None))
                with patch.object(shopping_memory, "_owned_store", side_effect=AssertionError("must not access a store")):
                    self.assertEqual(0, shopping_memory.main(["context", "--category", "test-category"]))
            self.assertEqual([], list(root.iterdir()))

    def test_concurrent_commits_are_serialized(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "state"
            shopping_memory._ensure_store(store)

            def commit(index: int) -> dict:
                record = valid_record()
                record["session_id"] = f"concurrent-{index}"
                return shopping_memory.commit_record(record, store)

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                receipts = list(pool.map(commit, range(4)))
            self.assertTrue(all(receipt["raw_written"] for receipt in receipts))
            self.assertEqual(4, len((store / "raw-research.jsonl").read_text(encoding="utf-8").splitlines()))

    def test_blocked_record_never_promotes_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            record = valid_record()
            record["queries"] = [q for q in record["queries"] if q.get("platform") != "shop-b"]
            record["recommendation"]["mode"] = "conditional"
            receipt = shopping_memory.commit_record(record, Path(directory) / "state")
            self.assertEqual(0, receipt["trusted_findings"])
            self.assertEqual(1, receipt["raw_findings"])

    def test_structurally_invalid_record_never_promotes_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            record = valid_record()
            del record["request"]["use_case"]
            record["recommendation"]["mode"] = "conditional"
            receipt = shopping_memory.commit_record(record, Path(directory) / "state")
            self.assertTrue(receipt["validation_errors"])
            self.assertEqual(0, receipt["trusted_findings"])

    def test_context_resolves_priority_and_exposes_explicit_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "state"
            first = valid_record()
            shopping_memory.commit_record(first, store)
            second = valid_record()
            second["session_id"] = "session-context-2"
            second["preference_events"] = [
                {"key": "risk_tolerance", "value": "high", "evidence_type": "explicit"},
                {"key": "platform_preference", "value": "shop-a", "evidence_type": "single_context"},
            ]
            shopping_memory.commit_record(second, store)
            context = shopping_memory.load_context("apparel_textiles", store)
            self.assertEqual("conflict", context["preference_profile"]["risk_tolerance"]["status"])
            self.assertEqual("resolved", context["preference_profile"]["platform_preference"]["status"])
            self.assertEqual(2, len(context["trusted_findings"]))


class ConditionalRegressionTests(unittest.TestCase):
    def restricted_record(self):
        record = valid_record()
        record["recommendation"]["mode"] = "conditional"
        candidate = record["candidates"][0]
        candidate["review_coverage"].update({
            "access_mode": "none", "access_result": "blocked",
            "access_attempts": [
                {
                    "mode": "public_http", "result": "blocked",
                    "source_url": "https://a.example/reviews",
                    "detail": "platform risk control 601",
                },
                {
                    "mode": "anonymous_browser", "result": "unavailable",
                    "source_url": "https://a.example/reviews",
                    "detail": "temporary browser tool unavailable",
                },
                {
                    "mode": "authorized_session", "result": "not_authorized",
                    "source_url": "https://a.example/reviews",
                    "detail": "current task did not authorize a logged-in session",
                },
            ],
            "reported_negative_count": None, "accessible_negative_count": 0,
            "read_negative_count": 0, "negative_reviews": [],
            "filter_verified": False, "pages_read": 0, "exhausted": False,
            "error_code": "AUTH_REQUIRED",
            "limitations": ["platform risk control 601"],
        })
        candidate["authenticity"]["verdict"] = "unknown"
        record["dynamic_facts"][0].update({"status": "restricted", "value": "1??"})
        return record

    def test_blocked_comments_and_hidden_price_allow_evidenced_conditional(self):
        result = shopping_memory.validate_record(self.restricted_record())
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(result["conditional_eligible"])
        self.assertEqual("BLOCKED", result["publication"])
        self.assertEqual("PASS", result["effective_gates"]["identity"])

    def test_restricted_record_cannot_claim_strong(self):
        record = self.restricted_record()
        record["recommendation"]["mode"] = "strong"
        self.assertFalse(shopping_memory.validate_record(record)["valid"])

    def test_conditional_requires_evidence_conditions_and_correct_selected_id(self):
        for change in ("conditions", "basis", "wrong_id", "missing_check", "no_sources"):
            with self.subTest(change=change):
                record = self.restricted_record()
                candidate = record["candidates"][0]
                if change == "conditions":
                    record["recommendation"]["conditions"] = []
                elif change == "basis":
                    candidate.pop("conditional_basis")
                elif change == "wrong_id":
                    record["recommendation"]["candidate_ids"] = ["wrong-sku"]
                elif change == "missing_check":
                    candidate["purchase_checks"].pop("seller_and_returns")
                else:
                    candidate["purchase_checks"]["variant"]["source_urls"] = []
                self.assertFalse(shopping_memory.validate_record(record)["valid"])

    def test_missing_size_or_failed_deadline_prevents_conditional(self):
        for status in ("BLOCKED", "FAIL"):
            record = self.restricted_record()
            record["candidates"][0]["purchase_checks"]["hard_constraints"]["status"] = status
            self.assertFalse(shopping_memory.validate_record(record)["conditional_eligible"])

    def test_failure_cannot_be_relabelled_conditional(self):
        for field, verdict in (("identity", "conflict"), ("seller_risk", "high"), ("authenticity", "manipulated")):
            record = self.restricted_record()
            record["candidates"][0][field]["verdict"] = verdict
            result = shopping_memory.validate_record(record)
            self.assertFalse(result["valid"])
            self.assertFalse(result["conditional_eligible"])

    def test_safety_unknown_and_high_risk_na_are_not_waived(self):
        for category, status in (("apparel_textiles", "BLOCKED"), ("child_pet", "N/A"), ("food_health_beauty", "N/A")):
            record = self.restricted_record()
            record["category"] = category
            record["candidates"][0]["purchase_checks"]["safety"]["status"] = status
            self.assertFalse(shopping_memory.validate_record(record)["conditional_eligible"])

    def test_user_full_audit_requirement_is_respected(self):
        record = self.restricted_record()
        record["request"]["require_full_review_before_recommendation"] = True
        self.assertFalse(shopping_memory.validate_record(record)["conditional_eligible"])

    def test_accessible_unread_negatives_are_not_waived(self):
        record = self.restricted_record()
        record["candidates"][0]["review_coverage"]["accessible_negative_count"] = 2
        self.assertFalse(shopping_memory.validate_record(record)["conditional_eligible"])

    def test_interface_block_requires_interactive_page_path(self):
        record = valid_record()
        coverage = record["candidates"][0]["review_coverage"]
        coverage["access_mode"] = "none"
        coverage["access_result"] = "blocked"
        coverage["limitations"] = ["public interface blocked"]
        coverage.update({
            "negative_reviews": [], "filter_verified": False,
            "pages_read": 0, "exhausted": False,
            "error_code": "PUBLIC_HTTP_BLOCKED",
        })
        coverage["access_attempts"] = [{
            "mode": "public_http", "result": "blocked",
            "source_url": "https://a.example/reviews", "detail": "HTTP 444",
        }]
        result = shopping_memory.validate_record(record)
        self.assertFalse(result["valid"])
        self.assertTrue(any("official client or anonymous browser path" in error for error in result["errors"]))

    def test_anonymous_browser_can_complete_after_interface_block(self):
        record = valid_record()
        coverage = record["candidates"][0]["review_coverage"]
        coverage.update({
            "access_mode": "anonymous_browser",
            "access_result": "complete",
            "access_attempts": [
                {
                    "mode": "public_http", "result": "blocked",
                    "source_url": "https://a.example/reviews", "detail": "business risk code 601",
                },
                {
                    "mode": "anonymous_browser", "result": "complete",
                    "source_url": "https://a.example/reviews", "detail": "read visible filtered reviews and pagination",
                },
            ],
        })
        result = shopping_memory.validate_record(record)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual("PASS", result["effective_gates"]["review_completeness"])

    def test_official_client_can_complete_after_interface_block(self):
        record = valid_record()
        coverage = record["candidates"][0]["review_coverage"]
        coverage.update({
            "access_mode": "official_client",
            "access_result": "complete",
            "access_attempts": [
                {
                    "mode": "public_http", "result": "blocked",
                    "source_url": "https://item.taobao.com/item.htm?id=12345", "detail": "masked review interface",
                },
                {
                    "mode": "official_client", "result": "complete",
                    "source_url": "https://item.taobao.com/item.htm?id=12345",
                    "detail": "read visible full-review panel and exhausted its accessible pages",
                    "authorization": "current_task",
                },
            ],
            "reader_evidence": {
                "backend": "taobao-official-desktop-client/manual-ui",
                "command": "visible full-review panel and pagination",
                "captured_at": "2026-10-22T12:00:00+08:00",
                "source_url": "https://item.taobao.com/item.htm?id=12345",
            },
            "adapter_version": "official-client/manual-1",
        })
        result = shopping_memory.validate_record(record)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual("PASS", result["effective_gates"]["review_completeness"])

    def test_authorized_session_requires_current_task_authorization(self):
        record = valid_record()
        coverage = record["candidates"][0]["review_coverage"]
        coverage.update({
            "access_mode": "authorized_session",
            "access_result": "complete",
            "access_attempts": [{
                "mode": "authorized_session", "result": "complete",
                "source_url": "https://a.example/reviews", "detail": "read logged-in review page",
            }],
        })
        result = shopping_memory.validate_record(record)
        self.assertFalse(result["valid"])
        self.assertTrue(any("authorization must equal current_task" in error for error in result["errors"]))

    def test_unapproved_session_is_recorded_without_being_read(self):
        record = self.restricted_record()
        result = shopping_memory.validate_record(record)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual("BLOCKED", result["effective_gates"]["review_completeness"])

    def test_captcha_stops_authorized_session(self):
        record = self.restricted_record()
        coverage = record["candidates"][0]["review_coverage"]
        coverage["access_mode"] = "authorized_session"
        coverage["access_attempts"][-1] = {
            "mode": "authorized_session", "result": "blocked",
            "source_url": "https://a.example/reviews", "detail": "CAPTCHA appeared; stopped",
            "authorization": "current_task", "challenge_detected": True,
        }
        result = shopping_memory.validate_record(record)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual("BLOCKED", result["effective_gates"]["review_completeness"])

    def test_review_access_record_rejects_cookie_or_credentials(self):
        for key in ("cookie", "password", "access_token"):
            with self.subTest(key=key):
                record = valid_record()
                record["candidates"][0]["review_coverage"]["access_attempts"][0][key] = "secret"
                self.assertFalse(shopping_memory.validate_record(record)["valid"])

    def test_external_source_cannot_claim_complete_platform_access(self):
        record = valid_record()
        coverage = record["candidates"][0]["review_coverage"]
        coverage.update({
            "access_mode": "external_source",
            "access_result": "complete",
            "access_attempts": [{
                "mode": "external_source", "result": "complete",
                "source_url": "https://forum.example/review", "detail": "independent owner report",
            }],
        })
        self.assertFalse(shopping_memory.validate_record(record)["valid"])

    def test_schema_version_one_remains_compatible(self):
        record = valid_record()
        record["schema_version"] = 1
        coverage = record["candidates"][0]["review_coverage"]
        for key in ("access_mode", "access_result", "access_attempts"):
            coverage.pop(key)
        self.assertTrue(shopping_memory.validate_record(record)["valid"])

    def test_schema_version_two_remains_compatible(self):
        record = valid_record()
        record["schema_version"] = 2
        coverage = record["candidates"][0]["review_coverage"]
        for key in (
            "negative_reviews", "reader_evidence", "filter_verified", "pages_read",
            "exhausted", "adapter_version", "error_code",
        ):
            coverage.pop(key)
        self.assertTrue(shopping_memory.validate_record(record)["valid"])

    def test_schema_three_rejects_ambiguous_empty_array(self):
        record = valid_record()
        coverage = record["candidates"][0]["review_coverage"]
        coverage.update({
            "reported_negative_count": 0,
            "accessible_negative_count": 0,
            "read_negative_count": 0,
            "negative_reviews": [],
            "filter_verified": False,
            "pages_read": 0,
            "exhausted": False,
        })
        result = shopping_memory.validate_record(record)
        self.assertFalse(result["valid"])
        self.assertEqual("BLOCKED", result["effective_gates"]["review_completeness"])

    def test_schema_three_requires_each_read_review_record(self):
        record = valid_record()
        record["candidates"][0]["review_coverage"]["negative_reviews"] = []
        result = shopping_memory.validate_record(record)
        self.assertFalse(result["valid"])
        self.assertTrue(any("length must equal read_negative_count" in error for error in result["errors"]))

    def test_schema_three_complete_requires_exhausted_and_no_error(self):
        for change in ("exhausted", "error_code"):
            with self.subTest(change=change):
                record = valid_record()
                coverage = record["candidates"][0]["review_coverage"]
                if change == "exhausted":
                    coverage["exhausted"] = False
                else:
                    coverage["error_code"] = "AUTH_REQUIRED"
                result = shopping_memory.validate_record(record)
                self.assertFalse(result["valid"])
                self.assertEqual("BLOCKED", result["effective_gates"]["review_completeness"])

    def test_schema_three_blocked_access_requires_error_code(self):
        record = self.restricted_record()
        record["candidates"][0]["review_coverage"]["error_code"] = None
        result = shopping_memory.validate_record(record)
        self.assertFalse(result["valid"])
        self.assertTrue(any("requires a non-empty error_code" in error for error in result["errors"]))

    def test_unlogged_complaints_are_not_waived(self):
        record = self.restricted_record()
        record["candidates"][0]["complaints"] = []
        self.assertFalse(shopping_memory.validate_record(record)["conditional_eligible"])

    def test_hidden_and_historical_price_never_pass_freshness(self):
        for price in (
            {"amount": "1??"}, {"status": "historical"},
            {"current_offer": False}, {"candidate_id": "wrong-sku"},
            {"verified_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        ):
            record = valid_record()
            record["dynamic_facts"][0].update(price)
            record["recommendation"]["mode"] = "none"
            self.assertEqual("BLOCKED", shopping_memory.validate_record(record)["publication"])

    def test_facts_are_bound_to_each_finalist(self):
        record = valid_record()
        other = copy.deepcopy(record["candidates"][0])
        other["id"] = "candidate-2"
        record["candidates"].append(other)
        record["recommendation"] = {"mode": "conditional", "candidate_ids": ["candidate-2"], "conditions": ["price"]}
        self.assertFalse(shopping_memory.validate_record(record)["conditional_eligible"])

    def test_stale_shipping_prevents_conditional(self):
        record = self.restricted_record()
        record["dynamic_facts"][2]["verified_at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        self.assertFalse(shopping_memory.validate_record(record)["conditional_eligible"])

    def test_jd_mobile_and_desktop_are_one_platform(self):
        record = valid_record()
        record["queries"][0]["url"] = "https://item.jd.com/1.html"
        record["queries"][1]["url"] = "https://item.m.jd.com/product/1.html"
        record["recommendation"]["mode"] = "none"
        self.assertEqual("BLOCKED", shopping_memory.validate_record(record)["effective_gates"]["web_coverage"])

    def test_transaction_ranking_is_not_independent_experience(self):
        record = valid_record()
        record["queries"][2]["url"] = "https://a.example/ranking"
        record["recommendation"]["mode"] = "none"
        self.assertEqual("BLOCKED", shopping_memory.validate_record(record)["effective_gates"]["web_coverage"])

    def test_two_relevant_bands_are_enough_when_explained(self):
        record = valid_record()
        record["price_analysis"]["bands"] = record["price_analysis"]["bands"][:2]
        record["price_analysis"]["fewer_bands_reason"] = "only two shortlisted offers are relevant to this purchase"
        self.assertTrue(shopping_memory.validate_record(record)["valid"])

    def test_severe_complaints_on_different_candidates_are_not_combined(self):
        record = valid_record()
        first = record["candidates"][0]
        first["complaints"][0]["severity"] = "safety_or_fraud"
        other = copy.deepcopy(first)
        other["id"] = "candidate-2"
        other["complaints"][0]["source_url"] = "https://b.example/different-product"
        record["candidates"].append(other)
        record["recommendation"]["mode"] = "none"
        self.assertNotEqual("FAIL", shopping_memory.validate_record(record)["effective_gates"]["complaint_severity"])


if __name__ == "__main__":
    unittest.main()
