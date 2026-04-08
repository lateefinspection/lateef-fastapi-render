import re
import math


class BaseAdapter:
    name = "base"

    SUMMARY_PAGES = []

    ISSUE_LINE_RE = re.compile(
        r'^(?P<code>\d+\.\d+\.\d+)\s+'
        r'(?P<system>[^-]+?)\s*-\s*'
        r'(?P<component>[^:]+):\s*'
        r'(?P<title>.+)$'
    )

    ISSUE_CODE_RE = re.compile(r'^\d+\.\d+\.\d+\b')

    FOOTER_PATTERNS = [
        re.compile(r'Page\s+\d+\s+of\s+\d+', re.IGNORECASE),
    ]

    SEVERITY_PATTERNS = [
        ("major", re.compile(r"\bmajor defect\b", re.IGNORECASE)),
        ("material", re.compile(r"\bmaterial defect\b", re.IGNORECASE)),
        ("minor", re.compile(r"\bminor defect\b", re.IGNORECASE)),
        ("safety", re.compile(r"\bsafety\b", re.IGNORECASE)),
    ]

    def clean_text_block(self, text: str) -> str:
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                lines.append("")
                continue
            if any(p.search(stripped) for p in self.FOOTER_PATTERNS):
                continue
            lines.append(stripped)
        return "\n".join(lines).strip()

    def clean_recommendation(self, text: str) -> str:
        text = text.strip()
        text = re.sub(r'\b(Major|Material|Minor)\s+Defect\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text).strip(" .")
        return text + "." if text else ""

    def normalize_report_severity(self, text: str) -> str:
        for label, pattern in self.SEVERITY_PATTERNS:
            if pattern.search(text):
                if label == "safety":
                    return "major"
                return label
        return "unknown"

    def map_platform_priority(self, report_severity: str, text: str) -> str:
        t = text.lower()

        if any(k in t for k in [
            "hazard",
            "active water leak",
            "leaking",
            "prone to leaking",
            "collapse",
            "unsafe",
            "safety concern",
        ]):
            return "urgent"

        if report_severity == "major":
            return "high"

        if report_severity == "material":
            return "medium"

        if report_severity == "minor":
            return "low"

        return "medium"

    def map_repair_type(self, text: str, recommendation: str) -> str:
        combined = f"{text} {recommendation}".lower()

        if "recommended diy project" in combined or "diy" in combined:
            return "diy_possible"

        if any(k in combined for k in [
            "electric", "electrical contractor", "electrician",
            "plumb", "plumbing contractor",
            "roof", "roofer", "roofing professional", "gutter contractor",
            "hvac"
        ]):
            return "licensed_contractor"

        if "engineer" in combined:
            return "specialist_evaluation"

        if "handyman" in combined:
            return "handyman"

        return "specialist_evaluation"

    def map_timeline(self, priority: str, text: str) -> str:
        t = text.lower()

        if priority == "urgent":
            return "before_occupancy"

        if priority == "high":
            return "0_to_30_days"

        if "old system" in t or "budgeting for repairs and future replacement" in t:
            return "budget_and_monitor"

        if priority == "medium":
            return "30_to_90_days"

        return "monitor_annually"

    def build_homeowner_summary(self, issue_title: str, recommendation: str) -> str:
        base = issue_title.lower()
        if recommendation:
            return f"{base}. Recommended action: {recommendation}"
        return base

    def build_why_it_matters(self, text: str) -> str:
        t = text.lower()

        if any(k in t for k in ["leak", "water intrusion", "moisture", "mold"]):
            return "May allow water intrusion, hidden damage, mold, or moisture-related deterioration."

        if any(k in t for k in ["hazard", "electrical", "wiring", "shock"]):
            return "May create a safety hazard and should be addressed by a qualified professional."

        if any(k in t for k in ["structural", "collapse", "movement"]):
            return "May affect structural stability or safety."

        if "old system" in t or "end of its service life" in t:
            return "Appears near or beyond expected service life and may fail unexpectedly."

        return "May lead to damage, safety issues, or system failure if not addressed."

    def extract_summary_issues(self, pages):
        issues = []

        for page in pages:
            if page["page_number"] not in self.SUMMARY_PAGES:
                continue

            for line in page.get("text", "").splitlines():
                match = self.ISSUE_LINE_RE.match(line.strip())
                if not match:
                    continue

                issues.append({
                    "issue_code": match.group("code").strip(),
                    "system": match.group("system").strip(),
                    "component": match.group("component").strip(),
                    "issue_title": match.group("title").strip(),
                    "summary_page": page["page_number"]
                })

        seen = set()
        deduped = []
        for issue in issues:
            if issue["issue_code"] in seen:
                continue
            seen.add(issue["issue_code"])
            deduped.append(issue)

        return deduped

    def extract_detail(self, issue_code, pages):
        raise NotImplementedError

    def get_page_by_number(self, pages, page_number):
        for page in pages:
            if page["page_number"] == page_number:
                return page
        return None

    def get_images_for_page(self, images, page_number):
        result = []
        for img in images:
            if img["page_number"] != page_number:
                continue

            filename = img["filename"].lower()

            # decorative/reused assets
            if filename.endswith(".png"):
                continue

            result.append(img)

        return result

    def bbox_center(self, bbox):
        x0, y0, x1, y1 = bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    def bbox_distance(self, a, b):
        ax, ay = self.bbox_center(a)
        bx, by = self.bbox_center(b)
        return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)

    def bbox_top(self, bbox):
        return bbox[1]

    def bbox_bottom(self, bbox):
        return bbox[3]

    def bbox_left(self, bbox):
        return bbox[0]

    def bbox_right(self, bbox):
        return bbox[2]

    def extract_issue_anchor_blocks(self, page):
        anchors = []

        for block in page.get("text_blocks", []):
            text = block.get("text", "").strip()
            bbox = block.get("bbox")

            if not text or not bbox:
                continue

            if self.ISSUE_CODE_RE.match(text):
                anchors.append({
                    "text": text,
                    "bbox": bbox
                })

        anchors.sort(key=lambda b: self.bbox_top(b["bbox"]))
        return anchors

    def find_current_issue_anchor(self, page, issue_code, issue_title):
        if not page:
            return None

        title_lower = issue_title.lower()
        candidates = []

        for block in page.get("text_blocks", []):
            text = block.get("text", "").lower()
            bbox = block.get("bbox")

            if not text or not bbox:
                continue

            score = 0

            if issue_code in text:
                score += 20

            if title_lower in text:
                score += 10

            title_words = [w for w in title_lower.split() if len(w) > 3]
            for word in title_words:
                if word in text:
                    score += 1

            if score > 0:
                candidates.append((score, block))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def find_next_issue_anchor(self, page, current_anchor):
        anchors = self.extract_issue_anchor_blocks(page)
        if not current_anchor or not current_anchor.get("bbox"):
            return None

        current_top = self.bbox_top(current_anchor["bbox"])

        below = []
        for anchor in anchors:
            anchor_top = self.bbox_top(anchor["bbox"])
            if anchor_top > current_top + 5:
                below.append(anchor)

        if not below:
            return None

        below.sort(key=lambda a: self.bbox_top(a["bbox"]))
        return below[0]

    def rank_images_for_issue_region(self, issue_anchor, next_anchor, page_images):
        if not issue_anchor or not issue_anchor.get("bbox"):
            return []

        issue_bbox = issue_anchor["bbox"]

        issue_top = self.bbox_top(issue_bbox)
        issue_bottom = self.bbox_bottom(issue_bbox)
        issue_left = self.bbox_left(issue_bbox)
        issue_right = self.bbox_right(issue_bbox)

        issue_center_x, _ = self.bbox_center(issue_bbox)

        next_top = None
        if next_anchor and next_anchor.get("bbox"):
            next_top = self.bbox_top(next_anchor["bbox"])

        ranked = []

        for img in page_images:
            bbox = img.get("bbox")
            if not bbox:
                continue

            img_top = self.bbox_top(bbox)
            img_center_x, img_center_y = self.bbox_center(bbox)

            in_vertical_window = False
            if next_top is not None:
                if img_center_y >= issue_top and img_center_y < next_top:
                    in_vertical_window = True
            else:
                if img_center_y >= issue_top:
                    in_vertical_window = True

            vertical_penalty = 0 if in_vertical_window else 100000

            horizontal_distance = abs(img_center_x - issue_center_x)

            if img_center_x < issue_left:
                horizontal_penalty = horizontal_distance * 3
            elif img_center_x > issue_right:
                horizontal_penalty = horizontal_distance * 1
            else:
                horizontal_penalty = 0

            vertical_gap = max(0, img_top - issue_bottom)

            score = vertical_penalty + (vertical_gap * 2) + horizontal_penalty
            ranked.append((score, img))

        ranked.sort(key=lambda x: x[0])
        return ranked

    def match_images_to_issue_block(self, pages, images, issue_code, issue_title, detail_page):
        page = self.get_page_by_number(pages, detail_page)
        if not page:
            return [], []

        page_images = self.get_images_for_page(images, detail_page)
        all_page_paths = [img["path"] for img in page_images]

        if not page_images:
            return [], []

        current_anchor = self.find_current_issue_anchor(page, issue_code, issue_title)
        next_anchor = self.find_next_issue_anchor(page, current_anchor)

        if not current_anchor:
            return [img["path"] for img in page_images[:2]], all_page_paths

        ranked = self.rank_images_for_issue_region(current_anchor, next_anchor, page_images)
        if not ranked:
            return [img["path"] for img in page_images[:2]], all_page_paths

        primary = []
        seen = set()

        for _, img in ranked:
            path = img["path"]
            if path in seen:
                continue
            seen.add(path)
            primary.append(path)
            if len(primary) >= 2:
                break

        return primary, all_page_paths
