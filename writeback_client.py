import requests
from datetime import datetime
import boto3
import os
import mimetypes

# n8n webhook
N8N_WEBHOOK_URL = "https://lateefinspection.app.n8n.cloud/webhook/receive-approved-issue"

# S3 config
S3_BUCKET_NAME = "home-inspection-reports-598120811152-us-east-2-an"
AWS_REGION = "us-east-2"

s3 = boto3.client("s3")


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def upload_verified_image_to_s3(local_path: str, record_id: str, issue_code: str) -> str | None:
    """
    Upload the selected verified image to S3 and return a public URL.
    """
    if not local_path:
        print("No local image path provided.")
        return None

    if not os.path.exists(local_path):
        print("Verified image file not found:", local_path)
        return None

    try:
        file_ext = os.path.splitext(local_path)[1].lower() or ".jpeg"
        mime_type = mimetypes.guess_type(local_path)[0] or "image/jpeg"

        s3_key = f"approved-images/{record_id}/{issue_code}{file_ext}"

        with open(local_path, "rb") as f:
            s3.upload_fileobj(
                f,
                S3_BUCKET_NAME,
                s3_key,
                ExtraArgs={
                    "ContentType": mime_type
                }
            )

        public_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        print("Uploaded image to S3:", public_url)
        return public_url

    except Exception as e:
        print("ERROR uploading image to S3:", str(e))
        return None


def send_verified_issue(issue_record: dict) -> dict:
    """
    Sends one approved issue from FastAPI to n8n.
    Returns a dict so the caller can inspect success/failure.
    """
    try:
        image_url = upload_verified_image_to_s3(
            issue_record.get("verified_image_path"),
            issue_record.get("record_id"),
            issue_record.get("issue_code")
        )

        payload = {
            "recordId": issue_record.get("record_id"),
            "reportId": issue_record.get("report_number"),
            "status": "Admin Acknowledged",
            "adminNotes": "Approved via review system",
            "verifiedIssue": {
                "issueCode": issue_record.get("issue_code"),
                "severity": issue_record.get("report_severity"),
                "description": issue_record.get("issue_title"),
                "verified": True,
                "verifiedAt": now_iso(),
                "imageUrl": image_url,
                "system": issue_record.get("system"),
                "component": issue_record.get("component"),
                "homeownerSummary": issue_record.get("homeowner_summary"),
                "whyItMatters": issue_record.get("why_it_matters"),
                "nextAction": issue_record.get("next_action"),
            },
        }

        response = requests.post(
            N8N_WEBHOOK_URL,
            json=payload,
            timeout=10,
        )

        print("=== SEND TO N8N ===")
        print("Payload:", payload)
        print("Response:", response.status_code, response.text)

        return {
            "ok": response.status_code == 200,
            "status_code": response.status_code,
            "response_text": response.text,
            "payload": payload,
        }

    except Exception as e:
        print("ERROR sending to n8n:", str(e))
        return {
            "ok": False,
            "message": str(e),
        }
