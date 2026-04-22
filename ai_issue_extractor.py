import os
import json
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def extract_issues_with_ai(full_text):
    """
    Uses AI to extract real inspection issues only, including page numbers.
    The input text should already include page markers like:
    === PAGE 4 ===
    """

    prompt = f"""
You are a professional home inspector.

Extract ONLY real inspection issues from the report text.

STRICT RULES:
- Ignore disclaimers
- Ignore general explanations
- Ignore boilerplate language
- Ignore generic inspection limitations
- Only include real defects, deficiencies, or actionable problems
- Use the page markers to determine the page number for each issue

Return JSON ONLY in this exact format:

[
  {{
    "issue_title": "...",
    "summary": "...",
    "system": "Roof/Plumbing/Electrical/etc",
    "severity": "low/medium/high",
    "page_number": 4
  }}
]

REQUIREMENTS:
- page_number must be an integer
- system should be short and useful
- severity must be only: low, medium, or high
- issue_title should be concise
- summary should be homeowner-readable

TEXT:
{full_text[:20000]}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )

    content = response.choices[0].message.content or ""

    # Remove markdown code fences if present
    if "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
            content = content.replace("json", "", 1).strip()

    try:
        data = json.loads(content)
        if isinstance(data, list):
            cleaned = []
            for item in data:
                if not isinstance(item, dict):
                    continue

                page_number = item.get("page_number")
                try:
                    page_number = int(page_number) if page_number is not None else None
                except Exception:
                    page_number = None

                cleaned.append({
                    "issue_title": item.get("issue_title", ""),
                    "summary": item.get("summary", ""),
                    "system": item.get("system", ""),
                    "severity": item.get("severity", "medium"),
                    "page_number": page_number,
                })
            return cleaned

        print("⚠️ AI response was not a list.")
        print(content)
        return []

    except Exception:
        print("⚠️ Failed to parse AI response, raw output:")
        print(content)
        return []
