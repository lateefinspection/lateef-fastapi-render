from adapters.bigben_internachi_adapter import BigBenInternachiAdapter
from adapters.roof_report_adapter import RoofReportAdapter
from adapters.sewer_scope_adapter import SewerScopeAdapter
from adapters.section_based_adapter import SectionBasedAdapter


def classify_report(extracted):
    pages = extracted.get("pages", [])
    first_pages_text = "\n".join(page.get("text", "") for page in pages[:10]).lower()

    # Big Ben / InterNACHI-style full inspection
    if (
        "inspection report by big ben inspections" in first_pages_text
        or "internachi" in first_pages_text
    ):
        return BigBenInternachiAdapter()

    # Roof-only report heuristic
    roof_keywords = [
        "roof inspection",
        "roof report",
        "roof covering",
        "flashing",
        "gutters",
        "downspouts",
        "roof penetrations",
        "shingles",
    ]
    roof_hits = sum(1 for k in roof_keywords if k in first_pages_text)

    if roof_hits >= 3:
        return RoofReportAdapter()

    # Sewer scope report heuristic
    sewer_keywords = [
        "sewer scope",
        "sewer inspection",
        "camera inspection",
        "lateral",
        "roots",
        "belly",
        "offset joint",
        "blockage",
        "cleanout",
        "main line",
    ]
    sewer_hits = sum(1 for k in sewer_keywords if k in first_pages_text)

    if sewer_hits >= 3:
        return SewerScopeAdapter()

    # Fallback for unknown / looser report formats
    return SectionBasedAdapter()
