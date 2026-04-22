class SectionBasedAdapter:
    name = "section_based"

    def parse(self, extracted):
        issues = []
        current_system = "General"

        pages = extracted.get("pages", [])

        for page in pages:
            text = page.get("text", "")
            lines = text.split("\n")

            for line in lines:
                clean = line.strip()
                lower = clean.lower()

                # 🔹 Detect section headers (ALL CAPS)
                if clean.isupper() and 3 < len(clean) < 40:
                    current_system = clean.title()
                    continue

                # 🔹 Skip junk lines
                if len(clean) < 40:
                    continue

                if any(skip in lower for skip in [
                    "inspection report",
                    "prepared for",
                    "date of inspection",
                    "cover page",
                    "this report is",
                    "summary of conditions"
                ]):
                    continue

                # 🔹 Only detect STRONG issue signals
                if any(keyword in lower for keyword in [
                    "defect",
                    "damage",
                    "not functioning",
                    "leak",
                    "crack",
                    "missing",
                    "unsafe",
                    "recommend repair",
                    "needs repair",
                    "replace"
                ]):
                    issues.append({
                        "issue_code": "SB.1",
                        "system": current_system,
                        "component": "Unknown",
                        "issue_title": clean[:100],
                        "homeowner_summary": clean,
                        "why_it_matters": "Issue identified in inspection section.",
                        "next_action": "Repair or further evaluate.",
                        "summary_page": page.get("page_number")
                    })

        return issues
