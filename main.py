from fastapi import FastAPI
from pydantic import BaseModel
import requests
from typing import List, Dict, Any

# Import your parser
from extract_findings import extract_findings

app = FastAPI()

# -------------------------------
# Health check (IMPORTANT for Render)
# -------------------------------
@app.get("/")
def health():
    return {"status": "ok"}

# -------------------------------
# Request schema
# -------------------------------
class ReportRequest(BaseModel):
    reportUrl: str

# -------------------------------
# MAIN ENDPOINT
# -------------------------------
@app.post("/webhook/adapter-test-upload")
async def adapter_test_upload(data: ReportRequest):
    try:
        print(f"Received report URL: {data.reportUrl}")

        # Step 1: Download PDF
        response = requests.get(data.reportUrl)
        if response.status_code != 200:
            return {
                "success": False,
                "error": "Failed to download PDF"
            }

        pdf_bytes = response.content

        # Step 2: Extract findings
        findings = extract_findings(pdf_bytes)

        return {
            "success": True,
            "issueCount": len(findings),
            "findings": findings
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
