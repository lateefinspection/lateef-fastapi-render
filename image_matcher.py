import os
import uuid

S3_BUCKET = os.getenv("S3_BUCKET_NAME", "home-inspection-reports-598120811152-us-east-2-an")
S3_REGION = os.getenv("AWS_REGION", "us-east-2")


def match_image(section: str, record_id: str):
    image_id = str(uuid.uuid4())

    return (
        f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/"
        f"approved-images/{record_id}/{image_id}.png"
    )
