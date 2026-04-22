import os
import glob
from pathlib import Path
import boto3
from openai import OpenAI

# ===== CONFIG =====
S3_BUCKET = os.getenv("BUCKET_NAME")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-2")
S3_PREFIX = "adapter-test-images"

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
s3_client = boto3.client("s3", region_name=AWS_REGION)


# ===== HELPERS =====

def _dedupe(paths):
    seen = set()
    result = []
    for p in paths:
        if not p:
            continue
        name = os.path.basename(p)
        if name not in seen:
            seen.add(name)
            result.append(p)
    return result


# ===== IMAGE COLLECTION =====

def collect_images_by_page(extracted):
    images_by_page = {}

    def add(page, path):
        if page is None or not path:
            return
        images_by_page.setdefault(page, [])
        images_by_page[page].append(path)
        images_by_page[page] = _dedupe(images_by_page[page])

    for file_path in glob.glob(str(OUTPUT_DIR / "images" / "*")):
        filename = os.path.basename(file_path).lower()
        page = None

        if "page_" in filename:
            try:
                page = int(filename.split("page_")[1].split("_")[0])
            except:
                pass

        add(page, file_path)

    return images_by_page


# ===== S3 =====

def upload_and_get_url(local_path):
    if not local_path or not os.path.exists(local_path):
        return None

    if not S3_BUCKET:
        print("❌ BUCKET_NAME not set")
        return None

    try:
        filename = os.path.basename(local_path)
        key = f"{S3_PREFIX}/{filename}"

        s3_client.upload_file(local_path, S3_BUCKET, key)

        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=3600,
        )

        return url

    except Exception as e:
        print("❌ S3 upload failed:", str(e))
        return None


# ===== FALLBACK =====

def fallback_match(issue_title, candidates):
    if not candidates:
        return None
    idx = abs(hash(issue_title)) % len(candidates)
    return candidates[idx]


# ===== MAIN MATCH =====

def match_images_for_issue(issue_title, summary_page, images_by_page):
    if not summary_page:
        return [], [], None

    same = images_by_page.get(summary_page, [])
    prev_page = images_by_page.get(summary_page - 1, [])
    next_page = images_by_page.get(summary_page + 1, [])

    all_candidates = _dedupe(same + prev_page + next_page)

    if not all_candidates:
        return [], [], None

    candidates = all_candidates[:5]

    urls = []
    local_for_ai = []

    for path in candidates:
        url = upload_and_get_url(path)
        if url:
            urls.append(url)
            local_for_ai.append(path)

    if len(urls) < 2:
        return same, all_candidates, fallback_match(issue_title, all_candidates)

    content = [{
        "type": "input_text",
        "text": (
            "You are reviewing home inspection images.\n\n"
            f"Issue: {issue_title}\n\n"
            "Carefully compare ALL images.\n"
            "Choose the ONE image that most clearly and specifically shows this exact issue.\n\n"
            "Do NOT choose a general image.\n"
            "Focus on visible evidence of the issue.\n\n"
            "Return ONLY the index number (0-based)."
        )
    }]

    for url in urls:
        content.append({
            "type": "input_image",
            "image_url": url
        })

    try:
        response = openai_client.responses.create(
            model="gpt-4o-mini",
            input=[{
                "role": "user",
                "content": content
            }],
            max_output_tokens=20
        )

        output = (response.output_text or "").strip()
        digits = "".join(c for c in output if c.isdigit())

        if digits:
            idx = int(digits)
            if 0 <= idx < len(local_for_ai):
                return same, all_candidates, local_for_ai[idx]

    except Exception as e:
        print("⚠️ AI matching failed:", str(e))

    return same, all_candidates, fallback_match(issue_title, all_candidates)
