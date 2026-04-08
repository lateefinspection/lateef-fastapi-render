import re
from .base_adapter import BaseAdapter


class RoofReportAdapter(BaseAdapter):
    name = "roof_report"

    SUMMARY_PAGES = [1, 2]

    FOOTER_PATTERNS = BaseAdapter.FOOTER_PATTERNS + [
        re.compile(r'roof inspection', re.IGNORECASE),
        re.compile(r'page\s+\d+', re.IGNORECASE),
    ]

    ROOF_ISSUE_LINE_WITH_CODE = re.compile(
        r'^(?P<code>\d+\.\d+\.\d+)\s+'
        r'(?P<component>[^:]+):\s*'
        r'(?P<title>.+)$'
    )

    ROOF_ISSUE_LINE_NO_CODE = re.compile(
        r'^(?P<component>Roof Covering|Flashing|Gutters|Downspouts|Chimney|Skylight|Vent Pipe|Roof Penetrations|Drainage|Soffit|Fascia|Eaves)\s*:\s*(?P<title>.+)$',
        re.IGNORECASE
    )

    def extract_summary_issues(self, pages):
        issues = []
        synthetic_counter = 1

        for page in pages:
            if page["page_number"] not in self.SUMMARY_PAGES:
                continue

            for raw_line in page.get("text", "").splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                m1 = self.ROOF_ISSUE_LINE_WITH_CODE.match(line)
                if m1:
                    issues.append({
                        "issue_code": m1.group("code").strip(),
                        "system": "Roof",
                        "component": m1.group("component").strip(),
                        "issue_title": m1.group("title").strip(),
                        "summary_page": page["page_number"]
                    })
                    continue

                m2 = self.ROOF_ISSUE_LINE_NO_CODE.match(line)
                if m2:
                    synthetic_code = f"R.{synthetic_counter}"
                    synthetic_counter += 1

                    issues.append({
                        "issue_code": synthetic_code,
                        "system": "Roof",
                        "component": m2.group("component").strip(),
                        "issue_title": m2.group("title").strip(),
                        "summary_page": page["page_number"]
                    })

        seen = set()
        deduped = []
        for issue in issues:
            key = (issue["issue_code"], issue["component"], issue["issue_title"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)

        return deduped

    def find_detail_by_component_title(self, component, issue_title, pages):
        component_lower = component.lower()
        title_lower = issue_title.lower()

        best = None
        best_score = 0

        for page in pages:
            text = page.get("text", "")
            if not text:
                continue

            text_lower = text.lower()

            score = 0
            if component_lower in text_lower:
                score += 5
            if title_lower in text_lower:
                score += 10

            title_words = [w for w in title_lower.split() if len(w) > 3]
            for word in title_words:
                if word in text_lower:
                    score += 1

            if score > best_score:
                best_score = score
                best = page

        if not best or best_score < 6:
            return None, "", ""

        lines = best.get("text", "").splitlines()

        start = None
        for i, line in enumerate(lines):
            l = line.strip().lower()
            if title_lower in l or component_lower in l:
                start = i
                break

        if start is None:
            return best["page_number"], "", ""

        block = []
        for i in range(start, min(len(lines), start + 18)):
            block.append(lines[i])

        block_text = self.clean_text_block("\n".join(block))

        recommendation = ""
        rec_match = re.search(
            r"(recommend(?:ation)?\s*[:\-]?\s*.*|contact a qualified.*|repair.*recommended.*)",
            block_text,
            re.IGNORECASE
        )
        if rec_match:
            recommendation = self.clean_recommendation(rec_match.group(1))

        return best["page_number"], block_text, recommendation

    def extract_detail(self, issue_code, pages):
        if issue_code.startswith("R."):
            return None, "", ""

        for page in pages:
            text = page.get("text", "")
            if issue_code not in text:
                continue

            lines = text.splitlines()
            start = None

            for i, line in enumerate(lines):
                if issue_code in line:
                    start = i
                    break

            if start is None:
                continue

            block = []
            for i in range(start, min(len(lines), start + 18)):
                line = lines[i]

                if i > start and re.match(r'^\d+\.\d+\.\d+', line.strip()):
                    break

                block.append(line)

            block_text = self.clean_text_block("\n".join(block))

            recommendation = ""
            rec_match = re.search(
                r"(Recommendation\s*.*|Contact a qualified.*|Repair.*recommended.*)",
                block_text,
                re.IGNORECASE | re.DOTALL
            )
            if rec_match:
                rec_line = rec_match.group(1).splitlines()[0].strip()
                recommendation = self.clean_recommendation(rec_line)

            return page["page_number"], block_text, recommendation

        return None, "", ""

    def extract_detail_from_issue(self, issue, pages):
        if not issue["issue_code"].startswith("R."):
            detail_page, source_text, recommendation = self.extract_detail(issue["issue_code"], pages)
            if detail_page:
                return detail_page, source_text, recommendation

        return self.find_detail_by_component_title(
            issue["component"],
            issue["issue_title"],
            pages
        )
