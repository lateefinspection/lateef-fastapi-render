import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from pydantic import BaseModel

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


class AdapterTestRequest(BaseModel):
    event: str | None = "adapter_test"
    testRecordId: str | None = None
    reportUrl: str
    fileName: str | None = None
    inputMode: str | None = None
    expectedReportFamily: str | None = None
    inspectorCompany: str | None = None
    adminNotes: str | None = None


def run_command(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        details = e.stderr or e.stdout or str(e)
        return False, details


def load_issue_records() -> list[dict]:
    issue_file = OUTPUT_DIR / "issue_records_v1.json"
    if not issue_file.exists():
        return []
    with open(issue_file, "r", encoding="utf-8") as f:
        return json.load(f)


def build_response(
    test_record_id: str | None,
    extracted_issues: list[dict],
) -> dict:
    extracted = []
    for item in extracted_issues:
        extracted.append(
            {
                "issueCode": item.get("issue_code"),
                "issueTitle": item.get("issue_title"),
                "severity": item.get("report_severity"),
                "system": item.get("system"),
                "component": item.get("component"),
                "hasImage": bool(item.get("verified_image_path")),
            }
        )

    detected_adapter = extracted_issues[0].get("adapter_name") if extracted_issues else "unknown"
    page_count = None
    image_count = None
    try:
        with open(OUTPUT_DIR / "extracted.json", "r", encoding="utf-8") as f:
            extracted_json = json.load(f)
            page_count = extracted_json.get("page_count", 0)
            images = extracted_json.get("images", [])
            image_count = len(images)
    except Exception:
        page_count = 0
        image_count = 0

    return {
        "success": True,
        "testRecordId": test_record_id,
        "detectedAdapter": detected_adapter,
        "pageCount": page_count or 0,
        "imageCount": image_count or 0,
        "issueCount": len(extracted_issues),
        "issuesWithImagesCount": sum(1 for x in extracted_issues if x.get("verified_image_path")),
        "extractionPreview": "",
        "expectedReportFamily": None,
        "inspectorCompany": None,
        "adminNotes": None,
        "extractedIssues": extracted,
    }


@app.get("/")
def root():
    return {"status": "running"}


@app.post("/webhook/adapter-test")
def adapter_test(payload: AdapterTestRequest):
    if not payload.reportUrl:
        return {"success": False, "message": "reportUrl is required"}

    test_record_id = payload.testRecordId or str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        pdf_path = tmp_path / (payload.fileName or "report.pdf")

        download_cmd = [
            "python",
            "-c",
            (
                "import requests,sys; "
                "url=sys.argv[1]; out=sys.argv[2]; "
                "r=requests.get(url, timeout=120); "
                "r.raise_for_status(); "
                "open(out,'wb').write(r.content)"
            ),
            payload.reportUrl,
            str(pdf_path),
        ]
        ok, details = run_command(download_cmd, BASE_DIR)
        if not ok:
            return {
                "success": False,
                "message": "Adapter test processing failed",
                "details": f"Download failed: {details}",
            }

        ok, details = run_command(["python", "extract_findings.py", str(pdf_path)], BASE_DIR)
        if not ok:
            return {
                "success": False,
                "message": "Adapter test processing failed",
                "details": details,
            }

        ok, details = run_command(["python", "build_issue_records.py"], BASE_DIR)
        if not ok:
            return {
                "success": False,
                "message": "build_issue_records.py failed",
                "details": details,
            }

        issues = load_issue_records()
        return build_response(test_record_id, issues)


@app.post("/webhook/adapter-test-upload")
async def adapter_test_upload(
    file: UploadFile = File(...),
    testRecordId: str | None = Form(default=None),
    expectedReportFamily: str | None = Form(default=None),
    inspectorCompany: str | None = Form(default=None),
    adminNotes: str | None = Form(default=None),
):
    if not file.filename:
        return {"success": False, "message": "Uploaded file must have a filename"}

    test_record_id = testRecordId or str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        pdf_path = tmp_path / file.filename

        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        ok, details = run_command(["python", "extract_findings.py", str(pdf_path)], BASE_DIR)
        if not ok:
            return {
                "success": False,
                "message": "Adapter test processing failed",
                "details": details,
                "testRecordId": test_record_id,
            }

        ok, details = run_command(["python", "build_issue_records.py"], BASE_DIR)
        if not ok:
            return {
                "success": False,
                "message": "build_issue_records.py failed",
                "details": details,
                "testRecordId": test_record_id,
            }

        issues = load_issue_records()
        response = build_response(test_record_id, issues)
        response["expectedReportFamily"] = expectedReportFamily
        response["inspectorCompany"] = inspectorCompany
        response["adminNotes"] = adminNotes
        return response
