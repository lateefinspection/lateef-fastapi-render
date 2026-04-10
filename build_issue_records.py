import json
import uuid
from report_classifier import classify_report

# Import adapters
from adapters.bigben_internachi_adapter import BigBenInternachiAdapter
from adapters.section_based_adapter import SectionBasedAdapter
from adapters.spectora_adapter import SpectoraAdapter


def load_extracted():
    with open("output/extracted.json", "r", encoding="utf-8") as f:
        return json.load(f)


def get_adapter(name):
    adapters = {
        "bigben_internachi": BigBenInternachiAdapter,
        "section_based": SectionBasedAdapter,
        "spectora": SpectoraAdapter,
    }

    return adapters.get(name, SectionBasedAdapter)()


def build_issue_records():
    extracted = load_extracted()

    pages = extracted.get("pages", [])

    # 🔥 FIXED: pass pages (not entire object)
    adapter_name = classify_report(pages)

    adapter = get_adapter(adapter_name)

    print(f"Adapter used: {adapter_name}")

    issues = adapter.extract_summary_issues(pages)

    records = []

    for issue in issues:
        record = {
            "adapter_name": adapter_name,
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

            # 🔥 CORE ISSUE DATA
            "issue_code": issue.get("issue_code"),
            "system": issue.get("system"),
            "component": issue.get("component"),
            "issue_title": issue.get("issue_title"),
            "report_severity": issue.get("report_severity"),
            "platform_priority": issue.get("platform_priority"),

            "homeowner_summary": f"{issue.get('issue_title', '').lower()}. Recommended action: Further evaluation recommended.",
            "why_it_matters": "May lead to damage, safety issues, or system failure if not addressed.",
            "next_action": "Further evaluation recommended.",

            "repair_type": "specialist_evaluation",
            "suggested_timeline": "30_to_90_days",
            "monitoring_status": "needs_repair",

            "summary_page": issue.get("summary_page"),
            "detail_page": None,
            "source_text": "",
            "recommendation_text": "Further evaluation recommended.",

            "candidate_image_paths": [],
            "all_page_image_paths": [],
            "verified_image_path": None,

            "review_status": "pending_verification",
            "review_state": "new",
            "last_viewed_at": None,
            "approved_at": None,
        }

        records.append(record)

    return adapter_name, records


def main():
    adapter_name, records = build_issue_records()

    print(f"Issue records built: {len(records)}")

    with open("output/issue_records_v1.json", "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print("Saved to: output/issue_records_v1.json")


if __name__ == "__main__":
    main()
