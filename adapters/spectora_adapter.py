import re
from .base_adapter import BaseAdapter


class SpectoraAdapter(BaseAdapter):
    name = "spectora"

    ISSUE_PATTERN = re.compile(r"^\d+\.\d+\.\d+\s+(.+?):\s+(.+)$")

    def clean_line(self, line: str) -> str:
        s = re.sub(r"\s+", " ", line.strip())

        replacements = {
            "Õ": "i",
            "Ö": "f",
            "Þ": "ff",
            "\u00a0": " ",
        }
        for k, v in replacements.items():
            s = s.replace(k, v)

        return s

    def infer_severity(self, text: str) -> str:
        t = text.lower()
        if any(x in t for x in ["unsafe", "hazard", "replace", "scalding", "double taps"]):
            return "high"
        if any(x in t for x in ["repair", "damage", "damaged", "crack", "leak", "corrosion", "settling"]):
            return "medium"
        if any(x in t for x in ["maintenance", "upgrade", "monitor"]):
            return "low"
        return "unknown"

    def infer_priority(self, text: str) -> str:
        sev = self.infer_severity(text)
        return sev if sev != "unknown" else "medium"

    def extract_summary_issues(self, pages):
        issues = []
        counter = 1
        summary_found = False

        for page in pages:
            text = page.get("text", "")
            page_number = page.get("page_number")
            lower = text.lower()

            if "summary" in lower and not summary_found:
                summary_found = True

            if not summary_found:
                continue

            matched_any = False

            for raw_line in text.splitlines():
                line = self.clean_line(raw_line)
                match = self.ISSUE_PATTERN.match(line)
                if not match:
                    continue

                matched_any = True

                system_part = match.group(1).strip()
                issue_part = match.group(2).strip()

                system = system_part.split("-")[0].strip() or "General"

                issues.append({
                    "issue_code": f"SP.{counter}",
                    "system": system,
                    "component": system_part,
                    "issue_title": issue_part,
                    "summary_page": page_number,
                    "detail_page": None,
                    "report_severity": self.infer_severity(issue_part),
                    "platform_priority": self.infer_priority(issue_part),
                    "source_text": "",
                    "recommendation_text": "Further evaluation recommended.",
                    "candidate_image_paths": [],
                    "all_page_image_paths": [],
                    "verified_image_path": None,
                })
                counter += 1

            if summary_found and not matched_any:
                break

        seen = set()
        clean = []
        for issue in issues:
            key = issue["issue_title"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            clean.append(issue)

        return clean

    def extract_detail(self, issue_code, pages):
        issues = self.extract_summary_issues(pages)

        for issue in issues:
            if issue["issue_code"] == issue_code:
                page_num = issue.get("summary_page")
                page = self.get_page_by_number(pages, page_num)

                if page:
                    text = self.clean_text_block(page.get("text", ""))
                    issue["detail_page"] = page_num
                    issue["source_text"] = text
                    issue["recommendation_text"] = "Further evaluation recommended."
                    return page_num, text, "Further evaluation recommended."

        return None, "", "Further evaluation recommended."
