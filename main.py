from fastapi import FastAPI, HTTPException
import requests
import uuid

from extract_findings import extract_text_from_pdf
from ai_issue_extractor import extract_issues_from_text
from image_matcher import match_images

app = FastAPI()


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/webhook/adapter-test-upload")
def process_report(data: dict):
    try:
        report_url = data.get("reportUrl")
        record_id = data.get("recordId", str(uuid.uuid4()))

        if not report_url:
            raise HTTPException(status_code=400, detail="Missing reportUrl")

        print(f"📥 Processing report: {report_url}")

        # Step 1: Download PDF
        pdf_bytes = requests.get(report_url).content

        # Step 2: Extract text
        text = extract_text_from_pdf(pdf_bytes)

        print(f"📄 Extracted text length: {len(text)}")

        # Step 3: AI issue extraction
        issues = extract_issues_from_text(text)

        print(f"🤖 AI Issues: {issues}")

        # Step 4: Image matching
        matched_images = match_images(pdf_bytes, issues, record_id)

        print(f"🖼️ Matched images: {matched_images}")

        # Step 5: Combine output
        output = {}

        for system in ["roof", "plumbing", "electrical", "hvac", "foundation"]:
            output[system] = {
                "issue": issues.get(system, {}).get("issue", "no issues found"),
                "imageUrl": matched_images.get(system)
            }

        output["summary"] = issues.get("summary", "")

        return {"output": output}

    except Exception as e:
        print("🔥 ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))
