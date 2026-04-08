from fastapi import FastAPI, Request
import requests
import hashlib
import boto3
import uuid
import json
import os
import subprocess

app = FastAPI()

BUCKET_NAME = "home-inspection-reports-598120811152-us-east-2-an"
OUTPUT_DIR = "output"
SUBMISSION_CONTEXT_FILE = os.path.join(OUTPUT_DIR, "submission_context.json")

s3 = boto3.client("s3")


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def save_submission_context(payload, s3_key, file_hash):
    ensure_output_dir()

    context = {
        "recordId": payload.get("recordId"),
        "reportId": payload.get("reportId"),
        "submittedAt": payload.get("submittedAt"),
        "submittedBy": payload.get("submittedBy"),
        "propertyAddress": payload.get("propertyAddress"),
        "inspectionDate": payload.get("inspectionDate"),
        "clientName": payload.get("clientName"),
        "clientEmail": payload.get("clientEmail"),
        "clientPhone": payload.get("clientPhone"),
        "additionalComments": payload.get("additionalComments"),
        "additionalServices": payload.get("additionalServices", []),
        "reportUrl": payload.get("reportUrl"),
        "reportFilename": payload.get("reportFilename"),
        "reportLink": payload.get("reportLink"),
        "s3Key": s3_key,
        "sha256": file_hash,
    }

    with open(SUBMISSION_CONTEXT_FILE, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, ensure_ascii=False)

    print(f"Saved submission context to: {SUBMISSION_CONTEXT_FILE}")


@app.get("/")
def root():
    return {"status": "running"}


@app.post("/webhook/fillout")
async def fillout_webhook(request: Request):
    try:
        data = await request.json()
        print("=== WEBHOOK RECEIVED ===")
        print(json.dumps(data, indent=2))

        file_url = data.get("reportUrl")
        filename = data.get("reportFilename", "inspection_report.pdf")

        if not file_url:
            print("ERROR: reportUrl not found in payload")
            return {"status": "error", "message": "reportUrl not found"}

        print("File URL:", file_url)
        print("Original filename:", filename)

        response = requests.get(file_url, timeout=60)
        response.raise_for_status()
        file_bytes = response.content

        print("Downloaded bytes:", len(file_bytes))

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        document_id = str(uuid.uuid4())
        s3_key = f"{document_id}.pdf"

        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=file_bytes,
            ContentType="application/pdf",
            Metadata={
                "original-filename": str(filename),
                "record-id": str(data.get("recordId", "")),
                "report-id": str(data.get("reportId", "")),
                "client-email": str(data.get("clientEmail", "")),
                "property-address": str(data.get("propertyAddress", "")),
                "sha256": file_hash,
            }
        )

        save_submission_context(data, s3_key, file_hash)
        os.makedirs("output", exist_ok=True)
        local_pdf_path = os.path.join("output", "latest_uploaded_report.pdf")

        with open(local_pdf_path, "wb") as f:
            f.write(file_bytes)

        print("Saved local PDF to:", local_pdf_path)

        extract_result = subprocess.run(
            ["python", "extract_findings.py", local_pdf_path],
            capture_output=True,
            text=True
        )
        print("=== EXTRACT FINDINGS OUTPUT ===")
        print(extract_result.stdout)
        if extract_result.stderr:
            print("=== EXTRACT FINDINGS ERRORS ===")
            print(extract_result.stderr)

        if extract_result.returncode != 0:
            raise Exception("extract_findings.py failed")

        build_result = subprocess.run(
            ["python", "build_issue_records.py"],
            capture_output=True,
            text=True
        )
        print("=== BUILD ISSUE RECORDS OUTPUT ===")
        print(build_result.stdout)
        if build_result.stderr:
            print("=== BUILD ISSUE RECORDS ERRORS ===")
            print(build_result.stderr)

        if build_result.returncode != 0:
            raise Exception("build_issue_records.py failed")
        print("UPLOAD SUCCESS")
        print("S3 key:", s3_key)
        print("SHA256:", file_hash)

        return {
            "status": "stored",
            "document_id": document_id,
            "hash": file_hash,
            "s3_key": s3_key,
            "original_filename": filename,
            "recordId": data.get("recordId"),
            "reportId": data.get("reportId"),
        }

    except Exception as e:
        print("WEBHOOK ERROR:", str(e))
        return {"status": "error", "message": str(e)}

from fastapi import FastAPI, Request
import requests
import hashlib
import boto3
import uuid
import json
import os
import subprocess
import tempfile

# keep your existing imports and app = FastAPI()

@app.post("/webhook/adapter-test")
async def adapter_test_webhook(request: Request):
    try:
        data = await request.json()

        report_url = data.get("reportUrl")
        file_name = data.get("fileName", "adapter_test_report.pdf")
        test_record_id = data.get("testRecordId")
        expected_report_family = data.get("expectedReportFamily")
        inspector_company = data.get("inspectorCompany")
        admin_notes = data.get("adminNotes")

        if not report_url:
            return {
                "success": False,
                "message": "reportUrl is required"
            }

        response = requests.get(report_url, timeout=120)
        response.raise_for_status()
        file_bytes = response.content

        os.makedirs("output", exist_ok=True)
        local_pdf_path = os.path.join("output", "adapter_test_report.pdf")

        with open(local_pdf_path, "wb") as f:
            f.write(file_bytes)

        extract_result = subprocess.run(
            ["python", "extract_findings.py", local_pdf_path],
            capture_output=True,
            text=True
        )
        if extract_result.returncode != 0:
            return {
                "success": False,
                "message": "extract_findings.py failed",
                "details": extract_result.stderr
            }

        build_result = subprocess.run(
            ["python", "build_issue_records.py"],
            capture_output=True,
            text=True
        )
        if build_result.returncode != 0:
            return {
                "success": False,
                "message": "build_issue_records.py failed",
                "details": build_result.stderr
            }

        extracted_path = os.path.join("output", "extracted.json")
        issue_records_path = os.path.join("output", "issue_records_v1.json")

        with open(extracted_path, "r", encoding="utf-8") as f:
            extracted = json.load(f)

        with open(issue_records_path, "r", encoding="utf-8") as f:
            issue_records = json.load(f)

        detected_adapter = issue_records[0]["adapter_name"] if issue_records else "unknown"
        page_count = extracted.get("page_count", 0)
        image_count = len(extracted.get("images", []))
        issue_count = len(issue_records)

        extracted_issues = []
        issues_with_images_count = 0

        for issue in issue_records[:50]:
            has_image = bool(issue.get("candidate_image_paths"))
            if has_image:
                issues_with_images_count += 1

            extracted_issues.append({
                "issueCode": issue.get("issue_code"),
                "issueTitle": issue.get("issue_title"),
                "severity": issue.get("report_severity"),
                "system": issue.get("system"),
                "component": issue.get("component"),
                "hasImage": has_image
            })

        extraction_preview = ""
        if issue_records:
            extraction_preview = issue_records[0].get("source_text", "")[:4000]

        return {
            "success": True,
            "testRecordId": test_record_id,
            "detectedAdapter": detected_adapter,
            "pageCount": page_count,
            "imageCount": image_count,
            "issueCount": issue_count,
            "issuesWithImagesCount": issues_with_images_count,
            "extractionPreview": extraction_preview,
            "expectedReportFamily": expected_report_family,
            "inspectorCompany": inspector_company,
            "adminNotes": admin_notes,
            "extractedIssues": extracted_issues
        }

    except Exception as e:
        return {
            "success": False,
            "message": "Adapter test processing failed",
            "details": str(e)
        }



