import re
from .base_adapter import BaseAdapter


class SectionBasedAdapter(BaseAdapter):
    name = "section_based"

    BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$")

    STRONG_SIGNALS = [
        "damage", "damaged", "deteriorated", "defect", "defective",
        "missing", "crack", "cracked", "leak", "leaking",
        "rot", "rotted", "unsafe", "hazard", "not working",
        "not functional", "loose", "corrosion", "staining",
        "improper", "trip hazard", "water damage", "water intrusion",
        "spalling", "voids", "undermining", "not present",
        "settlement", "movement", "heaving", "anti-tip bracket missing",
        "recommend repairs", "recommend replacement", "recommend evaluation",
        "further evaluation", "monitor", "service recommended",
        "repair recommended", "replace recommended", "condensation leak",
    ]

    BAD_PHRASES = [
        "summary pages",
        "table of contents",
        "inspection report",
        "read the complete report",
        "standards of practice",
        "this inspection is not",
        "this report is",
        "not intended",
        "buyer name",
        "professional home inspections",
        "copyright notice",
        "inspection agreement",
        "client was offered",
        "services not requested",
        "recommended as they may cover future repairs",
        "part of homeownership",
        "should not be relied upon",
        "structure orientation",
        "important information/limitations",
        "weather conditions",
        "temperature at the time of",
        "precipitation in the last 48 hrs",
        "for informational purposes",
        "this document",
        "direct real estate representative",
        "non-transferrable",
        "guarantee or warranty",
        "scope and limitations",
        "definition of terms",
        "serviceable (s)",
        "consideration item (ci)",
        "not present (np)",
        "not inspected (ni)",
        "not operated (no)",
        "repair / replace (rr)",
    ]

    SYSTEM_KEYWORDS = {
        "Roof": [
            "roof", "shingle", "flashing", "gutter", "downspout",
            "soffit", "fascia", "chimney", "roof vent", "ridge vent",
        ],
        "Exterior": [
            "siding", "cladding", "trim", "window", "door", "deck",
            "porch", "driveway", "walkway", "grading", "drainage",
            "retaining wall", "garage door", "exterior",
        ],
        "Electrical": [
            "electrical", "gfci", "outlet", "receptacle", "panel",
            "breaker", "wiring", "double tapped", "switch",
        ],
        "Plumbing": [
            "plumbing", "sink", "toilet", "shower", "tub", "faucet",
            "drain", "supply line", "leak", "water heater",
        ],
        "HVAC": [
            "hvac", "furnace", "air handler", "condenser", "coil",
            "condensation", "duct", "return", "supply", "cooling",
            "heating", "thermostat", "unit",
        ],
        "Interior": [
            "interior", "wall", "ceiling", "floor", "stair", "handrail",
            "drywall", "cabinet", "countertop", "window", "door",
        ],
        "Foundation": [
            "foundation", "crawlspace", "basement", "settlement",
            "crack", "movement", "heaving", "slab",
        ],
    }

    def clean_line(self, line: str) -> str:
        s = line.strip()
        m = self.BULLET_RE.match(s)
        if m:
            s = m.group(1).strip()
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def infer_system(self, text: str) -> str:
        lower = text.lower()
        for system, keywords in self.SYSTEM_KEYWORDS.items():
            if any(k in lower for k in keywords):
                return system
        return "General"

    def infer_component(self, text: str) -> str:
        cleaned = self.clean_line(text)
        cleaned = re.sub(r"^(sfty|saf|info|obs|excl|rr|ci|np|ni|no)\s*[-:]\s*", "", cleaned, flags=re.IGNORECASE)
        first = re.split(r"[,:;()\-]", cleaned)[0].strip()
        words = first.split()
        if not words:
            return "General"
        return " ".join(words[:4]).title()

    def line_looks_like_issue(self, line: str) -> bool:
        s = self.clean_line(line)
        if not s:
            return False

        lower = s.lower()

        # Block definition lines like "NOT PRESENT (NP) = ..."
        if re.match(r"^[A-Z\s/]+\(.*\)\s*=", s):
            return False

        # Block ALL CAPS headings
        if s.isupper() and len(s.split()) <= 6:
            return False

        # Block legend/definition keywords
        definition_keywords = [
            "not present",
            "not inspected",
            "not operated",
            "repair / replace",
            "serviceable",
            "consideration item",
            "definition of terms",
        ]
        if any(k in lower for k in definition_keywords):
            return False

        # Reject numbering patterns from structured summary lists
        if re.match(r"^\d+\.\d+\.\d+", s):
            return False

        # Reject section headers like "16: Something"
        if re.match(r"^\d{1,2}[:.)]\s+", s):
            return False

        # Reject very long narrative paragraphs
        if len(s.split()) > 18:
            return False

        # Reject lines that are too short
        if len(s.split()) < 3:
            return False

        # Reject obvious document/legal text
        if any(p in lower for p in self.BAD_PHRASES):
            return False

        # Reject general "inspected for..." narrative lines
        if "were inspected" in lower or "was inspected" in lower:
            return False

        # Reject lines starting with narrative stop phrases
        bad_starts = [
            "the client", "this report", "this inspection", "for the sake",
            "there may be", "copying and pasting", "one-year home warranties",
            "references to", "professional home inspections",
        ]
        if any(lower.startswith(p) for p in bad_starts):
            return False

        # Keep only lines with real issue/action signals
        return any(signal in lower for signal in self.STRONG_SIGNALS)

    def extract_summary_issues(self, pages):
        issues = []
        counter = 1

        for page in pages:
            page_number = page.get("page_number")
            text = page.get("text", "")
            lower = text.lower()

            # Skip TOC / summary instruction pages
            if "table of contents" in lower or "summary pages" in lower:
                continue

            for raw_line in text.splitlines():
                line = self.clean_line(raw_line)
                if not self.line_looks_like_issue(line):
                    continue

                system = self.infer_system(line)
                component = self.infer_component(line)

                issues.append({
                    "issue_code": f"S1.{counter}",
                    "system": system,
                    "component": component,
                    "issue_title": line[:140],
                    "summary_page": page_number
                })
                counter += 1

        # Deduplicate by title
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
        extracted_issues = self.extract_summary_issues(pages)
        target_issue = None

        for issue in extracted_issues:
            if issue["issue_code"] == issue_code:
                target_issue = issue
                break

        if not target_issue:
            return None, "", ""

        summary_page = target_issue.get("summary_page")
        issue_title = target_issue.get("issue_title", "").lower()

        # Try same page first
        if summary_page:
            page = self.get_page_by_number(pages, summary_page)
            if page:
                text = self.clean_text_block(page.get("text", ""))
                recommendation = self.extract_recommendation(text)
                return summary_page, text, recommendation

        # Fallback search
        for page in pages:
            text = self.clean_text_block(page.get("text", ""))
            if issue_title and issue_title[:40] in text.lower():
                recommendation = self.extract_recommendation(text)
                return page.get("page_number"), text, recommendation

        return None, "", "Further evaluation recommended."

    def extract_recommendation(self, text: str) -> str:
        text = text.strip()
        lower = text.lower()

        if any(p in lower for p in self.BAD_PHRASES):
            return "Further evaluation recommended."

        patterns = [
            r"recommended action[:\s]+(.+?)(?:\n|$)",
            r"recommend(?:ed)?[:\s]+(.+?)(?:\n|$)",
            r"should be repaired[:\s]*(.+?)(?:\n|$)",
            r"should be replaced[:\s]*(.+?)(?:\n|$)",
            r"(contact a qualified .+?)(?:\.|$)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip() if match.groups() else match.group(0).strip()
                if len(value.split()) <= 20:
                    return self.clean_recommendation(value)

        if "electric" in lower:
            return "Contact a qualified electrician."
        if "plumb" in lower:
            return "Contact a qualified plumbing contractor."
        if "roof" in lower:
            return "Contact a qualified roofing contractor."
        if "hvac" in lower or "heating" in lower or "cooling" in lower or "condensation" in lower:
            return "Contact a qualified HVAC professional."

        return "Further evaluation recommended."
