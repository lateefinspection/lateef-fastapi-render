#!/usr/bin/env python3
"""
HomeFax Intake Standard Mapper v1

Purpose:
Convert current HomeFax standard preview output into the official
HomeFaxIntakeStandardV1 payload shape.

Input:
  JSON file from:
  GET /records/{record_id}/homefax-standard-report-preview-clean-v4?limit=100

Output:
  schemas/homefax_intake_standard_v1.generated.json

This script does not write to the database.
This script does not call FastAPI.
This script only maps existing JSON into the intake standard contract.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_TENANT_ID = "lateef-home-inspection"
DEFAULT_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def pick_first(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return default

    text = str(value).strip().lower()

    if text in {"1", "true", "yes", "y", "on"}:
        return True

    if text in {"0", "false", "no", "n", "off"}:
        return False

    return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def normalize_decision(issue: Dict[str, Any]) -> str:
    raw = pick_first(
        issue.get("homeowner_decision"),
        issue.get("decision"),
        issue.get("current_status"),
        issue.get("status"),
        "unreviewed",
    ).lower()

    aliases = {
        "": "unreviewed",
        "open": "unreviewed",
        "none": "unreviewed",
        "null": "unreviewed",
        "monitoring": "monitor",
        "needs_repair": "repair_needed",
        "repair": "repair_needed",
        "repaired": "already_repaired",
        "dismissed": "not_an_issue",
        "image_mismatch": "wrong_photo",
        "image_review_needed": "wrong_photo",
        "needs_image_review": "wrong_photo",
    }

    decision = aliases.get(raw, raw)

    allowed = {
        "unreviewed",
        "monitor",
        "repair_needed",
        "needs_contractor",
        "wrong_photo",
        "already_repaired",
        "not_an_issue",
    }

    return decision if decision in allowed else "unreviewed"


def normalize_image_match_status(issue: Dict[str, Any]) -> str:
    raw = pick_first(
        issue.get("image_match_status"),
        issue.get("evidence_image_match_status"),
        "suggested",
    ).lower()

    aliases = {
        "image_mismatch": "mismatch",
        "wrong_photo": "mismatch",
        "needs_review": "image_review_needed",
        "needs_image_review": "image_review_needed",
        "": "none",
    }

    status = aliases.get(raw, raw)

    allowed = {
        "none",
        "suggested",
        "verified",
        "mismatch",
        "image_review_needed",
    }

    return status if status in allowed else "suggested"


def normalize_pipeline_stage(preview_payload: Dict[str, Any]) -> str:
    if preview_payload.get("success") is True:
        return "review_ready"

    return "failed"


def normalize_severity(issue: Dict[str, Any]) -> str:
    raw = pick_first(
        issue.get("standard_severity"),
        issue.get("severity"),
        "unknown",
    ).lower()

    aliases = {
        "high": "major",
        "medium": "moderate",
        "low": "minor",
        "info": "maintenance",
    }

    severity = aliases.get(raw, raw)

    allowed = {
        "critical",
        "major",
        "moderate",
        "minor",
        "maintenance",
        "safety",
        "unknown",
    }

    if "safety" in severity or "hazard" in severity:
        return "safety"

    return severity if severity in allowed else "unknown"


def list_from_possible_json(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if str(item).strip()]
        except Exception:
            pass

        return [text]

    return []


def map_issue_to_standard_finding(issue: Dict[str, Any], index: int, now: str) -> Dict[str, Any]:
    source_item_number = pick_first(
        issue.get("source_item_number"),
        issue.get("item_number"),
        issue.get("source_number"),
        issue.get("item"),
        str(index + 1),
    )

    title = pick_first(
        issue.get("source_finding_title"),
        issue.get("title"),
        issue.get("standard_plain_summary"),
        "Untitled finding",
    )

    candidate_urls = list_from_possible_json(
        issue.get("candidate_image_urls")
        or issue.get("candidate_images")
        or issue.get("image_candidates")
    )

    primary_image_url = pick_first(
        issue.get("primary_image_url"),
        issue.get("image_url"),
        issue.get("suggested_image_url"),
        issue.get("verified_image_url"),
    )

    verified_image_url = pick_first(issue.get("verified_image_url"))

    candidate_count = as_int(
        issue.get("candidate_image_count"),
        len(candidate_urls),
    )

    if candidate_count == 0 and candidate_urls:
        candidate_count = len(candidate_urls)

    homeowner_decision = normalize_decision(issue)
    image_match_status = normalize_image_match_status(issue)

    baseline_locked = as_bool(issue.get("baseline_locked"), False)

    return {
        "finding_id": pick_first(issue.get("id"), source_item_number),
        "source": {
            "source_item_number": source_item_number,
            "source_report_section": pick_first(issue.get("source_report_section"), issue.get("section")),
            "source_finding_title": title,
            "source_finding_text": pick_first(issue.get("source_finding_text"), issue.get("summary")),
            "source_recommendation": pick_first(issue.get("source_recommendation"), issue.get("recommendation")),
            "source_page": issue.get("source_page") if issue.get("source_page") not in ("", None) else None,
            "source_pdf_url": pick_first(issue.get("source_pdf_url")),
            "source_pdf_page_url": pick_first(issue.get("source_pdf_page_url")),
        },
        "homefax": {
            "standard_schema_version": pick_first(
                issue.get("homefax_standard_schema_version"),
                issue.get("standard_schema_version"),
                DEFAULT_VERSION,
            ),
            "category": pick_first(issue.get("standard_category"), issue.get("category")),
            "system": pick_first(issue.get("standard_system"), issue.get("system")),
            "component": pick_first(issue.get("standard_component"), issue.get("component")),
            "defect_type": pick_first(issue.get("standard_defect_type"), issue.get("defect_type")),
            "location_area": pick_first(
                issue.get("standard_location_area"),
                issue.get("location"),
                issue.get("area"),
                issue.get("room"),
            ),
            "severity": normalize_severity(issue),
            "risk_reasons": list_from_possible_json(issue.get("standard_risk_reasons") or issue.get("risk_reasons")),
            "plain_summary": pick_first(issue.get("standard_plain_summary"), issue.get("summary")),
            "recommended_trade": pick_first(issue.get("standard_recommended_trade"), issue.get("recommended_trade")),
            "recommended_action": pick_first(issue.get("standard_recommended_action"), issue.get("recommended_action")),
            "monitoring_plan": pick_first(issue.get("standard_monitoring_plan"), issue.get("monitoring_plan")),
        },
        "evidence": {
            "image_url": pick_first(issue.get("image_url"), primary_image_url),
            "primary_image_url": primary_image_url,
            "verified_image_url": verified_image_url,
            "candidate_image_urls": candidate_urls,
            "candidate_image_count": candidate_count,
            "image_match_status": image_match_status,
            "image_match_confidence": pick_first(issue.get("image_match_confidence")),
            "needs_image_review": as_bool(
                issue.get("needs_image_review"),
                default=(image_match_status != "verified"),
            ),
        },
        "review_state": {
            "homeowner_decision": homeowner_decision,
            "homeowner_note": pick_first(issue.get("homeowner_note")),
            "homeowner_reviewed_at": issue.get("homeowner_reviewed_at") or None,
            "current_status": pick_first(issue.get("current_status"), issue.get("status"), "open"),
            "hidden_from_review_queue": as_bool(issue.get("hidden_from_review_queue"), False),
        },
        "admin_state": {
            "admin_review_status": pick_first(issue.get("admin_review_status"), "pending"),
            "admin_image_decision": pick_first(issue.get("admin_image_decision"), "pending"),
            "admin_note": pick_first(issue.get("admin_note")),
            "admin_reviewed_at": issue.get("admin_reviewed_at") or None,
            "final_approval_status": pick_first(issue.get("final_approval_status"), "not_approved"),
            "final_approved_by": pick_first(issue.get("final_approved_by")),
            "final_approved_at": issue.get("final_approved_at") or None,
            "baseline_locked": baseline_locked,
            "baseline_locked_at": issue.get("baseline_locked_at") or None,
        },
        "monitoring": {
            "monitoring_enabled": homeowner_decision == "monitor",
            "monitoring_category": pick_first(
                issue.get("monitoring_category"),
                issue.get("standard_category"),
                issue.get("category"),
            ).lower(),
            "recurrence_group_key": pick_first(issue.get("recurrence_group_key")),
            "recurrence_count": as_int(issue.get("recurrence_count"), 0),
            "alert_status": pick_first(issue.get("alert_status"), "none"),
            "next_check_due_at": issue.get("next_check_due_at") or None,
            "resolved_at": issue.get("resolved_at") or None,
            "resolved_by_event_id": pick_first(issue.get("resolved_by_event_id")),
        },
        "audit": {
            "created_at": pick_first(issue.get("created_at"), now),
            "updated_at": pick_first(issue.get("updated_at"), now),
            "created_by": "homefax_intake_mapper_v1",
            "updated_by": "homefax_intake_mapper_v1",
            "source_trace": [
                {
                    "system": "fastapi_standard_preview",
                    "event": "mapped_to_homefax_intake_standard_v1",
                    "mapped_at": now,
                }
            ],
        },
    }


def build_homefax_intake_payload(
    preview_payload: Dict[str, Any],
    record_id: str,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Dict[str, Any]:
    now = utc_now()

    issues = preview_payload.get("issues") or preview_payload.get("standard_findings") or []

    if not isinstance(issues, list):
        raise ValueError("Input payload must contain issues[] or standard_findings[] array.")

    standard_findings = [
        map_issue_to_standard_finding(issue, index, now)
        for index, issue in enumerate(issues)
        if isinstance(issue, dict)
    ]

    return {
        "homefax_intake_standard_version": DEFAULT_VERSION,
        "record_id": record_id,
        "tenant_id": tenant_id,
        "source": {
            "source_system": "fastapi",
            "source_workflow": "homefax_standard_preview_clean_v4",
            "source_record_id": record_id,
            "source_submission_id": "",
            "received_at": now,
        },
        "property": {
            "property_id": "",
            "address_full": pick_first(
                preview_payload.get("property_address"),
                preview_payload.get("address"),
                "Unknown property address",
            ),
            "street": "",
            "city": "",
            "state": "",
            "postal_code": "",
            "country": "US",
            "property_type": "",
            "year_built": None,
            "square_feet": None,
        },
        "homeowner": {
            "homeowner_user_id": "",
            "name": pick_first(preview_payload.get("homeowner_name")),
            "email": pick_first(preview_payload.get("homeowner_email")),
            "phone": pick_first(preview_payload.get("homeowner_phone")),
        },
        "inspection": {
            "inspection_id": pick_first(preview_payload.get("inspection_id")),
            "inspection_date": pick_first(preview_payload.get("inspection_date")),
            "inspection_company": pick_first(preview_payload.get("inspection_company")),
            "inspector_name": pick_first(preview_payload.get("inspector_name")),
            "report_family": pick_first(preview_payload.get("report_family")),
            "detected_adapter": pick_first(preview_payload.get("detected_adapter")),
            "services": {
                "general_home_inspection": True,
                "radon": False,
                "termite": False,
                "sewer_scope": False,
                "drone": False,
            },
        },
        "original_report": {
            "file_name": pick_first(preview_payload.get("file_name"), f"{record_id}.pdf"),
            "file_url": pick_first(preview_payload.get("file_url")),
            "stored_pdf_url": pick_first(preview_payload.get("stored_pdf_url"), preview_payload.get("source_pdf_url")),
            "page_count": preview_payload.get("page_count") if preview_payload.get("page_count") not in ("", None) else None,
            "sha256": pick_first(preview_payload.get("sha256")),
            "storage_status": pick_first(preview_payload.get("storage_status"), "stored"),
        },
        "processing": {
            "pipeline_stage": normalize_pipeline_stage(preview_payload),
            "parser_status": "complete" if standard_findings else "partial",
            "image_processing_status": "complete",
            "standardization_status": "complete" if standard_findings else "partial",
            "issues_count": len(standard_findings),
            "candidate_images_count": sum(
                as_int(item.get("evidence", {}).get("candidate_image_count"), 0)
                for item in standard_findings
            ),
            "errors": [],
        },
        "standard_findings": standard_findings,
        "audit": {
            "created_at": now,
            "updated_at": now,
            "created_by": "homefax_intake_mapper_v1",
            "updated_by": "homefax_intake_mapper_v1",
            "source_trace": [
                {
                    "system": "fastapi_standard_preview",
                    "event": "intake_standard_payload_generated",
                    "mapped_at": now,
                }
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Map standard preview JSON into HomeFax Intake Standard v1.")
    parser.add_argument("--input", required=True, help="Path to standard preview JSON file.")
    parser.add_argument("--output", required=True, help="Path to write mapped intake standard JSON.")
    parser.add_argument("--record-id", required=True, help="HomeFax record id.")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="Tenant id.")

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    preview_payload = json.loads(input_path.read_text())
    mapped_payload = build_homefax_intake_payload(
        preview_payload=preview_payload,
        record_id=args.record_id,
        tenant_id=args.tenant_id,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(mapped_payload, indent=2))

    print("HomeFax Intake Standard Mapper v1 complete")
    print(f"input: {input_path}")
    print(f"output: {output_path}")
    print(f"record_id: {args.record_id}")
    print(f"standard_findings: {len(mapped_payload.get('standard_findings', []))}")
    print(f"candidate_images_count: {mapped_payload.get('processing', {}).get('candidate_images_count')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
