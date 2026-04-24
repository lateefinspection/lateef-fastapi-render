from openai import OpenAI
import os
import json

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def extract_issues_from_text(text: str):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": f"""
You are a professional home inspector AI.

Analyze the following home inspection report text and extract REAL issues.

Return ONLY valid JSON:

{{
  "roof": {{ "issue": "...", "severity": "low|medium|high" }},
  "plumbing": {{ "issue": "...", "severity": "low|medium|high" }},
  "electrical": {{ "issue": "...", "severity": "low|medium|high" }},
  "hvac": {{ "issue": "...", "severity": "low|medium|high" }},
  "foundation": {{ "issue": "...", "severity": "low|medium|high" }},
  "summary": "..."
}}

Rules:
- If no issue → "no issues found"
- Be realistic
- Do not hallucinate

TEXT:
{text[:12000]}
"""
                }
            ],
            temperature=0.2,
        )

        content = response.choices[0].message.content

        # 🔥 FORCE JSON SAFE PARSE
        try:
            return json.loads(content)
        except:
            print("⚠️ JSON parse failed, returning raw")
            return {"raw": content}

    except Exception as e:
        print("🔥 OpenAI ERROR:", str(e))

        return {
            "roof": {"issue": "AI error", "severity": "low"},
            "plumbing": {"issue": "AI error", "severity": "low"},
            "electrical": {"issue": "AI error", "severity": "low"},
            "hvac": {"issue": "AI error", "severity": "low"},
            "foundation": {"issue": "AI error", "severity": "low"},
            "summary": "AI extraction failed"
        }
