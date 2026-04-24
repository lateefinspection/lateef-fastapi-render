from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import json
import traceback

# Internal modules
from extract_findings import extract_text_from_pdf
from ai_issue_extractor import extract_issues_from_text
from normalizers import normalize_issues
from image_matcher import match_image

app = FastAPI()


# -------------------------------
# REQUEST MODEL
# -------------------------------
class IntakeRequest(BaseModel):
    reportUrl: str
    recordId: str


# -------------------------------
# HEALTH CHECK (for Render)
# -------------------------------
@app.get("/")
def health():
    return {"status": "ok", "service": "HomeFax AI Engine"}


# -------------------------------
# MAIN PIPELINE
# -------------------------------
@app.post("/webhook/adapter-test-upload")
def process_report(request: IntakeRequest):
    try:
        report_url = request.reportUrl
        record_id = request.recordId

        print(f"\n📥 Processing report: {record_id}")

        # -------------------------------
        # STEP 1: DOWNLOAD PDF
        # -------------------------------
        response = requests.get(report_url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to download PDF")

        pdf_bytes = response.content

        # -------------------------------
        # STEP 2: EXTRACT TEXT
        # -------------------------------
        extracted_text = extract_text_from_pdf(pdf_bytes)

        if not extracted_text or len(extracted_text.strip()) < 50:
            print("⚠️ Low text extracted — continuing anyway")

        # -------------------------------
        # STEP 3: AI ISSUE EXTRACTION
        # -------------------------------
        ai_raw = extract_issues_from_text(extracted_text)

        try:
            ai_data = json.loads(ai_raw)
        except Exception:
            print("⚠️ AI returned invalid JSON — fallback triggered")
            ai_data = {
                "roof": {"issue": "no issues found", "severity": "low"},
                "plumbing": {"issue": "no issues found", "severity": "low"},
                "electrical": {"issue": "no issues found", "severity": "low"},
                "hvac": {"issue": "no issues found", "severity": "low"},
                "foundation": {"issue": "no issues found", "severity": "low"},
                "summary": "AI parsing failed"
            }

        # -------------------------------
        # STEP 4: NORMALIZE STRUCTURE
        # -------------------------------
        normalized = normalize_issues(ai_data)

        # -------------------------------
        # STEP 5: IMAGE MATCHING
        # -------------------------------
        output = {}

        for section, data in normalized.items():

            # summary handled separately
            if section == "summary":
                output["summary"] = data
                continue

            issue_text = data.get("issue", "no issues found")
            severity = data.get("severity", "low")

            output[section] = {
                "issue": issue_text,
                "severity": severity,
                "imageUrl": match_image(section, record_id)
            }

        print("✅ Processing complete")

        return {
            "output": output
        }

    except Exception as e:
        print("🔥 ERROR:", str(e))
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=f"Processing failed: {str(e)}"
        )
