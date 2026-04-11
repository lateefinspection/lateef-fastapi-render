import re
from .base_adapter import BaseAdapter


class AmeriSpecAdapter(BaseAdapter):
    name = "amerispec"

    ISSUE_KEYWORDS = [
        "crack",
        "damage",
        "damaged",
        "leak",
        "defect",
        "unsafe",
        "hazard",
        "corrosion",
        "deteriorated",
        "missing",
        "not working",
        "not functional",
        "repair",
        "replace",
    ]

    def clean_line(self, line: str) -> str:
        return re.sub(r"\s+", " ", line.strip())

    def infer_severity(self, text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["unsafe", "hazard", "critical"]):
            return "high"
        if any(x in t for x in ["repair", "damage", "crack", "leak", "defect"]):
            return "medium"
        return "unknown"

    def infer_priority(self, text: str) -> str:
        sev = self.infer_severity(text)
        return sev if sev != "unknown" else "medium"

    def extract_summary_issues(self, pages):
        issues = []
        counter = 1

        current_system = "General"
        current_issue = None
        current_recommendation = ""

        for page in pages:
            text = page.get("text", "")
            page_number = page.get("page_number")
            lines = text.splitlines()

            for raw_line in lines:
                line = self.clean_line(raw_line)
                lower = line.lower()

                if line.endswith(":") and len(line.split()) < 6:
                    current_system = line.replace(":", "").strip()
                    continue

                if any(k in lower for k in ["defect", "condition", "observation"]):
                    current_issue = line
                    continue

                if "recommendation" in lower:
                    current_recommendation = line
                    continue

                if current_issue and any(k in lower for k in self.ISSUE_KEYWORDS):
                    issue_text = f"{current_issue} - {line}"

                    issues.append({
                        "issue_code": f"AM.{counter}",
                        "system": current_system,
                        "component": current_system,
                        "issue_title": issue_text[:120],
                        "summary_page": page_number,
                        "detail_page": None,
                        "report_severity": self.infer_severity(issue_text),
                        "platform_priority": self.infer_priority(issue_text),
                        "source_text": "",
                        "recommendation_text": current_recommendation or "Further evaluation recommended.",
                        "candidate_image_paths": [],
                        "all_page_image_paths": [],
                        "verified_image_path": None,
                    })

                    counter += 1
                    current_issue = None
                    current_recommendation = ""

        return issues

    def extract_detail(self, issue_code, pages):
        return None, "", "Further evaluation recommended."
