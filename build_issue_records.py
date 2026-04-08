import json
import uuid
import os
from report_classifier import classify_report

EXTRACTED_FILE = "output/extracted.json"
OUTPUT_FILE = "output/issue_records_v1.json"
SUBMISSION_CONTEXT_FILE = "output/submission_context.json"


def load_submission_context():
    if not os.path.exists(SUBMISSION_CONTEXT_FILE):
        return {}

    with open(SUBMISSION_CONTEXT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    with open(EXTRACTED_FILE, "r", encoding="utf-8") as f:
        extracted = json.load(f)

    submission = load_submission_context()

    pages = extracted.get("pages", [])
    images = extracted.get("images", [])

    adapter = classify_report(extracted)
    issues = adapter.extract_summary_issues(pages)

    # local fallback IDs remain useful internally, but now we also keep real external IDs
    property_id = str(uuid.uuid4())
    internal_report_uuid = str(uuid.uuid4())

    records = []

    for issue in issues:
        if hasattr(adapter, "extract_detail_from_issue"):
            detail_page, source_text, recommendation = adapter.extract_detail_from_issue(issue, pages)
        else:
            detail_page, source_text, recommendation = adapter.extract_detail(
                issue["issue_code"],
                pages
            )

        combined = f"{issue['issue_title']} {source_text} {recommendation}"

        severity = adapter.normalize_report_severity(combined)
        priority = adapter.map_platform_priority(severity, combined)
        repair_type = adapter.map_repair_type(source_text, recommendation)
        timeline = adapter.map_timeline(priority, combined)

        primary_images, all_page_images = adapter.match_images_to_issue_block(
            pages,
            images,
            issue["issue_code"],
            issue["issue_title"],
            detail_page
        )

        record = {
            "adapter_name": adapter.name,

            # local/internal IDs
            "property_id": property_id,
            "internal_report_uuid": internal_report_uuid,

            # external submission linkage
            "record_id": submission.get("recordId"),
            "report_number": submission.get("reportId"),
            "submitted_at": submission.get("submittedAt"),
            "submitted_by": submission.get("submittedBy"),
            "property_address": submission.get("propertyAddress"),
            "inspection_date": submission.get("inspectionDate"),
            "client_name": submission.get("clientName"),
            "client_email": submission.get("clientEmail"),
            "client_phone": submission.get("clientPhone"),
            "additional_comments": submission.get("additionalComments"),
            "additional_services": submission.get("additionalServices", []),
            "report_url": submission.get("reportUrl"),
            "report_filename": submission.get("reportFilename"),
            "report_link": submission.get("reportLink"),
            "s3_key": submission.get("s3Key"),
            "source_pdf_sha256": submission.get("sha256"),

            # canonical issue fields
            "issue_code": issue["issue_code"],
            "system": issue["system"],
            "component": issue["component"],
            "issue_title": issue["issue_title"],
            "report_severity": severity,
            "platform_priority": priority,
            "homeowner_summary": adapter.build_homeowner_summary(
                issue["issue_title"],
                recommendation
            ),
            "why_it_matters": adapter.build_why_it_matters(combined),
            "next_action": recommendation if recommendation else "Further evaluation recommended.",
            "repair_type": repair_type,
            "suggested_timeline": timeline,
            "monitoring_status": "needs_repair",
            "summary_page": issue["summary_page"],
            "detail_page": detail_page,
            "source_text": source_text,
            "recommendation_text": recommendation,
            "candidate_image_paths": primary_images,
            "all_page_image_paths": all_page_images,
            "verified_image_path": primary_images[0] if primary_images else None,
            "review_status": "pending_verification",
            "review_state": "new",
            "last_viewed_at": None,
            "approved_at": None
        }

        records.append(record)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"Adapter used: {adapter.name}")
    print(f"Issue records built: {len(records)}")
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
