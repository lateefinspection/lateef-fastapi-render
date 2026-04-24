from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os
import uuid
import boto3
from pdf2image import convert_from_bytes

app = FastAPI()

# =========================
# ENV CONFIG
# =========================
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

# =========================
# REQUEST MODEL
# =========================
class ReportRequest(BaseModel):
    reportUrl: str
    recordId: str

# =========================
# HEALTH CHECK
# =========================
@app.get("/")
def health():
    return {"status": "ok"}

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
            return {"error": "Failed to download PDF"}

        pdf_bytes = response.content

        # -------------------------
        # CONVERT PDF → IMAGES
        # -------------------------
        images = convert_from_bytes(pdf_bytes)

        uploaded_urls = []

        # -------------------------
        # UPLOAD IMAGES TO S3
        # -------------------------
        for i, image in enumerate(images):
            file_id = str(uuid.uuid4())
            filename = f"approved-images/{record_id}/{file_id}.png"

            local_path = f"/tmp/{file_id}.png"
            image.save(local_path, "PNG")

            s3.upload_file(
                local_path,
                S3_BUCKET,
                filename,
                ExtraArgs={"ContentType": "image/png"}
            )

            image_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{filename}"
            uploaded_urls.append(image_url)

        # -------------------------
        # SAFE IMAGE MAPPING
        # -------------------------
        def get_image(index):
            if uploaded_urls and len(uploaded_urls) > index:
                return uploaded_urls[index]
            elif uploaded_urls:
                return uploaded_urls[0]
            return None

        # -------------------------
        # RESPONSE STRUCTURE
        # -------------------------
        return {
            "output": {
                "roof": {
                    "issue": "no issues found",
                    "imageUrl": get_image(0)
                },
                "plumbing": {
                    "issue": "no issues found",
                    "imageUrl": get_image(1)
                },
                "electrical": {
                    "issue": "no issues found",
                    "imageUrl": get_image(2)
                },
                "hvac": {
                    "issue": "no issues found",
                    "imageUrl": get_image(3)
                },
                "foundation": {
                    "issue": "no issues found",
                    "imageUrl": get_image(4)
                },
                "summary": "home is in good condition"
            }
        }

    except Exception as e:
        return {"error": str(e)}
