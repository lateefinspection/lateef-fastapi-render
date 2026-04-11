import json
import uuid
from report_classifier import classify_report

from adapters.bigben_internachi_adapter import BigBenInternachiAdapter
from adapters.section_based_adapter import SectionBasedAdapter
from adapters.spectora_adapter import SpectoraAdapter
from adapters.amerispec_adapter import AmeriSpecAdapter


def load_extracted():
    with open("output/extracted.json", "r", encoding="utf-8") as f:
        return json.load(f)


def get_adapter(name):
    adapters = {
        "bigben_internachi": BigBenInternachiAdapter,
        "section_based": SectionBasedAdapter,
        "spectora": SpectoraAdapter,
        "amerispec": AmeriSpecAdapter,
    }
    return adapters.get(name, SectionBasedAdapter)()


def default_why_it_matters(issue_title: str) -> str:
    t = (issue_title or "").lower()

    if any(x in t for x in ["leak", "water", "stain", "moisture", "drainage"]):
        return "May allow water intrusion, hidden damage, mold, or moisture-related deterioration."

    if any(x in t for x in ["unsafe", "hazard", "egress", "double tap", "scalding", "loose railing"]):
        return "May create a safety hazard and should be addressed by a qualified professional."

    if any(x in t for x in ["corrosion", "crack", "settling", "sagging", "movement"]):
        return "May worsen over time and lead to larger repair needs if not addressed."

    return "May lead to damage, safety issues, or system failure if not addressed."


def default_next_action(issue_title: str) -> str:
    t = (issue_title or "").lower()

    if any(x in t for x in ["electrical", "gfci", "panel", "double tap"]):
        return "Contact a qualified electrician."
    if any(x in t for x in ["plumb", "toilet", "sink", "drain", "water heater", "scalding"]):
        return "Contact a qualified plumbing contractor."
    if any(x in t for x in ["roof", "chimney", "gutter", "downspout", "flashing"]):
        return "Contact a qualified roofing contractor."
    if any(x in t for x in ["hvac", "heating", "cooling", "air filter", "condensation"]):
        return "Contact a qualified HVAC professional."

    return "Further evaluation recommended."


def build_issue_records():
    extracted = load_extracted()
    pages = extracted.get("pages", [])

    adapter_name = classify_report(pages)
    adapter = get_adapter(adapter_name)

    print(f"Adapter used: {adapter_name}")

    issues = adapter.extract_summary_issues(pages)
    records = []

    for issue in issues:
        issue_title = issue.get("issue_title", "")
        next_action = issue.get("next_action") or default_next_action(issue_title)

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

            "issue_code": issue.get("issue_code"),
            "system": issue.get("system"),
            "component": issue.get("component"),
            "issue_title": issue_title,
            "report_severity": issue.get("report_severity", "unknown"),
            "platform_priority": issue.get("platform_priority", "medium"),

            "homeowner_summary": issue.get("homeowner_summary")
            or f"{issue_title.lower()}. Recommended action: {next_action}",

            "why_it_matters": issue.get("why_it_matters")
            or default_why_it_matters(issue_title),

            "next_action": next_action,

            "repair_type": "specialist_evaluation",
            "suggested_timeline": "30_to_90_days",
            "monitoring_status": "needs_repair",

            "summary_page": issue.get("summary_page"),
            "detail_page": issue.get("detail_page"),
            "source_text": issue.get("source_text", ""),
            "recommendation_text": issue.get("recommendation_text", next_action),

            "candidate_image_paths": issue.get("candidate_image_paths", []),
            "all_page_image_paths": issue.get("all_page_image_paths", []),
            "verified_image_path": issue.get("verified_image_path"),

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
        json.dump(records, f, indent=2, ensure_ascii=False)

    print("Saved to: output/issue_records_v1.json")


if __name__ == "__main__":
    main()
