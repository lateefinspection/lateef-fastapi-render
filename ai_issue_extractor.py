from openai import OpenAI
import os
import json

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def extract_issues_from_text(text: str):
    prompt = f"""
You are a professional home inspector AI.

Analyze the following home inspection report text and extract REAL issues.

Return ONLY valid JSON in this format:

{{
  "roof": {{ "issue": "...", "severity": "low|medium|high" }},
  "plumbing": {{ "issue": "...", "severity": "low|medium|high" }},
  "electrical": {{ "issue": "...", "severity": "low|medium|high" }},
  "hvac": {{ "issue": "...", "severity": "low|medium|high" }},
  "foundation": {{ "issue": "...", "severity": "low|medium|high" }},
  "summary": "..."
}}

Rules:
- If NO issue → say "no issues found"
- If issue exists → describe clearly
- Be realistic like a real inspector
- Do NOT hallucinate extreme problems

TEXT:
{text[:12000]}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
        )

        content = response.choices[0].message.content

        return content

    except Exception as e:
        print("🔥 OpenAI ERROR:", str(e))

        return json.dumps({
            "roof": {"issue": "AI error", "severity": "low"},
            "plumbing": {"issue": "AI error", "severity": "low"},
            "electrical": {"issue": "AI error", "severity": "low"},
            "hvac": {"issue": "AI error", "severity": "low"},
            "foundation": {"issue": "AI error", "severity": "low"},
            "summary": "AI extraction failed"
        })
