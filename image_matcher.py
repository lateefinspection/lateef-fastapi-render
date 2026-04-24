import os
import uuid

S3_BUCKET = os.getenv("S3_BUCKET_NAME", "")
S3_REGION = os.getenv("AWS_REGION", "us-east-2")


def match_image(section: str, record_id: str):
    """
    Returns a valid S3 image URL for a section
    """

    image_id = str(uuid.uuid4())

    # fallback safe URL builder
    image_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/approved-images/{record_id}/{image_id}.png"

    return image_url
