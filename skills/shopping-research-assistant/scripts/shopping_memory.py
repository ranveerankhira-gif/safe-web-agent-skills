#!/usr/bin/env python3
"""Validate gated shopping research and append privacy-filtered local history."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent.resolve()
CONTAINER_ROOT = SKILLS_ROOT.parent.resolve()
FORBIDDEN_STATE_ROOTS = (SKILL_ROOT, SKILLS_ROOT, CONTAINER_ROOT)
STATE_ENV = "SHOPPING_RESEARCH_STATE_ROOT"
STATE_MARKER = ".shopping-research-state.json"
GATE_NAMES = (
    "requirement",
    "web_coverage",
    "identity",
    "review_completeness",
    "manipulation",
    "complaint_severity",
    "seller",
    "price_value",
    "publication",
)
PRE_PUBLICATION_GATES = GATE_NAMES[:-1]
TRUST_GATES = (
    "web_coverage",
    "identity",
    "review_completeness",
    "manipulation",
    "complaint_severity",
)
STATUSES = {"PASS", "FAIL", "BLOCKED", "N/A"}
STATUS_WEIGHT = {"PASS": 0, "N/A": 0, "BLOCKED": 1, "FAIL": 2}
SOURCE_ROLES = {
    "transaction",
    "independent_experience",
    "complaint_rights",
    "official",
    "price_history",
    "other",
}
AUTHENTICITY = {"credible", "mixed", "manipulated", "unknown"}
IDENTITY = {"matched", "unique", "conflict", "unknown"}
SELLER_RISK = {"acceptable", "mixed", "high", "unknown"}
COMPLAINT_SEVERITIES = {
    "safety_or_fraud",
    "repeated_functional",
    "ordinary_defect",
    "logistics_service",
    "subjective_mismatch",
}
COMPLAINT_SCOPES = {"product", "seller"}
REVIEW_ACCESS_MODES = {
    "public_http",
    "anonymous_browser",
    "official_client",
    "authorized_session",
    "external_source",
    "none",
}
REVIEW_ACCESS_RESULTS = {"complete", "partial", "blocked", "unavailable"}
REVIEW_ATTEMPT_RESULTS = REVIEW_ACCESS_RESULTS | {"not_authorized"}
REVIEW_PRIVATE_KEY_PARTS = {
    "user",
    "username",
    "nickname",
    "avatar",
    "account",
    "cookie",
    "token",
    "credential",
    "用户名",
    "昵称",
    "头像",
    "账号",
}
PREFERENCE_TYPES = {"explicit", "repeated_inference", "single_context"}
PREFERENCE_PRIORITY = {"explicit": 3, "repeated_inference": 2, "single_context": 1}
SENSITIVE_KEY_PARTS = {
    "full_address",
    "exact_address",
    "account",
    "account_id",
    "order",
    "order_id",
    "phone",
    "telephone",
    "mobile",
    "identity_card",
    "id_card",
    "payment",
    "bank_card",
    "recipient_name",
    "cookie",
    "password",
    "passwd",
    "access_token",
    "refresh_token",
    "credential",
    "收货地址",
    "详细地址",
    "账号",
    "订单号",
    "手机号",
    "电话",
    "身份证",
    "银行卡",
    "支付信息",
    "收礼人姓名",
}
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _domain(value: str) -> str:
    host = (urlparse(value).hostname or "").lower().removeprefix("www.")
    # Known platform aliases: mobile/detail/ranking hosts are not independent sources.
    for canonical, aliases in {
        "jd.com": ("jd.com", "360buy.com"),
        "suning.com": ("suning.com",),
        "taobao.com": ("taobao.com", "tmall.com"),
        "smzdm.com": ("smzdm.com",),
        "sina.com.cn": ("sina.com.cn", "sina.cn"),
    }.items():
        if any(host == alias or host.endswith("." + alias) for alias in aliases):
            return canonical
    return host


def _worse(current: str, derived: str) -> str:
    return derived if STATUS_WEIGHT[derived] > STATUS_WEIGHT[current] else current


def _redact_text(value: str) -> str:
    value = PHONE_RE.sub("[REDACTED_PHONE]", value)
    value = ID_RE.sub("[REDACTED_ID]", value)
    value = CARD_RE.sub("[REDACTED_PAYMENT]", value)
    return EMAIL_RE.sub("[REDACTED_ACCOUNT]", value)


def redact_sensitive(value: Any, parent_key: str = "") -> Any:
    normalized = parent_key.casefold()
    if any(part.casefold() in normalized for part in SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(key): redact_sensitive(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item, parent_key) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _gate_statuses(record: dict[str, Any], errors: list[str]) -> dict[str, str]:
    gates = record.get("gates")
    effective: dict[str, str] = {}
    if not isinstance(gates, dict):
        errors.append("gates must be an object")
        return {name: "BLOCKED" for name in GATE_NAMES}
    for name in GATE_NAMES:
        item = gates.get(name)
        if not isinstance(item, dict):
            errors.append(f"gates.{name} must be an object")
            effective[name] = "BLOCKED"
            continue
        status = item.get("status")
        reason = item.get("reason")
        if status not in STATUSES:
            errors.append(f"gates.{name}.status must be PASS, FAIL, BLOCKED, or N/A")
            effective[name] = "BLOCKED"
        else:
            effective[name] = status
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"gates.{name}.reason must be non-empty")
    return effective


def _derive_requirement(record: dict[str, Any], errors: list[str]) -> str:
    request = record.get("request")
    required = (
        "summary",
        "use_case",
        "region",
        "delivery_deadline",
        "hard_constraints",
        "risk_tolerance",
        "budget_flexibility",
    )
    if not isinstance(request, dict):
        errors.append("request must be an object")
        return "BLOCKED"
    missing = [key for key in required if key not in request or request[key] in (None, "", [])]
    if missing:
        errors.append("request is missing required fields: " + ", ".join(missing))
        return "BLOCKED"
    return "PASS"


def _derive_web_coverage(record: dict[str, Any], errors: list[str]) -> str:
    queries = record.get("queries")
    if not isinstance(queries, list):
        errors.append("queries must be a list")
        return "BLOCKED"
    successful: dict[str, set[str]] = {role: set() for role in SOURCE_ROLES}
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            errors.append(f"queries[{index}] must be an object")
            continue
        role = query.get("role")
        platform = query.get("platform")
        status = query.get("status")
        query_text = query.get("query")
        if role not in SOURCE_ROLES:
            errors.append(f"queries[{index}].role is invalid")
            continue
        if not isinstance(platform, str) or not platform.strip():
            errors.append(f"queries[{index}].platform must be non-empty")
        if not isinstance(query_text, str) or not query_text.strip():
            errors.append(f"queries[{index}].query must be non-empty")
        if status == "success":
            if not _is_http_url(query.get("url")):
                errors.append(f"queries[{index}] success requires an http(s) url")
            else:
                successful[role].add(_domain(query["url"]))
        elif status == "failed":
            if not isinstance(query.get("failure_reason"), str) or not query["failure_reason"].strip():
                errors.append(f"queries[{index}] failed requires failure_reason")
        else:
            errors.append(f"queries[{index}].status must be success or failed")
    if len(successful["transaction"]) < 2:
        return "BLOCKED"
    independent = successful["independent_experience"] - successful["transaction"] - successful["official"]
    if not independent or not successful["complaint_rights"]:
        return "BLOCKED"
    if record.get("branded_or_modelled") is True and not successful["official"]:
        return "BLOCKED"
    return "PASS"


def _validate_complaint(complaint: Any, path: str, errors: list[str]) -> tuple[str, str] | None:
    if not isinstance(complaint, dict):
        errors.append(f"{path} must be an object")
        return None
    required = (
        "source_url",
        "date",
        "variant",
        "summary",
        "evidence",
        "seller_response",
        "resolved",
        "severity",
        "scope",
    )
    missing = [key for key in required if key not in complaint]
    if missing:
        errors.append(f"{path} missing fields: {', '.join(missing)}")
        return None
    if not _is_http_url(complaint.get("source_url")):
        errors.append(f"{path}.source_url must be http(s)")
        return None
    if complaint.get("severity") not in COMPLAINT_SEVERITIES:
        errors.append(f"{path}.severity is invalid")
        return None
    if complaint.get("scope") not in COMPLAINT_SCOPES:
        errors.append(f"{path}.scope must be product or seller")
        return None
    if not isinstance(complaint.get("summary"), str) or not complaint["summary"].strip():
        errors.append(f"{path}.summary must be non-empty")
        return None
    return complaint["severity"], _domain(complaint["source_url"])


def _coverage_status(coverage: Any, path: str, errors: list[str]) -> str:
    if not isinstance(coverage, dict):
        errors.append(f"{path} is required")
        return "BLOCKED"
    reported = coverage.get("reported_count")
    accessible = coverage.get("accessible_count")
    read = coverage.get("read_count")
    limitations = coverage.get("limitations")
    if not isinstance(accessible, int) or accessible < 0 or not isinstance(read, int) or read < 0:
        errors.append(f"{path} accessible_count and read_count must be non-negative integers")
        return "BLOCKED"
    if reported is not None and (not isinstance(reported, int) or reported < 0):
        errors.append(f"{path}.reported_count must be null or a non-negative integer")
        return "BLOCKED"
    if not isinstance(limitations, list):
        errors.append(f"{path}.limitations must be a list")
        return "BLOCKED"
    if read != accessible or limitations:
        return "BLOCKED"
    if isinstance(reported, int) and reported > accessible:
        return "BLOCKED"
    return "PASS"


def _review_access_status(coverage: dict[str, Any], path: str, errors: list[str]) -> str:
    mode = coverage.get("access_mode")
    result = coverage.get("access_result")
    attempts = coverage.get("access_attempts")
    status = "PASS"
    if mode not in REVIEW_ACCESS_MODES:
        errors.append(f"{path}.access_mode is invalid")
        status = "BLOCKED"
    if result not in REVIEW_ACCESS_RESULTS:
        errors.append(f"{path}.access_result is invalid")
        status = "BLOCKED"
    if not isinstance(attempts, list) or not attempts:
        errors.append(f"{path}.access_attempts must be a non-empty list")
        return "BLOCKED"

    attempt_pairs: set[tuple[str, str]] = set()
    public_blocked = False
    interactive_page_recorded = False
    challenge_recorded = False
    for index, attempt in enumerate(attempts):
        attempt_path = f"{path}.access_attempts[{index}]"
        if not isinstance(attempt, dict):
            errors.append(f"{attempt_path} must be an object")
            status = "BLOCKED"
            continue
        attempt_mode = attempt.get("mode")
        attempt_result = attempt.get("result")
        if attempt_mode not in REVIEW_ACCESS_MODES - {"none"}:
            errors.append(f"{attempt_path}.mode is invalid")
            status = "BLOCKED"
        if attempt_result not in REVIEW_ATTEMPT_RESULTS:
            errors.append(f"{attempt_path}.result is invalid")
            status = "BLOCKED"
        if not _is_http_url(attempt.get("source_url")):
            errors.append(f"{attempt_path}.source_url must be http(s)")
            status = "BLOCKED"
        if not isinstance(attempt.get("detail"), str) or not attempt["detail"].strip():
            errors.append(f"{attempt_path}.detail must be non-empty")
            status = "BLOCKED"
        forbidden_keys = {
            str(key) for key in attempt
            if any(part.casefold() in str(key).casefold() for part in SENSITIVE_KEY_PARTS)
        }
        if forbidden_keys:
            errors.append(f"{attempt_path} must not contain credentials or account data")
            status = "BLOCKED"
        if isinstance(attempt_mode, str) and isinstance(attempt_result, str):
            attempt_pairs.add((attempt_mode, attempt_result))
        if attempt_mode == "public_http" and attempt_result in {"blocked", "partial"}:
            public_blocked = True
        if attempt_mode in {"anonymous_browser", "official_client"}:
            interactive_page_recorded = True
        if (
            attempt_mode in {"authorized_session", "official_client"}
            and attempt_result != "not_authorized"
            and attempt.get("authorization") != "current_task"
        ):
            errors.append(f"{attempt_path}.authorization must equal current_task")
            status = "BLOCKED"
        challenge = attempt.get("challenge_detected", False)
        if not isinstance(challenge, bool):
            errors.append(f"{attempt_path}.challenge_detected must be boolean when present")
            status = "BLOCKED"
        elif challenge:
            challenge_recorded = True
            if attempt_result != "blocked":
                errors.append(f"{attempt_path} must be blocked after a CAPTCHA or account challenge")
                status = "BLOCKED"

    if public_blocked and not interactive_page_recorded:
        errors.append(f"{path} must record an official client or anonymous browser path after public access is blocked")
        status = "BLOCKED"
    if mode != "none" and not any(pair[0] == mode for pair in attempt_pairs):
        errors.append(f"{path}.access_mode must match a recorded attempt")
        status = "BLOCKED"
    if result == "complete" and (mode in {"none", "external_source"} or (mode, "complete") not in attempt_pairs):
        errors.append(f"{path} cannot claim complete access from the selected mode")
        status = "BLOCKED"
    if challenge_recorded and not coverage.get("limitations"):
        errors.append(f"{path}.limitations must record the CAPTCHA or account challenge")
        status = "BLOCKED"
    if result != "complete":
        status = "BLOCKED"
    return status


def _validate_reader_evidence(value: Any, path: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return False
    valid = True
    for key in ("backend", "command"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"{path}.{key} must be non-empty")
            valid = False
    if _parse_time(value.get("captured_at")) is None:
        errors.append(f"{path}.captured_at must be ISO 8601")
        valid = False
    if not _is_http_url(value.get("source_url")):
        errors.append(f"{path}.source_url must be http(s)")
        valid = False
    forbidden = {
        str(key) for key in value
        if any(part.casefold() in str(key).casefold() for part in SENSITIVE_KEY_PARTS)
    }
    if forbidden:
        errors.append(f"{path} must not contain credentials or account data")
        valid = False
    return valid


def _validate_negative_review(value: Any, path: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return False
    required = (
        "source_url",
        "date",
        "variant",
        "stars",
        "summary",
        "follow_up",
        "seller_response",
    )
    missing = [key for key in required if key not in value]
    if missing:
        errors.append(f"{path} missing fields: {', '.join(missing)}")
        return False
    valid = True
    if not _is_http_url(value.get("source_url")):
        errors.append(f"{path}.source_url must be http(s)")
        valid = False
    for key in ("date", "variant"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(f"{path}.{key} must be a non-empty string or 'unknown'")
            valid = False
    stars = value.get("stars")
    if stars is not None and (not isinstance(stars, int) or isinstance(stars, bool) or not 1 <= stars <= 5):
        errors.append(f"{path}.stars must be null or an integer from 1 to 5")
        valid = False
    if not isinstance(value.get("summary"), str) or not value["summary"].strip():
        errors.append(f"{path}.summary must be non-empty")
        valid = False
    for key in ("follow_up", "seller_response"):
        if value.get(key) is not None and not isinstance(value.get(key), str):
            errors.append(f"{path}.{key} must be null or a string")
            valid = False
    forbidden = {
        str(key) for key in value
        if any(part.casefold() in str(key).casefold() for part in REVIEW_PRIVATE_KEY_PARTS)
    }
    if forbidden:
        errors.append(f"{path} must not contain reviewer identity or credentials")
        valid = False
    return valid


def _review_reader_status(coverage: dict[str, Any], path: str, errors: list[str]) -> str:
    status = "PASS"
    reviews = coverage.get("negative_reviews")
    if not isinstance(reviews, list):
        errors.append(f"{path}.negative_reviews must be a list")
        reviews = []
        status = "BLOCKED"
    else:
        for index, review in enumerate(reviews):
            if not _validate_negative_review(review, f"{path}.negative_reviews[{index}]", errors):
                status = "BLOCKED"
    read_count = coverage.get("read_negative_count")
    if isinstance(read_count, int) and not isinstance(read_count, bool) and len(reviews) != read_count:
        errors.append(f"{path}.negative_reviews length must equal read_negative_count")
        status = "BLOCKED"

    if not _validate_reader_evidence(coverage.get("reader_evidence"), f"{path}.reader_evidence", errors):
        status = "BLOCKED"
    filter_verified = coverage.get("filter_verified")
    pages_read = coverage.get("pages_read")
    exhausted = coverage.get("exhausted")
    adapter_version = coverage.get("adapter_version")
    error_code = coverage.get("error_code")
    if not isinstance(filter_verified, bool):
        errors.append(f"{path}.filter_verified must be boolean")
        status = "BLOCKED"
    if not isinstance(pages_read, int) or isinstance(pages_read, bool) or pages_read < 0:
        errors.append(f"{path}.pages_read must be a non-negative integer")
        status = "BLOCKED"
    if not isinstance(exhausted, bool):
        errors.append(f"{path}.exhausted must be boolean")
        status = "BLOCKED"
    if not isinstance(adapter_version, str) or not adapter_version.strip():
        errors.append(f"{path}.adapter_version must be non-empty")
        status = "BLOCKED"
    if error_code is not None and (not isinstance(error_code, str) or not error_code.strip()):
        errors.append(f"{path}.error_code must be null or a non-empty string")
        status = "BLOCKED"

    complete = coverage.get("access_result") == "complete"
    if complete:
        if filter_verified is not True:
            errors.append(f"{path} complete access requires a verified negative-review filter")
            status = "BLOCKED"
        if not isinstance(pages_read, int) or isinstance(pages_read, bool) or pages_read < 1:
            errors.append(f"{path} complete access requires pages_read >= 1")
            status = "BLOCKED"
        if exhausted is not True:
            errors.append(f"{path} complete access requires exhausted=true")
            status = "BLOCKED"
        if error_code is not None:
            errors.append(f"{path} complete access requires error_code=null")
            status = "BLOCKED"
    else:
        if not isinstance(error_code, str) or not error_code.strip():
            errors.append(f"{path} incomplete access requires a non-empty error_code")
            status = "BLOCKED"
        status = "BLOCKED"
    return status


def _derive_candidate_gates(record: dict[str, Any], errors: list[str]) -> dict[str, str]:
    derived = {
        "identity": "PASS",
        "review_completeness": "PASS",
        "manipulation": "PASS",
        "complaint_severity": "PASS",
        "seller": "PASS",
    }
    candidates = record.get("candidates")
    if not isinstance(candidates, list):
        errors.append("candidates must be a list")
        return {name: "BLOCKED" for name in derived}
    finalists = [item for item in candidates if isinstance(item, dict) and item.get("finalist") is True]
    if not finalists:
        return {name: "BLOCKED" for name in derived}
    for index, candidate in enumerate(finalists):
        severe_domains: set[str] = set()
        path = f"candidates[{index}]"
        for key in ("id", "name"):
            if not isinstance(candidate.get(key), str) or not candidate[key].strip():
                errors.append(f"{path}.{key} must be non-empty")

        identity = candidate.get("identity")
        if not isinstance(identity, dict) or identity.get("verdict") not in IDENTITY:
            errors.append(f"{path}.identity.verdict is required")
            derived["identity"] = _worse(derived["identity"], "BLOCKED")
        else:
            verdict = identity["verdict"]
            evidence = identity.get("evidence")
            if verdict in {"matched", "unique"} and (not isinstance(evidence, list) or not evidence):
                derived["identity"] = _worse(derived["identity"], "BLOCKED")
            elif verdict == "conflict":
                derived["identity"] = "FAIL"
            elif verdict == "unknown":
                derived["identity"] = _worse(derived["identity"], "BLOCKED")

        coverage = candidate.get("review_coverage")
        if not isinstance(coverage, dict):
            errors.append(f"{path}.review_coverage is required")
            derived["review_completeness"] = _worse(derived["review_completeness"], "BLOCKED")
        else:
            accessible = coverage.get("accessible_negative_count")
            read = coverage.get("read_negative_count")
            reported = coverage.get("reported_negative_count")
            limitations = coverage.get("limitations")
            if not isinstance(accessible, int) or accessible < 0 or not isinstance(read, int) or read < 0:
                errors.append(f"{path}.review_coverage counts must be non-negative integers")
                derived["review_completeness"] = _worse(derived["review_completeness"], "BLOCKED")
            elif read != accessible:
                derived["review_completeness"] = _worse(derived["review_completeness"], "BLOCKED")
            if reported is not None and (not isinstance(reported, int) or reported < 0):
                errors.append(f"{path}.reported_negative_count must be null or a non-negative integer")
            elif isinstance(reported, int) and isinstance(accessible, int) and reported > accessible:
                derived["review_completeness"] = _worse(derived["review_completeness"], "BLOCKED")
            if not isinstance(limitations, list):
                errors.append(f"{path}.review_coverage.limitations must be a list")
                derived["review_completeness"] = _worse(derived["review_completeness"], "BLOCKED")
            elif limitations:
                derived["review_completeness"] = _worse(derived["review_completeness"], "BLOCKED")
            if record.get("schema_version") in {2, 3}:
                derived["review_completeness"] = _worse(
                    derived["review_completeness"],
                    _review_access_status(coverage, f"{path}.review_coverage", errors),
                )
            if record.get("schema_version") == 3:
                derived["review_completeness"] = _worse(
                    derived["review_completeness"],
                    _review_reader_status(coverage, f"{path}.review_coverage", errors),
                )
            source_urls = coverage.get("source_urls")
            review_domains = {
                _domain(url) for url in source_urls if _is_http_url(url)
            } if isinstance(source_urls, list) else set()
            if len(review_domains) < 2:
                derived["review_completeness"] = _worse(
                    derived["review_completeness"], "BLOCKED"
                )

        authenticity = candidate.get("authenticity")
        if not isinstance(authenticity, dict) or authenticity.get("verdict") not in AUTHENTICITY:
            errors.append(f"{path}.authenticity.verdict is required")
            derived["manipulation"] = _worse(derived["manipulation"], "BLOCKED")
        else:
            verdict = authenticity["verdict"]
            evidence = authenticity.get("evidence")
            if verdict == "manipulated":
                derived["manipulation"] = "FAIL"
            elif verdict in {"mixed", "unknown"}:
                derived["manipulation"] = _worse(derived["manipulation"], "BLOCKED")
            elif not isinstance(evidence, list) or not evidence:
                derived["manipulation"] = _worse(derived["manipulation"], "BLOCKED")

        seller = candidate.get("seller_risk")
        if not isinstance(seller, dict) or seller.get("verdict") not in SELLER_RISK:
            errors.append(f"{path}.seller_risk.verdict is required")
            derived["seller"] = _worse(derived["seller"], "BLOCKED")
        else:
            verdict = seller["verdict"]
            evidence = seller.get("evidence")
            if verdict == "high":
                derived["seller"] = "FAIL"
            elif verdict in {"mixed", "unknown"}:
                derived["seller"] = _worse(derived["seller"], "BLOCKED")
            elif not isinstance(evidence, list) or not evidence:
                derived["seller"] = _worse(derived["seller"], "BLOCKED")
            source_urls = seller.get("source_urls")
            if not isinstance(source_urls, list) or not any(_is_http_url(url) for url in source_urls):
                derived["seller"] = _worse(derived["seller"], "BLOCKED")

        complaints = candidate.get("complaints")
        product_complaint_count = 0
        seller_complaint_count = 0
        if not isinstance(complaints, list):
            errors.append(f"{path}.complaints must be a list")
            derived["complaint_severity"] = _worse(derived["complaint_severity"], "BLOCKED")
        else:
            for complaint_index, complaint in enumerate(complaints):
                item = _validate_complaint(
                    complaint, f"{path}.complaints[{complaint_index}]", errors
                )
                if item is None:
                    derived["complaint_severity"] = _worse(
                        derived["complaint_severity"], "BLOCKED"
                    )
                elif item[0] == "safety_or_fraud":
                    severe_domains.add(item[1])
                if isinstance(complaint, dict) and complaint.get("scope") == "product":
                    product_complaint_count += 1
                if isinstance(complaint, dict) and complaint.get("scope") == "seller":
                    seller_complaint_count += 1
        product_coverage = candidate.get("complaint_coverage")
        product_status = _coverage_status(
            product_coverage, f"{path}.complaint_coverage", errors
        )
        if product_status != "PASS":
            derived["complaint_severity"] = _worse(
                derived["complaint_severity"], "BLOCKED"
            )
        elif product_complaint_count != product_coverage.get("read_count"):
            derived["complaint_severity"] = _worse(
                derived["complaint_severity"], "BLOCKED"
            )
        if isinstance(seller, dict):
            seller_coverage = seller.get("complaint_coverage")
            seller_status = _coverage_status(
                seller_coverage, f"{path}.seller_risk.complaint_coverage", errors
            )
            if seller_status != "PASS":
                derived["seller"] = _worse(derived["seller"], "BLOCKED")
            elif seller_complaint_count != seller_coverage.get("read_count"):
                derived["seller"] = _worse(derived["seller"], "BLOCKED")
        if len(severe_domains) >= 2:
            derived["complaint_severity"] = "FAIL"
    return derived


def _derive_price_value(record: dict[str, Any], errors: list[str]) -> str:
    analysis = record.get("price_analysis")
    if not isinstance(analysis, dict):
        errors.append("price_analysis must be an object")
        return "BLOCKED"
    bands = analysis.get("bands")
    if not isinstance(bands, list):
        errors.append("price_analysis.bands must be a list")
        return "BLOCKED"
    if len(bands) < 3:
        reason = analysis.get("fewer_bands_reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("fewer_bands_reason is required when comparing fewer than three bands")
            return "BLOCKED"
    if not bands:
        return "BLOCKED"
    for index, band in enumerate(bands):
        if not isinstance(band, dict):
            errors.append(f"price_analysis.bands[{index}] must be an object")
            return "BLOCKED"
        if not isinstance(band.get("audited_candidate_ids"), list) or not band["audited_candidate_ids"]:
            return "BLOCKED"
        if not isinstance(band.get("evidence_summary"), str) or not band["evidence_summary"].strip():
            return "BLOCKED"
    conclusion = analysis.get("conclusion")
    if not isinstance(conclusion, str) or not conclusion.strip():
        return "BLOCKED"
    return "PASS"


def _derive_freshness(record: dict[str, Any], errors: list[str], required: set[str] | None = None) -> str:
    facts = record.get("dynamic_facts")
    if not isinstance(facts, list):
        errors.append("dynamic_facts must be a list")
        return "BLOCKED"
    all_kinds = {"price", "inventory", "shipping", "store_status"}
    required = all_kinds if required is None else required
    candidates = record.get("candidates")
    if not isinstance(candidates, list):
        return "BLOCKED"
    finalist_ids = {c.get("id") for c in candidates
                    if isinstance(c, dict) and c.get("finalist") is True and isinstance(c.get("id"), str)}
    seen: dict[str, set[str]] = {cid: set() for cid in finalist_ids}
    blocked = False
    now = datetime.now(timezone.utc)
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            errors.append(f"dynamic_facts[{index}] must be an object")
            continue
        kind = fact.get("kind")
        status = fact.get("status")
        if kind not in all_kinds:
            errors.append(f"dynamic_facts[{index}].kind is invalid")
            continue
        if kind not in required:
            continue
        candidate_id = fact.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in finalist_ids:
            continue
        if status in {"unknown", "historical", "restricted"}:
            blocked = True
            continue
        if status == "verified":
            checked = _parse_time(fact.get("verified_at"))
            if checked is None:
                errors.append(f"dynamic_facts[{index}].verified_at is invalid")
                return "BLOCKED"
            age = (now - checked).total_seconds()
            if age < -300 or age > 72 * 3600 or not _is_http_url(fact.get("source_url")):
                blocked = True
                continue
            if kind == "price":
                amount = fact.get("amount")
                if (isinstance(amount, bool) or not isinstance(amount, (int, float))
                        or not math.isfinite(amount) or amount <= 0 or not fact.get("currency")
                        or fact.get("price_type") not in {"listed", "checkout", "member", "coupon"}
                        or fact.get("current_offer") is not True):
                    blocked = True
                    continue
            elif fact.get("value") in (None, ""):
                blocked = True
                continue
        elif status == "n_a":
            if not isinstance(fact.get("reason"), str) or not fact["reason"].strip():
                errors.append(f"dynamic_facts[{index}] n_a requires reason")
                return "BLOCKED"
        else:
            errors.append(f"dynamic_facts[{index}].status must be verified, n_a, unknown, historical, or restricted")
            return "BLOCKED"
        seen[candidate_id].add(kind)
    return "PASS" if seen and not blocked and all(kinds == required for kinds in seen.values()) else "BLOCKED"


def _conditional_eligibility(record: dict[str, Any], effective: dict[str, str]) -> tuple[bool, list[str]]:
    """Conditional advice has its own evidence requirements, never an automatic waiver."""
    reasons: list[str] = []
    recommendation = record.get("recommendation", {})
    if not isinstance(recommendation, dict):
        return False, ["recommendation is missing"]
    ids = recommendation.get("candidate_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(cid, str) for cid in ids):
        return False, ["candidate_ids must select concrete candidates"]
    if len(set(ids)) != len(ids):
        reasons.append("candidate_ids must be unique")
    if any(effective.get(gate) == "FAIL" for gate in PRE_PUBLICATION_GATES):
        reasons.append("a critical gate failed; exclude failed candidates and reassess")
    if effective.get("requirement") != "PASS" or effective.get("identity") != "PASS":
        reasons.append("requirements and identity must pass")
    request = record.get("request", {})
    if isinstance(request, dict) and request.get("require_full_review_before_recommendation") is True:
        if effective.get("review_completeness") != "PASS":
            reasons.append("user requires complete review audit before any recommendation")
    conditions = recommendation.get("conditions")
    if not isinstance(conditions, list) or not conditions or any(not isinstance(x, str) or not x.strip() for x in conditions):
        reasons.append("concrete purchase conditions are required")
    candidate_list = record.get("candidates")
    if not isinstance(candidate_list, list):
        return False, reasons + ["candidates must be a list"]
    candidates = {c.get("id"): c for c in candidate_list
                  if isinstance(c, dict) and isinstance(c.get("id"), str)}
    for cid in ids:
        candidate = candidates.get(cid)
        if not candidate or candidate.get("finalist") is not True:
            reasons.append(f"{cid}: selected candidate must be a finalist")
            continue
        basis = candidate.get("conditional_basis")
        if not isinstance(basis, str) or not basis.strip():
            reasons.append(f"{cid}: conditional_basis must explain evidence and residual risks")
        checks = candidate.get("purchase_checks", {})
        if isinstance(checks, dict) and any(isinstance(c, dict) and c.get("status") in {"FAIL", "BLOCKED"} for c in checks.values()):
            reasons.append(f"{cid}: a substantive purchase check failed or remains unverified")
        for key in ("variant", "seller_and_returns", "hard_constraints", "safety"):
            check = checks.get(key, {}) if isinstance(checks, dict) else {}
            if not isinstance(check, dict):
                check = {}
            status = check.get("status")
            reason = check.get("reason")
            urls = check.get("source_urls", [])
            if not isinstance(reason, str) or not reason.strip():
                reasons.append(f"{cid}.{key}: missing explanation")
            if key == "safety" and status == "N/A":
                if record.get("category") in {"food_health_beauty", "child_pet"}:
                    reasons.append(f"{cid}: safety evidence cannot be waived for this category")
                continue
            if status != "PASS" or not isinstance(urls, list) or not any(_is_http_url(url) for url in urls):
                reasons.append(f"{cid}.{key}: requires PASS with source evidence")
        for field, accessible_key, read_key in (
            ("review_coverage", "accessible_negative_count", "read_negative_count"),
            ("complaint_coverage", "accessible_count", "read_count"),
        ):
            coverage = candidate.get(field, {})
            if isinstance(coverage, dict) and coverage.get(accessible_key) != coverage.get(read_key):
                reasons.append(f"{cid}: accessible negative evidence remains unread")
        seller = candidate.get("seller_risk", {})
        coverage = seller.get("complaint_coverage", {}) if isinstance(seller, dict) else {}
        if isinstance(coverage, dict) and coverage.get("accessible_count") != coverage.get("read_count"):
            reasons.append(f"{cid}: accessible seller complaints remain unread")
        complaints = candidate.get("complaints", [])
        for scope, coverage in (("product", candidate.get("complaint_coverage", {})), ("seller", coverage)):
            logged = sum(isinstance(c, dict) and c.get("scope") == scope for c in complaints) if isinstance(complaints, list) else -1
            if isinstance(coverage, dict) and coverage.get("read_count") != logged:
                reasons.append(f"{cid}: read {scope} complaints must have matching records")
    selected_record = dict(record, candidates=[c for cid, c in candidates.items() if cid in ids])
    freshness_errors: list[str] = []
    if _derive_freshness(selected_record, freshness_errors, {"inventory", "shipping", "store_status"}) != "PASS":
        reasons.append("selected candidates need current inventory, shipping and store evidence")
    reasons.extend(freshness_errors)
    return not reasons, reasons


def validate_record(record: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return {
            "valid": False,
            "errors": ["record must be a JSON object"],
            "warnings": [],
            "effective_gates": {name: "BLOCKED" for name in GATE_NAMES},
            "publication": "BLOCKED",
        }
    if record.get("schema_version") not in {1, 2, 3}:
        errors.append("schema_version must equal 1, 2, or 3")
    session_id = record.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        errors.append("session_id must be non-empty")
    if _parse_time(record.get("researched_at")) is None:
        errors.append("researched_at must be ISO 8601")
    if not isinstance(record.get("category"), str) or not record["category"].strip():
        errors.append("category must be non-empty")

    effective = _gate_statuses(record, errors)
    derived = {
        "requirement": _derive_requirement(record, errors),
        "web_coverage": _derive_web_coverage(record, errors),
        "price_value": _derive_price_value(record, errors),
    }
    derived.update(_derive_candidate_gates(record, errors))
    freshness = _derive_freshness(record, errors)
    if freshness != "PASS":
        warnings.append("current per-candidate price, inventory, shipping, or store evidence is missing, restricted, unbound, or stale")
        derived["price_value"] = _worse(derived["price_value"], "BLOCKED")
    for name, status in derived.items():
        if effective.get(name) == "N/A":
            effective[name] = status
        else:
            effective[name] = _worse(effective.get(name, "BLOCKED"), status)

    pre_statuses = [effective[name] for name in PRE_PUBLICATION_GATES]
    if "FAIL" in pre_statuses:
        publication = "FAIL"
    elif "BLOCKED" in pre_statuses:
        publication = "BLOCKED"
    else:
        publication = "PASS"
    declared_publication = effective.get("publication", "BLOCKED")
    publication = _worse(declared_publication, publication)
    effective["publication"] = publication

    conditional_eligible, conditional_reasons = _conditional_eligibility(record, effective)
    recommendation = record.get("recommendation")
    if not isinstance(recommendation, dict):
        errors.append("recommendation must be an object")
    else:
        mode = recommendation.get("mode")
        if mode not in {"strong", "conditional", "none"}:
            errors.append("recommendation.mode must be strong, conditional, or none")
        elif mode == "strong" and publication != "PASS":
            errors.append("strong recommendation is forbidden unless publication gate is PASS")
        elif mode == "conditional" and not conditional_eligible:
            errors.extend("conditional: " + reason for reason in conditional_reasons)
        if mode == "strong":
            ids = recommendation.get("candidate_ids")
            candidate_list = record.get("candidates", [])
            finalists = {c.get("id") for c in candidate_list if isinstance(c, dict) and c.get("finalist") is True and isinstance(c.get("id"), str)} if isinstance(candidate_list, list) else set()
            if not isinstance(ids, list) or not ids or any(not isinstance(cid, str) or cid not in finalists for cid in ids):
                errors.append("strong recommendation must select existing finalists")
            # Strong advice must also meet the substantive purchase requirements.
            strong_record = copy.deepcopy(record)
            strong_record["recommendation"]["conditions"] = ["all applicable purchase checks verified"]
            strong_ok, strong_reasons = _conditional_eligibility(strong_record, effective)
            if not strong_ok:
                errors.extend("purchase: " + reason for reason in strong_reasons)

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "effective_gates": effective,
        "publication": publication,
        "conditional_eligible": conditional_eligible and not errors,
        "conditional_reasons": conditional_reasons,
    }


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _state_path(path: Path) -> Path:
    lexical = Path(os.path.abspath(os.path.expanduser(str(path))))
    cursor = lexical
    while True:
        if (cursor.exists() or cursor.is_symlink()) and _is_link_or_reparse(cursor):
            raise ValueError("symlinked or reparse-point state paths are not allowed")
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    resolved = lexical.resolve(strict=False)
    if any(resolved == root or root in resolved.parents for root in FORBIDDEN_STATE_ROOTS):
        raise ValueError("state directory must stay outside the skill, shared skills, and repository/configuration roots")
    return resolved


def _owned_store(path: Path, *, create: bool) -> Path:
    resolved = _state_path(path)
    if not resolved.exists():
        if not create:
            return resolved
        resolved.mkdir(parents=True, exist_ok=False)
    if not resolved.is_dir():
        raise ValueError("state root must be a directory")

    marker_path = resolved / STATE_MARKER
    if (marker_path.exists() or marker_path.is_symlink()) and _is_link_or_reparse(marker_path):
        raise ValueError("state ownership marker must not be a symlink or reparse point")
    if not marker_path.exists():
        entries = list(resolved.iterdir())
        if not create or entries:
            raise ValueError("state root is not an owned shopping-research directory")
        marker = {
            "schema": "shopping-research-state-v1",
            "owner": "shopping-research-assistant",
            "directory": str(resolved),
        }
        with marker_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(marker) + "\n")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker_directory = Path(str(marker.get("directory", ""))).resolve(strict=False)
    if (
        marker.get("schema") != "shopping-research-state-v1"
        or marker.get("owner") != "shopping-research-assistant"
        or marker_directory != resolved
    ):
        raise ValueError("state root ownership marker does not match")
    return resolved


def _ensure_store(path: Path) -> Path:
    return _owned_store(path, create=True)


def _configured_store(value: str | None) -> Path | None:
    configured = value or os.environ.get(STATE_ENV)
    if not configured or not configured.strip():
        return None
    return Path(configured)


@contextmanager
def _write_lock(store: Path, timeout_seconds: float = 10.0) -> Iterable[None]:
    lock_path = store / "write.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for state write lock")
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                yield item


def _append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical(item) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_unique(path: Path, item: dict[str, Any], hash_field: str = "_event_hash") -> bool:
    payload = copy.deepcopy(item)
    payload.pop(hash_field, None)
    event_hash = _hash(payload)
    for existing in _iter_jsonl(path):
        if existing.get(hash_field) == event_hash:
            return False
    payload[hash_field] = event_hash
    _append_jsonl(path, payload)
    return True


def _valid_preference(event: Any) -> tuple[bool, str]:
    if not isinstance(event, dict):
        return False, "preference event is not an object"
    key = event.get("key")
    if not isinstance(key, str) or not key.strip():
        return False, "preference event key is missing"
    normalized = key.casefold()
    if any(part.casefold() in normalized for part in SENSITIVE_KEY_PARTS):
        return False, f"sensitive preference key rejected: {key}"
    if event.get("evidence_type") not in PREFERENCE_TYPES:
        return False, f"invalid preference evidence_type: {key}"
    if "value" not in event:
        return False, f"preference value is missing: {key}"
    return True, ""


def _finding_layer(finding: Any, validation: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(finding, dict):
        return "raw", "finding is not an object"
    if not validation["valid"]:
        return "raw", "record failed structural validation"
    gates = validation["effective_gates"]
    if any(gates.get(name) != "PASS" for name in TRUST_GATES):
        return "raw", "trust gates did not all pass"
    platforms = finding.get("source_platforms")
    urls = finding.get("source_urls")
    if not isinstance(platforms, list) or len({str(x).casefold() for x in platforms}) < 2:
        return "raw", "fewer than two source platforms"
    if not isinstance(urls, list) or len({url for url in urls if _is_http_url(url)}) < 2:
        return "raw", "fewer than two valid source URLs"
    if finding.get("confidence") != "high":
        return "raw", "confidence is not high"
    evidence_dates = finding.get("evidence_dates")
    if not isinstance(evidence_dates, list) or not evidence_dates:
        return "raw", "evidence dates are missing"
    if any(_parse_time(value) is None for value in evidence_dates):
        return "raw", "an evidence date is invalid"
    if not isinstance(finding.get("statement"), str) or not finding["statement"].strip():
        return "raw", "statement is missing"
    return "trusted", "promotion gates passed"


def commit_record(record: dict[str, Any], store_path: Path) -> dict[str, Any]:
    store = _ensure_store(store_path)
    clean = redact_sensitive(record)
    validation = validate_record(clean)
    session_id = clean.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = "invalid-" + _hash(clean)[:16]
    receipt: dict[str, Any] = {
        "session_id": session_id,
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "raw_written": False,
        "raw_duplicate": False,
        "preferences_written": 0,
        "preferences_rejected": [],
        "findings_written": 0,
        "trusted_findings": 0,
        "raw_findings": 0,
        "finding_notes": [],
        "pending_category_written": False,
        "publication": validation["publication"],
        "conditional_eligible": validation.get("conditional_eligible", False),
        "conditional_reasons": validation.get("conditional_reasons", []),
        "effective_gates": validation["effective_gates"],
        "validation_errors": validation["errors"],
        "warnings": validation["warnings"],
    }
    raw_path = store / "raw-research.jsonl"
    preference_path = store / "preference-events.jsonl"
    finding_path = store / "category-findings.jsonl"
    pending_path = store / "pending-categories.jsonl"
    receipt_path = store / "receipts.jsonl"

    with _write_lock(store):
        content_hash = _hash(clean)
        receipt["record_hash"] = content_hash
        existing_records = [item for item in _iter_jsonl(raw_path) if item.get("session_id") == session_id]
        duplicate_revision = next((index for index, item in enumerate(existing_records, 1)
            if (item.get("_record_hash") or _hash({k: v for k, v in item.items() if not k.startswith("_")})) == content_hash), None)
        duplicate = duplicate_revision is not None
        receipt["revision"] = duplicate_revision if duplicate else len(existing_records) + 1
        if duplicate:
            receipt["raw_duplicate"] = True
        else:
            raw_entry = copy.deepcopy(clean)
            raw_entry["session_id"] = session_id
            raw_entry["_validation"] = validation
            raw_entry["_record_hash"] = content_hash
            raw_entry["_revision"] = receipt["revision"]
            _append_jsonl(raw_path, raw_entry)
            receipt["raw_written"] = True

        for event in clean.get("preference_events", []) if isinstance(clean.get("preference_events"), list) else []:
            okay, reason = _valid_preference(event)
            if not okay:
                receipt["preferences_rejected"].append(reason)
                continue
            preference = copy.deepcopy(event)
            preference["source_session"] = session_id
            preference.setdefault("observed_at", clean.get("researched_at"))
            if _append_unique(preference_path, preference):
                receipt["preferences_written"] += 1

        for finding in clean.get("category_findings", []) if isinstance(clean.get("category_findings"), list) else []:
            layer, note = _finding_layer(finding, validation)
            payload = copy.deepcopy(finding) if isinstance(finding, dict) else {"value": finding}
            payload.setdefault("category", clean.get("category"))
            payload["layer"] = layer
            payload["source_session"] = session_id
            payload["promotion_reason"] = note
            if _append_unique(finding_path, payload):
                receipt["findings_written"] += 1
                receipt[f"{layer}_findings"] += 1
            receipt["finding_notes"].append(note)

        pending = clean.get("pending_category")
        if pending:
            payload = {
                "category": clean.get("category"),
                "details": pending,
                "source_session": session_id,
                "observed_at": clean.get("researched_at"),
            }
            receipt["pending_category_written"] = _append_unique(pending_path, payload)

        _append_jsonl(receipt_path, redact_sensitive(receipt))
    return receipt


def load_context(category: str, store_path: Path) -> dict[str, Any]:
    # Read-only context must not create a directory or require a writable lock.
    store = _owned_store(store_path, create=False)
    preference_path = store / "preference-events.jsonl"
    finding_path = store / "category-findings.jsonl"
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in _iter_jsonl(preference_path):
        key = event.get("key")
        evidence_type = event.get("evidence_type")
        if isinstance(key, str) and evidence_type in PREFERENCE_PRIORITY:
            grouped.setdefault(key, []).append(event)
    trusted = [
        finding
        for finding in _iter_jsonl(finding_path)
        if finding.get("layer") == "trusted" and finding.get("category") == category
    ]

    profile: dict[str, Any] = {}
    for key, events in grouped.items():
        highest = max(PREFERENCE_PRIORITY[event["evidence_type"]] for event in events)
        top = [event for event in events if PREFERENCE_PRIORITY[event["evidence_type"]] == highest]
        values: dict[str, Any] = {}
        for event in top:
            values[_canonical(event.get("value"))] = event.get("value")
        profile[key] = {
            "status": "conflict" if len(values) > 1 else "resolved",
            "values": list(values.values()),
            "evidence_type": top[0]["evidence_type"],
            "source_sessions": sorted({str(event.get("source_session")) for event in top}),
        }
    return {
        "persistence_enabled": True,
        "category": category,
        "preference_profile": profile,
        "trusted_findings": trusted,
        "notice": "Historical context is search guidance only; re-check current price, inventory, shipping, seller, and product state.",
    }


def _load_record(args: argparse.Namespace) -> dict[str, Any]:
    if args.stdin:
        raw = sys.stdin.read()
    else:
        raw = Path(args.input).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("record must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "commit"):
        command = subparsers.add_parser(name)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--input", help="UTF-8 JSON record")
        source.add_argument("--stdin", action="store_true", help="read JSON from stdin")
        if name == "commit":
            command.add_argument(
                "--state-root",
                help=f"owned state directory outside the shared skills tree; or set {STATE_ENV}",
            )
    context = subparsers.add_parser("context")
    context.add_argument("--category", required=True)
    context.add_argument(
        "--state-root",
        help=f"owned state directory outside the shared skills tree; or set {STATE_ENV}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "context":
            store = _configured_store(args.state_root)
            if store is None:
                print(json.dumps({
                    "persistence_enabled": False,
                    "category": args.category,
                    "preference_profile": {},
                    "trusted_findings": [],
                    "notice": "Long-term shopping memory is disabled until the user explicitly configures an external state root.",
                }, ensure_ascii=False, indent=2))
                return 0
            result = load_context(args.category, store)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        record = _load_record(args)
        if args.command == "validate":
            result = validate_record(redact_sensitive(record))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["valid"] else 2
        store = _configured_store(args.state_root)
        if store is None:
            raise ValueError(
                f"long-term persistence is disabled; after explicit opt-in, pass --state-root or set {STATE_ENV}"
            )
        receipt = commit_record(record, store)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0 if not receipt["validation_errors"] else 2
    except (OSError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
