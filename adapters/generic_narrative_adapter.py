class GenericNarrativeAdapter:
    name = "generic_narrative"

    def parse(self, extracted):
        issues = []

        pages = extracted.get("pages", [])

        for page in pages:
            text = page.get("text", "")

            lines = text.split("\n")

            for line in lines:
                l = line.lower()

                if any(keyword in l for keyword in [
                    "repair",
                    "recommend",
                    "defect",
                    "damage",
                    "replace",
                    "issue"
                ]):
                    issues.append({
                        "issue_code": "GN.1",
                        "system": "General",
                        "component": "Unknown",
                        "issue_title": line[:80],
                        "homeowner_summary": line,
                        "why_it_matters": "Potential issue identified in inspection report.",
                        "next_action": "Review and evaluate.",
                        "summary_page": page.get("page_number")
                    })

        return issues
