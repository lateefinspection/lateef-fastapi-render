from fastapi import FastAPI
from pydantic import BaseModel
import requests
import pdfplumber
import os
import boto3
import uuid

app = FastAPI()


# =========================
# CONFIG
# =========================
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION")
S3_BUCKET = os.getenv("S3_BUCKET_NAME")

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)


class ReportRequest(BaseModel):
    reportUrl: str
    recordId: str


@app.get("/")
def health():
    return {"status": "ok"}


# =========================
# S3 TEST
# =========================
@app.get("/test-s3")
def test_s3():
    try:
        s3.list_buckets()
        return {
            "success": True,
            "message": "S3 connection successful",
            "bucket": S3_BUCKET
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================
# MAIN PIPELINE
# =========================
@app.post("/webhook/adapter-test-upload")
def adapter_test_upload(data: ReportRequest):
    try:
        pdf_url = data.reportUrl
        record_id = data.recordId

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

        # -------------------------
        # FAKE IMAGE GENERATION (TEMP)
        # -------------------------
        # We simulate an "image" upload for now
        image_key = f"approved-images/{record_id}/{uuid.uuid4()}.txt"

        temp_image_path = "temp_image.txt"
        with open(temp_image_path, "w") as f:
            f.write("placeholder image data")

        s3.upload_file(temp_image_path, S3_BUCKET, image_key)

        image_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{image_key}"

        # -------------------------
        # RESPONSE
        # -------------------------
        return {
            "output": {
                "roof": {
                    "issue": "no issues found",
                    "imageUrl": image_url
                },
                "plumbing": {
                    "issue": "no issues found",
                    "imageUrl": image_url
                },
                "electrical": {
                    "issue": "no issues found",
                    "imageUrl": image_url
                },
                "hvac": {
                    "issue": "no issues found",
                    "imageUrl": image_url
                },
                "foundation": {
                    "issue": "no issues found",
                    "imageUrl": image_url
                },
                "summary": "home is in good condition"
            }
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
