import re
from .base_adapter import BaseAdapter


class SewerScopeAdapter(BaseAdapter):
    name = "sewer_scope"

    SUMMARY_PAGES = [1, 2]

    FOOTER_PATTERNS = BaseAdapter.FOOTER_PATTERNS + [
        re.compile(r'sewer', re.IGNORECASE),
        re.compile(r'scope', re.IGNORECASE),
        re.compile(r'camera', re.IGNORECASE),
    ]

    DISTANCE_RE = re.compile(r'(?P<distance>\d+(?:\.\d+)?)\s*(?:ft|feet|\'|")', re.IGNORECASE)

    # Example styles:
    # 45.3 ft - Roots
    # 32' Belly in line
    # 67.4 Offset joint
    DISTANCE_FINDING_RE = re.compile(
        r'^(?P<distance>\d+(?:\.\d+)?)\s*(?:ft|feet|\'|")?\s*[-: ]\s*(?P<title>.+)$',
        re.IGNORECASE
    )

    # Example:
    # Roots observed
    # Belly in pipe
    # Offset joint
    # Debris / blockage
    TITLE_ONLY_RE = re.compile(
        r'^(?P<title>(roots?|root intrusion|belly|offset|offset joint|debris|blockage|crack|broken pipe|separation|standing water|sag|grease|scale|channeling).*)$',
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

                m1 = self.DISTANCE_FINDING_RE.match(line)
                if m1:
                    distance = m1.group("distance").strip()
                    title = m1.group("title").strip()

                    issues.append({
                        "issue_code": f"S.{synthetic_counter}",
                        "system": "Sewer",
                        "component": f"Lateral at {distance} ft",
                        "issue_title": title,
                        "summary_page": page["page_number"],
                        "distance_ft": distance
                    })
                    synthetic_counter += 1
                    continue

                m2 = self.TITLE_ONLY_RE.match(line)
                if m2:
                    issues.append({
                        "issue_code": f"S.{synthetic_counter}",
                        "system": "Sewer",
                        "component": "Sewer Lateral",
                        "issue_title": m2.group("title").strip(),
                        "summary_page": page["page_number"],
                        "distance_ft": None
                    })
                    synthetic_counter += 1

        seen = set()
        deduped = []
        for issue in issues:
            key = (
                issue["component"],
                issue["issue_title"],
                issue.get("distance_ft")
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)

        return deduped

    def build_homeowner_summary(self, issue_title: str, recommendation: str) -> str:
        base = issue_title.lower()
        if recommendation:
            return f"{base}. Recommended action: {recommendation}"
        return base

    def build_why_it_matters(self, text: str) -> str:
        t = text.lower()

        if "roots" in t or "root intrusion" in t:
            return "Roots can obstruct flow and damage the sewer line over time."

        if "belly" in t or "sag" in t or "standing water" in t:
            return "A low spot in the sewer line can hold water and increase the risk of backups."

        if "offset" in t or "separation" in t or "crack" in t or "broken pipe" in t:
            return "Damage or misalignment in the sewer line can restrict flow and may worsen over time."

        if "debris" in t or "blockage" in t or "grease" in t:
            return "Obstructions in the sewer line can reduce drainage performance and increase backup risk."

        return "This sewer condition may affect drainage performance and should be evaluated further."

    def map_repair_type(self, text: str, recommendation: str) -> str:
        combined = f"{text} {recommendation}".lower()

        if "monitor" in combined:
            return "monitor_only"

        if any(k in combined for k in ["clean", "jet", "auger"]):
            return "specialist_evaluation"

        return "licensed_contractor"

    def normalize_report_severity(self, text: str) -> str:
        t = text.lower()

        if any(k in t for k in ["broken pipe", "separation", "collapse", "major"]):
            return "major"

        if any(k in t for k in ["roots", "belly", "offset", "crack", "blockage"]):
            return "material"

        return "unknown"

    def map_platform_priority(self, report_severity: str, text: str) -> str:
        t = text.lower()

        if any(k in t for k in ["broken pipe", "separation", "collapse", "backup"]):
            return "urgent"

        if report_severity == "major":
            return "high"

        if report_severity == "material":
            return "medium"

        return "medium"

    def map_timeline(self, priority: str, text: str) -> str:
        if priority == "urgent":
            return "before_occupancy"
        if priority == "high":
            return "0_to_30_days"
        if priority == "medium":
            return "30_to_90_days"
        return "monitor_annually"

    def find_detail_by_distance_or_title(self, issue, pages):
        distance_ft = issue.get("distance_ft")
        title_lower = issue["issue_title"].lower()

        best = None
        best_score = 0

        for page in pages:
            text = page.get("text", "")
            if not text:
                continue

            text_lower = text.lower()
            score = 0

            if distance_ft and distance_ft in text_lower:
                score += 10

            if title_lower in text_lower:
                score += 12

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

            if distance_ft and distance_ft in l:
                start = i
                break

            if title_lower in l:
                start = i
                break

        if start is None:
            return best["page_number"], "", ""

        block = []
        for i in range(start, min(len(lines), start + 18)):
            line = lines[i]

            # stop on next strong measurement/title marker
            if i > start and self.DISTANCE_FINDING_RE.match(line.strip()):
                break

            block.append(line)

        block_text = self.clean_text_block("\n".join(block))

        recommendation = ""
        rec_match = re.search(
            r"(recommend(?:ation)?\s*[:\-]?\s*.*|recommend.*|repair.*recommended.*|evaluate.*|clean.*recommended.*)",
            block_text,
            re.IGNORECASE
        )
        if rec_match:
            recommendation = self.clean_recommendation(rec_match.group(1))

        return best["page_number"], block_text, recommendation

    def extract_detail(self, issue_code, pages):
        # sewer adapter mostly uses synthetic codes
        return None, "", ""

    def extract_detail_from_issue(self, issue, pages):
        return self.find_detail_by_distance_or_title(issue, pages)
