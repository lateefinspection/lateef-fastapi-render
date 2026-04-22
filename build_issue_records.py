import json
import uuid
from datetime import datetime, timezone

from ai_issue_extractor import extract_issues_with_ai
from image_matcher import collect_images_by_page, match_images_for_issue
from normalizers import (
    normalize_issue_title,
    normalize_summary,
    normalize_system,
    normalize_component,
    normalize_severity,
    map_priority,
    default_next_action,
    default_why_it_matters,
)

INPUT_PATH = "output/extracted.json"
OUTPUT_PATH = "output/issue_records_v1.json"


def load_extracted():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_page_aware_text(extracted):
    """
    Builds text with explicit page markers so AI can return page numbers.
    """
    parts = []
    for page in extracted.get("pages", []):
        page_number = page.get("page_number")
        text = page.get("text", "") or ""
        parts.append(f"=== PAGE {page_number} ===\n{text}")
    return "\n\n".join(parts)


def dedupe_ai_issues(ai_issues):
    deduped = []
    seen = set()

    for item in ai_issues:
        raw_title = item.get("issue_title", "")
        raw_system = item.get("system", "")
        raw_page = item.get("page_number")

        issue_title = normalize_issue_title(raw_title)
        normalized_system = normalize_system(raw_system or issue_title)
        normalized_component = normalize_component(
            item.get("component"),
            issue_title=issue_title,
            normalized_system=normalized_system,
        )

        key = (
            normalized_system.lower(),
            normalized_component.lower(),
            issue_title.lower(),
            raw_page,
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(item)

    return deduped


def build_issue_records(ai_issues, extracted):
    records = []

    ai_issues = dedupe_ai_issues(ai_issues)
    images_by_page = collect_images_by_page(extracted)

    for i, item in enumerate(ai_issues, start=1):
        raw_title = item.get("issue_title", "Unknown Issue")
        raw_summary = item.get("summary", "")
        raw_system = item.get("system", "")
        raw_component = item.get("component")
        raw_severity = item.get("severity", "unknown")
        raw_page = item.get("page_number")

        issue_title = normalize_issue_title(raw_title)
        homeowner_summary = normalize_summary(raw_summary) or issue_title

        normalized_system = normalize_system(raw_system or issue_title)
        normalized_component = normalize_component(
            raw_component,
            issue_title=issue_title,
            normalized_system=normalized_system,
        )
        normalized_severity = normalize_severity(raw_severity, issue_title)
        platform_priority = map_priority(normalized_severity)

        next_action = default_next_action(normalized_system, issue_title)
        why_it_matters = default_why_it_matters(
            normalized_system,
            issue_title,
            normalized_severity,
        )

        candidate_image_paths, all_page_image_paths, verified_image_path = match_images_for_issue(
            issue_title=issue_title,
            summary_page=raw_page,
            images_by_page=images_by_page,
        )

        now_iso = datetime.now(timezone.utc).isoformat()

        records.append({
            "adapter_name": "ai_extractor",
            "property_id": str(uuid.uuid4()),
            "internal_report_uuid": str(uuid.uuid4()),

            "record_id": None,
            "report_number": None,
            "submitted_at": None,
            "submitted_by": None,

            "property_address": None,
            "inspection_date": None,

            "client_name": None,
            "client_email": None,
            "client_phone": None,

            "additional_comments": None,
            "additional_services": [],

            "report_url": None,
            "report_filename": None,
            "report_link": None,
            "s3_key": None,
            "source_pdf_sha256": None,

            "issue_code": f"AI.{i}",
            "system": normalized_system,
            "component": normalized_component,
            "issue_title": issue_title,

            "report_severity": normalized_severity,
            "platform_priority": platform_priority,

            "homeowner_summary": homeowner_summary,
            "why_it_matters": why_it_matters,
            "next_action": next_action,

            "repair_type": "general_repair",
            "suggested_timeline": "30_to_90_days" if normalized_severity != "high" else "as_soon_as_possible",
            "monitoring_status": "needs_review",

            "summary_page": raw_page,
            "detail_page": raw_page,

            "source_text": homeowner_summary,
            "recommendation_text": next_action,

            "candidate_image_paths": candidate_image_paths,
            "all_page_image_paths": all_page_image_paths,
            "verified_image_path": verified_image_path,

            "review_status": "pending_verification",
            "review_state": "new",

            "last_viewed_at": None,
            "approved_at": None,

            "created_at": now_iso,
            "updated_at": now_iso,
        })

    return records


def main():
    extracted = load_extracted()

    full_text = build_page_aware_text(extracted)

    print("🤖 Running AI extraction...")

    ai_issues = extract_issues_with_ai(full_text)

    if not ai_issues:
        print("⚠️ No issues returned from AI")
        ai_issues = []

    records = build_issue_records(ai_issues, extracted)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print("\n===== BUILD COMPLETE =====")
    print("Adapter used: ai_extractor")
    print(f"Issue records built: {len(records)}")
    print(f"Saved to: {OUTPUT_PATH}\n")


if __name__ == "__main__":
    main()
