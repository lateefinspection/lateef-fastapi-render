from fastapi import FastAPI, UploadFile, File
import pdfplumber
import tempfile
import os
from typing import List

from image_matcher import match_image

# 🔥 OpenAI
from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


# 🔥 AI FUNCTION
def analyze_sections_with_ai(text: str):
    import json

    trimmed_text = text[:6000]

    prompt = f"""
You are an expert home inspection analyzer.

Analyze the report and extract findings for these sections ONLY:
roof, plumbing, electrical, hvac, foundation

Rules:
- If a section is NOT mentioned → return null
- If it IS mentioned → return a SHORT summary (1 sentence)
- DO NOT add extra keys
- RETURN JSON ONLY

FORMAT:

{{
  "roof": "summary or null",
  "plumbing": "summary or null",
  "electrical": "summary or null",
  "hvac": "summary or null",
  "foundation": "summary or null"
}}

REPORT:
{trimmed_text}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    content = response.choices[0].message.content.strip()

    # 🔍 DEBUG LOGGING
    print("\n🔥 RAW AI RESPONSE:\n", content, "\n")

    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        cleaned = content[start:end]
        parsed = json.loads(cleaned)

        return {
            "roof": parsed.get("roof"),
            "plumbing": parsed.get("plumbing"),
            "electrical": parsed.get("electrical"),
            "hvac": parsed.get("hvac"),
            "foundation": parsed.get("foundation"),
        }

    except Exception as e:
        print("❌ JSON PARSE FAILED:", e)

        return {
            "roof": "Inspection detected",
            "plumbing": "Inspection detected",
            "electrical": "Inspection detected",
            "hvac": "Inspection detected",
            "foundation": "Inspection detected"
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

    # 🔥 STEP 2: IMAGE MATCHING
    results = {}

    for section, summary in ai_sections.items():
        if summary:
            results[section] = {
                "summary": summary,
                "image": match_image(section, record_id)
            }

    os.remove(tmp_path)

    return {
        "sections": results,
        "pages": len(page_texts)
    }
