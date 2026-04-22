class SummaryAdapter:
    name = "summary_based"

    def can_handle(self, extracted):
        for page in extracted.get("pages", []):
            if "report summary" in page.get("text", "").lower():
                return True
        return True  # fallback for now

    def extract_issues(self, extracted):
        issues = []
        in_summary = False

        BAD_PHRASES = [
            "this report",
            "not intended",
            "not exhaustive",
            "read the entire report",
            "may be reported",
            "inspection is limited",
            "general information",
            "will not reveal every condition",
        ]

        GOOD_KEYWORDS = [
            "damage", "damaged",
            "crack", "cracked",
            "leak", "leaking",
            "missing",
            "loose",
            "unsafe",
            "not functioning",
            "repair",
            "replace",
            "defect",
            "recommend",
            "corrosion",
            "rot",
            "mold",
        ]

        for page in extracted.get("pages", []):
            text = page.get("text", "")

            # 🔥 START ONLY WHEN SUMMARY IS FOUND
            if "report summary" in text.lower():
                in_summary = True

            if not in_summary:
                continue

            lines = text.split("\n")

            for line in lines:
                clean = line.strip()
                lower = clean.lower()

                # Skip garbage
                if len(clean) < 40 or len(clean) > 200:
                    continue

                # Skip disclaimers
                if any(bad in lower for bad in BAD_PHRASES):
                    continue

                # Keep only real issue signals
                if not any(k in lower for k in GOOD_KEYWORDS):
                    continue

                issues.append({
                    "title": clean[:120],
                    "summary": clean,
                    "page": page.get("page_number"),
                })

        return issues
