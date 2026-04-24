from fastapi import FastAPI, Request
import requests
import tempfile
import os
import uuid
import json
import subprocess

from ai_issue_extractor import extract_issues_from_text
from image_matcher import match_image

app = FastAPI()


# -----------------------------
# Helper: Download PDF
# -----------------------------
def download_pdf(url: str) -> str:
    response = requests.get(url)
    response.raise_for_status()

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp_file.write(response.content)
    temp_file.close()

    return temp_file.name


# -----------------------------
# Helper: Extract text from PDF
# -----------------------------
def extract_text_from_pdf(pdf_path: str) -> str:
    try:
        output = subprocess.check_output(
            ["pdftotext", pdf_path, "-"],
            stderr=subprocess.DEVNULL
        )
        return output.decode("utf-8", errors="ignore")
    except Exception as e:
        print("PDF extraction error:", str(e))
        return ""


# -----------------------------
# Health check
# -----------------------------
@app.get("/")
def health():
    return {"status": "running"}


# -----------------------------
# MAIN ENDPOINT
# -----------------------------
@app.post("/webhook/adapter-test-upload")
async def adapter_test_upload(request: Request):
    try:
        body = await request.json()

        report_url = body.get("reportUrl")
        record_id = body.get("recordId", str(uuid.uuid4()))

        if not report_url:
            return {"error": "Missing reportUrl"}

        print(f"\n📄 Processing report: {report_url}")
        print(f"🆔 Record ID: {record_id}")

        # -----------------------------
        # Step 1: Download PDF
        # -----------------------------
        pdf_path = download_pdf(report_url)

        # -----------------------------
        # Step 2: Extract Text
        # -----------------------------
        text = extract_text_from_pdf(pdf_path)

        if not text.strip():
            print("⚠️ No text extracted from PDF")

        # -----------------------------
        # Step 3: AI Issue Extraction
        # -----------------------------
        ai_response = extract_issues_from_text(text)

        print("🧠 Raw AI response:", ai_response)

        try:
            parsed = json.loads(ai_response)
        except Exception as e:
            print("❌ JSON parse error:", str(e))
            parsed = {
                "roof": {"issue": "parse error", "severity": "low"},
                "plumbing": {"issue": "parse error", "severity": "low"},
                "electrical": {"issue": "parse error", "severity": "low"},
                "hvac": {"issue": "parse error", "severity": "low"},
                "foundation": {"issue": "parse error", "severity": "low"},
                "summary": "AI parsing failed"
            }

        # -----------------------------
        # Step 4: Image Matching
        # -----------------------------
        for system in ["roof", "plumbing", "electrical", "hvac", "foundation"]:
            try:
                image_url = match_image(system, record_id)
                parsed[system]["imageUrl"] = image_url
            except Exception as e:
                print(f"⚠️ Image match failed for {system}:", str(e))
                parsed[system]["imageUrl"] = None

        # -----------------------------
        # Step 5: Cleanup temp file
        # -----------------------------
        try:
            os.remove(pdf_path)
        except:
            pass

        # -----------------------------
        # Final Response
        # -----------------------------
        return {
            "output": parsed
        }

    except Exception as e:
        print("🔥 CRITICAL ERROR:", str(e))
        return {
            "error": str(e)
        }
