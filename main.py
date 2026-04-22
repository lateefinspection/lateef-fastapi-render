from fastapi import FastAPI
from pydantic import BaseModel
import requests
import pdfplumber

app = FastAPI()


class ReportRequest(BaseModel):
    reportUrl: str


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/webhook/adapter-test-upload")
def adapter_test_upload(data: ReportRequest):
    try:
        pdf_url = data.reportUrl

        # Download PDF
        response = requests.get(pdf_url)
        if response.status_code != 200:
            return {"success": False, "error": "Failed to download PDF"}

        file_path = "temp_report.pdf"
        with open(file_path, "wb") as f:
            f.write(response.content)

        # Extract text
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""

        return {
            "success": True,
            "message": "PDF processed",
            "textPreview": text[:500]  # first 500 chars
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
