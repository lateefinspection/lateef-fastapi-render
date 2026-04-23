from fastapi import FastAPI
from pydantic import BaseModel
import requests
import pdfplumber
import os
import boto3
from botocore.exceptions import NoCredentialsError

app = FastAPI()


# =========================
# MODELS
# =========================

class ReportRequest(BaseModel):
    reportUrl: str


# =========================
# HEALTH CHECK
# =========================

@app.get("/")
def health():
    return {"status": "ok"}


# =========================
# MAIN PDF PROCESSOR
# =========================

@app.post("/webhook/adapter-test-upload")
def adapter_test_upload(data: ReportRequest):
    try:
        pdf_url = data.reportUrl

        # -------------------------
        # DOWNLOAD PDF
        # -------------------------
        response = requests.get(pdf_url)
        if response.status_code != 200:
            return {"success": False, "error": "Failed to download PDF"}

        file_path = "temp_report.pdf"
        with open(file_path, "wb") as f:
            f.write(response.content)

        # -------------------------
        # EXTRACT TEXT
        # -------------------------
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""

        return {
            "success": True,
            "message": "PDF processed",
            "textPreview": text[:500]
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================
# S3 CONNECTION TEST
# =========================

@app.get("/test-s3")
def test_s3():
    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_DEFAULT_REGION")
        )

        bucket_name = os.getenv("S3_BUCKET_NAME")

        # Test access
        s3.list_objects_v2(Bucket=bucket_name, MaxKeys=1)

        return {
            "success": True,
            "message": "S3 connection successful",
            "bucket": bucket_name
        }

    except NoCredentialsError:
        return {"success": False, "error": "Invalid AWS credentials"}

    except Exception as e:
        return {"success": False, "error": str(e)}
