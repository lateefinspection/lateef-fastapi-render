import re
from .base_adapter import BaseAdapter


class BigBenInternachiAdapter(BaseAdapter):
    name = "bigben_internachi"
    SUMMARY_PAGES = [4, 5, 6]

    FOOTER_PATTERNS = BaseAdapter.FOOTER_PATTERNS + [
        re.compile(r'Inspection Services', re.IGNORECASE),
        re.compile(r'Vasintino Johnson', re.IGNORECASE),
        re.compile(r'6039 S Carpenter St', re.IGNORECASE),
    ]

    def clean_recommendation(self, text: str) -> str:
        text = super().clean_recommendation(text)
        text = re.sub(
            r'\b(Left side of the home|Right side of home|Front porch|Exterior|Bathroom|Basement)\b',
            '',
            text,
            flags=re.IGNORECASE
        )
        text = re.sub(r'\s+', ' ', text).strip(" .")
        return text + "." if text else ""

    def extract_detail(self, issue_code, pages):
        for page in pages:
            if page["page_number"] <= 6:
                continue

            text = page.get("text", "")
            if issue_code not in text:
                continue

            lines = text.splitlines()
            start = None

            for i, line in enumerate(lines):
                if line.strip().startswith(issue_code):
                    start = i
                    break

            if start is None:
                continue

            block = []
            for i in range(start, len(lines)):
                line = lines[i]

                if i > start and re.match(r'^\d+\.\d+\.\d+', line.strip()):
                    break

                block.append(line)

            block_text = self.clean_text_block("\n".join(block))

            recommendation = ""
            rec_match = re.search(r"Recommendation\s*(.+)", block_text, re.IGNORECASE | re.DOTALL)
            if rec_match:
                rec_line = rec_match.group(1).splitlines()[0].strip()
                recommendation = self.clean_recommendation(rec_line)

            return page["page_number"], block_text, recommendation

        return None, "", ""
