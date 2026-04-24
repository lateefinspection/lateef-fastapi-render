from fastapi import FastAPI, UploadFile, File
import pdfplumber
import tempfile
import os
from typing import Dict, List

from image_matcher import match_image

# 🔥 NEW: OpenAI
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


# 🔥 AI FUNCTION
def analyze_sections_with_ai(text: str) -> Dict[str, str]:
    """
    Uses OpenAI to detect which inspection sections are present
    and summarize issues.
    """

    prompt = f"""
You are analyzing a home inspection report.

Extract the following sections if they exist:
roof, plumbing, electrical, hvac, foundation

Return ONLY valid JSON like:
{{
  "roof": "summary or null",
  "plumbing": "summary or null",
  "electrical": "summary or null",
  "hvac": "summary or null",
  "foundation": "summary or null"
}}

Inspection text:
{text[:8000]}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    content = response.choices[0].message.content

    try:
        import json
        return json.loads(content)
    except Exception:
        # fallback if AI returns messy output
        return {
            "roof": None,
            "plumbing": None,
            "electrical": None,
            "hvac": None,
            "foundation": None
        }


@app.post("/analyze-report/")
async def analyze_report(file: UploadFile = File(...)):

    # 🔹 Save uploaded PDF
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    # 🔹 Extract text
    page_texts: List[str] = []
    with pdfplumber.open(tmp_path) as pdf:
        for page in pdf.pages:
            page_texts.append(page.extract_text() or "")

    full_text = "\n".join(page_texts)

    # 🔥 STEP 1: AI ANALYSIS
    ai_sections = analyze_sections_with_ai(full_text)

    record_id = "test-record-123"

    # 🔥 STEP 2: IMAGE MATCHING BASED ON AI
    results = {}

    for section, summary in ai_sections.items():
        if summary:  # only include detected sections
            results[section] = {
                "summary": summary,
                "image": match_image(section, record_id)
            }

    os.remove(tmp_path)

    return {
        "sections": results,
        "pages": len(page_texts)
    }
