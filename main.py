from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os

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

        # Step 1: Download PDF
        response = requests.get(pdf_url)
        if response.status_code != 200:
            return {"success": False, "error": "Failed to download PDF"}

        # Step 2: Save PDF temporarily
        file_path = "temp_report.pdf"
        with open(file_path, "wb") as f:
            f.write(response.content)

        # Step 3: Confirm saved
        file_size = os.path.getsize(file_path)

        return {
            "success": True,
            "message": "PDF downloaded successfully",
            "fileSize": file_size
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
