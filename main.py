from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from pdf2image import convert_from_bytes
import boto3
import os
import uuid
from pdfminer.high_level import extract_text
import openai
import tempfile
import json

app = FastAPI()

# =========================
# ENV VARIABLES
# =========================
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

# =========================
# S3 CLIENT
# =========================
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_DEFAULT_REGION,
)

# =========================
# REQUEST MODEL
# =========================
class RequestBody(BaseModel):
    reportUrl: str
    recordId: str


# =========================
# AI IMAGE MATCHER
# =========================
def match_images_to_issues(issues, page_texts):
    prompt = f"""
You are an expert home inspection analyst.

We have inspection issues:
{issues}

We also have extracted text from PDF pages:
{page_texts}

Match each issue to the most relevant page index (0-based).

Return ONLY valid JSON like:
{{
  "roof": 0,
  "plumbing": 1,
  "electrical": 2,
  "hvac": 3,
  "foundation": 0
}}
"""

    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    content = response.choices[0].message.content.strip()

    try:
        return json.loads(content)
    except:
        return {
            "roof": 0,
            "plumbing": 0,
            "electrical": 0,
            "hvac": 0,
            "foundation": 0
        }


# =========================
# MAIN ENDPOINT
# =========================
@app.post("/webhook/adapter-test-upload")
async def adapter_test_upload(body: RequestBody):
    try:
        # =========================
        # 1. DOWNLOAD PDF
        # =========================
        pdf_response = requests.get(body.reportUrl)
        if pdf_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to download PDF")

        pdf_bytes = pdf_response.content

        # =========================
        # 2. CONVERT TO IMAGES
        # =========================
        images = convert_from_bytes(pdf_bytes)

        image_urls = []
        page_texts = []

        # =========================
        # 3. PROCESS EACH PAGE
        # =========================
        for i, img in enumerate(images):
            # Save image temp
            img_filename = f"/tmp/{uuid.uuid4()}.png"
            img.save(img_filename, "PNG")

            # Upload to S3
            s3_key = f"approved-images/{body.recordId}/{uuid.uuid4()}.png"

            s3.upload_file(
                img_filename,
                S3_BUCKET_NAME,
                s3_key,
                ExtraArgs={"ContentType": "image/png"},
            )

            image_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_DEFAULT_REGION}.amazonaws.com/{s3_key}"
            image_urls.append(image_url)

            # =========================
            # Extract text from page
            # =========================
            pdf_temp = f"/tmp/{uuid.uuid4()}.pdf"
            img.save(pdf_temp, "PDF")

            try:
                text = extract_text(pdf_temp)
            except:
                text = ""

            page_texts.append(text[:2000])

        # =========================
        # 4. FAKE ISSUES (replace later with real AI output)
        # =========================
        issues = {
            "roof": "no issues found",
            "plumbing": "no issues found",
            "electrical": "no issues found",
            "hvac": "no issues found",
            "foundation": "no issues found"
        }

        # =========================
        # 5. AI MATCHING
        # =========================
        mapping = match_images_to_issues(issues, page_texts)

        # =========================
        # 6. APPLY MAPPING
        # =========================
        def get_image(section):
            idx = mapping.get(section, 0)
            return image_urls[idx] if idx < len(image_urls) else image_urls[0]

        output = {
            "roof": {"issue": issues["roof"], "imageUrl": get_image("roof")},
            "plumbing": {"issue": issues["plumbing"], "imageUrl": get_image("plumbing")},
            "electrical": {"issue": issues["electrical"], "imageUrl": get_image("electrical")},
            "hvac": {"issue": issues["hvac"], "imageUrl": get_image("hvac")},
            "foundation": {"issue": issues["foundation"], "imageUrl": get_image("foundation")},
            "summary": "home is in good condition"
        }

        # =========================
        # FINAL RESPONSE
        # =========================
        return {
            "debug": {
                "imageCount": len(image_urls),
                "mapping": mapping
            },
            "output": output
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
