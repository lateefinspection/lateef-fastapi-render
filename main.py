from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os
import uuid
import boto3
from pdf2image import convert_from_bytes

app = FastAPI()

# AWS CONFIG
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION")
S3_BUCKET = os.getenv("S3_BUCKET_NAME")

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY
)

class ReportRequest(BaseModel):
    reportUrl: str
    recordId: str


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/webhook/adapter-test-upload")
def adapter_test_upload(data: ReportRequest):
    try:
        pdf_url = data.reportUrl
        record_id = data.recordId

        # Download PDF
        response = requests.get(pdf_url)
        if response.status_code != 200:
            return {"error": "Failed to download PDF"}

        pdf_bytes = response.content

        # 🔥 Convert PDF → images (THIS IS THE UPGRADE)
        images = convert_from_bytes(pdf_bytes)

        uploaded_urls = []

        for i, image in enumerate(images[:3]):  # limit to first 3 pages
            file_id = str(uuid.uuid4())
            filename = f"approved-images/{record_id}/{file_id}.png"

            # Save locally
            local_path = f"/tmp/{file_id}.png"
            image.save(local_path, "PNG")

            # Upload to S3
            s3.upload_file(
                local_path,
                S3_BUCKET,
                filename,
                ExtraArgs={"ContentType": "image/png"}
            )

            image_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{filename}"
            uploaded_urls.append(image_url)

        # Build structured response
        def build_section():
            return {
                "issue": "no issues found",
                "imageUrl": uploaded_urls[0] if uploaded_urls else None
            }

        return {
            "output": {
                "roof": build_section(),
                "plumbing": build_section(),
                "electrical": build_section(),
                "hvac": build_section(),
                "foundation": build_section(),
                "summary": "home is in good condition"
            }
        }

    except Exception as e:
        return {"error": str(e)}
