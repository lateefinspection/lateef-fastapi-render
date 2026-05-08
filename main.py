# =========================
# HomeFax FastAPI Backend
# Main API Service
# =========================

import os
import re
import uuid
import secrets
import hashlib
import hmac
from io import BytesIO
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import pymysql
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel


# =========================
# ENV SETUP
# =========================

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "homefax_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "homefax")

APP_ENV = os.getenv("APP_ENV", "development")


# =========================
# FASTAPI APP
# =========================

app = FastAPI(
    title="HomeFax AI Backend",
    description="HomeFax AI ingestion, parser, automation, and alert backend.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# DATABASE CONNECTION
# =========================

def get_db_connection():
    """
    Central MySQL/RDS connection helper.

    Uses Render/AWS environment variables:
    DB_HOST
    DB_PORT
    DB_USER
    DB_PASSWORD
    DB_NAME
    """
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )


# =========================
# PYDANTIC MODELS
# =========================

class Finding(BaseModel):
    type: Optional[str] = "unknown"
    severity: Optional[str] = "low"
    location: Optional[str] = "unknown"
    notes: Optional[str] = ""


class InspectionProcessRequest(BaseModel):
    record_id: str
    findings: List[Finding]


# =========================
# BASIC ROUTES
# =========================

@app.get("/")
def root():
    return {
        "success": True,
        "service": "HomeFax AI Backend",
        "environment": APP_ENV,
        "db_host": DB_HOST,
        "db_name": DB_NAME,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "homefax-fastapi",
    }


@app.get("/db-health")
def db_health():
    """
    Confirms that Render/FastAPI can reach AWS RDS.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 AS ok")
        row = cursor.fetchone()

        cursor.close()
        conn.close()

        return {
            "status": "ok",
            "db_connected": True,
            "db_host": DB_HOST,
            "db_name": DB_NAME,
            "result": row,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database connection failed: {str(e)}",
        )


# =========================
# NOTIFICATION STUB
# =========================

def notify_record_owner(record_id: str, subject: str, message: str):
    """
    Placeholder notification function.

    This keeps /process-inspection stable even before email/SMS notification
    is fully connected.
    """
    print("NOTIFY_RECORD_OWNER")
    print("record_id:", record_id)
    print("subject:", subject)
    print("message:", message)


# =========================
# AI INGESTION — JSON FROM N8N
# =========================

@app.post("/process-inspection")
def process_inspection(data: InspectionProcessRequest):
    """
    Receives normalized findings from n8n and creates:

    1. alerts
    2. automation_tasks

    Expected JSON:
    {
      "record_id": "string",
      "findings": [
        {
          "type": "water_leak",
          "severity": "high",
          "location": "basement",
          "notes": "Active leak under stairs"
        }
      ]
    }
    """

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        record_id = (data.record_id or "").strip()

        if not record_id:
            raise HTTPException(status_code=400, detail="record_id is required")

        findings = data.findings or []

        alerts_created = 0
        tasks_created = 0
        processed_findings = []

        for f in findings:
            finding_type = (f.type or "unknown").lower().strip()
            finding_type = re.sub(r"\s+", "_", finding_type)

            severity = (f.severity or "low").lower().strip()
            location = (f.location or "unknown").lower().strip()
            notes = f.notes or ""

            if severity not in ["low", "medium", "high", "critical"]:
                severity = "low"

            alert_key = f"{record_id}:{finding_type}:{location}".lower()

            cursor.execute(
                """
                INSERT IGNORE INTO alerts
                (record_id, alert_type, severity, message, dedupe_key, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (
                    record_id,
                    finding_type,
                    severity,
                    notes,
                    alert_key,
                ),
            )

            new_alert = cursor.rowcount > 0

            if new_alert:
                alerts_created += 1

                if severity in ["high", "critical"]:
                    notify_record_owner(
                        record_id,
                        f"HomeFax Alert: {finding_type}",
                        f"{finding_type} detected at {location}. {notes}",
                    )

            task_key = f"{alert_key}:task"

            cursor.execute(
                """
                INSERT IGNORE INTO automation_tasks
                (
                    record_id,
                    task_type,
                    priority,
                    title,
                    description,
                    recommended_trade,
                    status,
                    source,
                    dedupe_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'open', 'ai_ingestion', %s)
                """,
                (
                    record_id,
                    finding_type,
                    severity,
                    f"{finding_type} issue",
                    notes,
                    "general_home_service",
                    task_key,
                ),
            )

            if cursor.rowcount > 0:
                tasks_created += 1

            processed_findings.append(
                {
                    "type": finding_type,
                    "severity": severity,
                    "location": location,
                    "notes": notes,
                    "alert_key": alert_key,
                    "task_key": task_key,
                }
            )

        conn.commit()

        return {
            "success": True,
            "record_id": record_id,
            "findings_count": len(findings),
            "alerts_created": alerts_created,
            "tasks_created": tasks_created,
            "processed_findings": processed_findings,
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("ERROR IN /process-inspection:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# =========================
# PDF PARSER HELPERS
# =========================

def _safe_slug(value: str) -> str:
    value = value or "inspection"
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:40] or "inspection"


def _make_pdf_record_id(filename: str) -> str:
    """
    Creates a unique record ID for PDF parser ingestion.

    Later, we can replace this with a real record_id from the intake form,
    property record, uploaded report metadata, or database.
    """
    base = _safe_slug(filename.rsplit(".", 1)[0] if filename else "inspection")
    suffix = uuid.uuid4().hex[:8]
    return f"pdf-{base}-{suffix}"


def _clean_line(line: str) -> str:
    line = line or ""
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def _extract_pdf_pages_from_bytes(content: bytes) -> List[dict]:
    """
    Extract text from PDF bytes.

    Preferred:
      PyMuPDF / fitz

    Fallback:
      pypdf

    Make sure requirements.txt includes at least:
      pypdf==5.1.0
    """
    pages = []

    # Try PyMuPDF first if installed.
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=content, filetype="pdf")
        for idx, page in enumerate(doc):
            text = page.get_text("text") or ""
            pages.append(
                {
                    "page": idx + 1,
                    "text": text,
                }
            )
        doc.close()

        return pages

    except Exception as fitz_error:
        print("PyMuPDF extraction skipped or failed:", fitz_error)

    # Fallback to pypdf.
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))

        for idx, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            pages.append(
                {
                    "page": idx + 1,
                    "text": text,
                }
            )

        return pages

    except Exception as pypdf_error:
        print("pypdf extraction failed:", pypdf_error)
        raise HTTPException(
            status_code=500,
            detail=(
                "PDF text extraction failed. Install PyMuPDF or pypdf. "
                "Recommended: add pypdf==5.1.0 to requirements.txt and redeploy."
            ),
        )


def _classify_report_from_pages(pages: List[dict]) -> str:
    """
    Lightweight report classifier.
    """
    if not isinstance(pages, list):
        return "section_based"

    text = "\n".join((p.get("text") or "") for p in pages[:10]).lower()

    spectora_signals = [
        "spectora",
        "rj home inspections",
        "rj residential report",
        "deficient",
        "maintenance",
        "page 1 of",
    ]

    if sum(1 for k in spectora_signals if k in text) >= 3:
        return "spectora"

    if (
        "internachi" in text
        or "standards of practice" in text
        or "big ben inspections" in text
    ):
        return "bigben_internachi"

    amerispec_signals = [
        "amerispec",
        "condition:",
        "recommendation:",
        "defect:",
        "observation:",
    ]

    if sum(1 for k in amerispec_signals if k in text) >= 2:
        return "amerispec"

    if "property inspection report" in text or "home inspection report" in text:
        return "generic_narrative"

    return "section_based"


def _guess_system(text: str) -> str:
    value = (text or "").lower()

    system_keywords = [
        (
            "Roof",
            [
                "roof",
                "shingle",
                "flashing",
                "chimney",
                "attic",
                "soffit",
                "fascia",
                "gutter",
                "downspout",
            ],
        ),
        (
            "Exterior",
            [
                "siding",
                "trim",
                "exterior",
                "wall cladding",
                "deck",
                "porch",
                "steps",
                "driveway",
                "walkway",
            ],
        ),
        (
            "Basement",
            [
                "basement",
                "crawlspace",
                "crawl space",
                "foundation",
                "sump",
                "moisture",
                "water intrusion",
            ],
        ),
        (
            "Foundation",
            [
                "foundation",
                "settlement",
                "structural",
                "beam",
                "joist",
                "crack",
            ],
        ),
        (
            "Electrical",
            [
                "electrical",
                "breaker",
                "panel",
                "gfci",
                "outlet",
                "receptacle",
                "wiring",
                "ground",
                "bonding",
            ],
        ),
        (
            "Plumbing",
            [
                "plumbing",
                "pipe",
                "valve",
                "toilet",
                "sink",
                "faucet",
                "water heater",
                "drain",
            ],
        ),
        (
            "HVAC",
            [
                "hvac",
                "furnace",
                "air conditioner",
                "air conditioning",
                "ac unit",
                "heat pump",
                "duct",
            ],
        ),
        (
            "Interior",
            [
                "interior",
                "ceiling",
                "floor",
                "wall",
                "window",
                "door",
                "stair",
                "handrail",
                "guardrail",
            ],
        ),
        (
            "Appliances",
            [
                "appliance",
                "dishwasher",
                "range",
                "oven",
                "microwave",
                "disposal",
            ],
        ),
        (
            "Garage",
            [
                "garage",
                "overhead door",
                "garage door",
                "opener",
            ],
        ),
        (
            "Pest",
            [
                "termite",
                "wdi",
                "wood destroying",
                "pest",
                "rodent",
                "insect",
            ],
        ),
        (
            "Safety",
            [
                "safety",
                "hazard",
                "smoke detector",
                "carbon monoxide",
                "co detector",
                "trip hazard",
            ],
        ),
    ]

    for system, keywords in system_keywords:
        if any(keyword in value for keyword in keywords):
            return system

    return "General"


def _guess_type(text: str) -> str:
    value = (text or "").lower()

    rules = [
        (
            "water_leak",
            [
                "water",
                "leak",
                "moisture",
                "stain",
                "staining",
                "seep",
                "wet",
                "drain",
                "gutter",
                "downspout",
                "basement",
            ],
        ),
        (
            "roof_damage",
            [
                "roof",
                "shingle",
                "flashing",
                "chimney",
                "attic",
                "soffit",
                "fascia",
            ],
        ),
        (
            "mold",
            [
                "mold",
                "fungal",
                "microbial",
                "mildew",
            ],
        ),
        (
            "electrical_issue",
            [
                "electrical",
                "breaker",
                "panel",
                "gfci",
                "outlet",
                "receptacle",
                "wiring",
                "ground",
            ],
        ),
        (
            "plumbing_issue",
            [
                "plumbing",
                "pipe",
                "valve",
                "toilet",
                "sink",
                "faucet",
                "water heater",
            ],
        ),
        (
            "foundation_issue",
            [
                "foundation",
                "settlement",
                "crack",
                "structural",
                "framing",
                "joist",
                "beam",
            ],
        ),
        (
            "hvac_issue",
            [
                "hvac",
                "furnace",
                "air condition",
                "air conditioning",
                "ac unit",
                "heat pump",
                "duct",
            ],
        ),
        (
            "pest_issue",
            [
                "pest",
                "termite",
                "wdi",
                "wood destroying",
                "rodent",
                "insect",
            ],
        ),
        (
            "safety_issue",
            [
                "safety",
                "hazard",
                "trip",
                "fall",
                "guardrail",
                "handrail",
                "smoke",
                "carbon monoxide",
            ],
        ),
    ]

    for issue_type, keywords in rules:
        if any(keyword in value for keyword in keywords):
            return issue_type

    return "general_issue"


def _guess_severity(text: str) -> str:
    value = (text or "").lower()

    if any(
        k in value
        for k in [
            "critical",
            "unsafe",
            "safety hazard",
            "danger",
            "urgent",
            "immediate safety",
        ]
    ):
        return "critical"

    if any(
        k in value
        for k in [
            "high",
            "major",
            "deficient",
            "repair",
            "further evaluation",
            "correction",
            "not functional",
        ]
    ):
        return "high"

    if any(
        k in value
        for k in [
            "low",
            "minor",
            "cosmetic",
            "monitor",
            "maintenance",
            "service",
        ]
    ):
        return "low"

    return "medium"


def _looks_like_issue_text(text: str) -> bool:
    value = (text or "").lower()

    issue_keywords = [
        "deficient",
        "defect",
        "repair",
        "replace",
        "recommend",
        "recommendation",
        "further evaluation",
        "correction",
        "not functional",
        "not operating",
        "unsafe",
        "safety",
        "hazard",
        "moisture",
        "water",
        "leak",
        "stain",
        "staining",
        "damaged",
        "deteriorated",
        "cracked",
        "missing",
        "loose",
        "improper",
        "failed",
        "service",
        "monitor",
    ]

    return any(keyword in value for keyword in issue_keywords)


def _extract_issue_candidates_from_pages(
    pages: List[dict],
    max_issues: int = 40,
) -> List[dict]:
    """
    Heuristic PDF issue extraction.

    This is not the final AI-grade parser, but it gives the workflow real,
    non-empty parser findings from inspection PDFs so the full rail works:

    PDF → n8n → Render parser → normalized findings → /process-inspection → RDS
    """
    all_lines = []

    for page in pages:
        page_num = page.get("page")
        raw_text = page.get("text") or ""

        for raw_line in raw_text.splitlines():
            line = _clean_line(raw_line)

            if len(line) < 8:
                continue

            low = line.lower()

            # Remove common noisy page/footer fragments.
            if low.startswith("page ") and len(line) < 25:
                continue

            if low in ["inspected", "not inspected", "not present", "not applicable"]:
                continue

            all_lines.append(
                {
                    "page": page_num,
                    "text": line,
                }
            )

    issues = []
    seen = set()

    for index, item in enumerate(all_lines):
        line = item["text"]

        if not _looks_like_issue_text(line):
            continue

        context_items = all_lines[max(0, index - 2): min(len(all_lines), index + 4)]
        context_lines = [x["text"] for x in context_items]
        context = " — ".join(context_lines)

        title = line

        if title.lower() in [
            "recommendation",
            "recommendations",
            "deficient",
            "defect",
            "defects",
            "repair",
            "repairs",
        ]:
            if index > 0:
                title = all_lines[index - 1]["text"]

        title = title[:180]
        description = context[:900]

        dedupe = re.sub(r"[^a-z0-9]+", "", (title + description).lower())[:220]

        if dedupe in seen:
            continue

        seen.add(dedupe)

        system = _guess_system(context)
        issue_type = _guess_type(context)
        severity = _guess_severity(context)

        issues.append(
            {
                "issueTitle": title,
                "severity": severity,
                "system": system,
                "component": system,
                "description": description,
                "recommendation": "Review and correct as recommended by a qualified contractor.",
                "page": item.get("page"),
                "type": issue_type,
            }
        )

        if len(issues) >= max_issues:
            break

    return issues


def _normalize_extracted_issue_to_finding(issue: dict) -> dict:
    title = (
        issue.get("issueTitle")
        or issue.get("title")
        or issue.get("name")
        or "Inspection issue"
    )

    description = (
        issue.get("description")
        or issue.get("summary")
        or issue.get("details")
        or issue.get("text")
        or ""
    )

    recommendation = (
        issue.get("recommendation")
        or issue.get("recommended_action")
        or issue.get("nextAction")
        or ""
    )

    notes_parts = [
        str(part).strip()
        for part in [title, description, recommendation]
        if part is not None and str(part).strip()
    ]

    notes = " — ".join(notes_parts)

    location = (
        issue.get("location")
        or issue.get("room")
        or issue.get("area")
        or issue.get("section")
        or issue.get("system")
        or "unspecified"
    )

    combined_text = f"{title} {description} {recommendation} {location}"

    return {
        "type": issue.get("type") or _guess_type(combined_text),
        "severity": _guess_severity(str(issue.get("severity") or combined_text)),
        "location": str(location),
        "notes": notes or str(issue),
    }


# =========================
# PDF UPLOAD ENDPOINT — ADAPTER PATH
# =========================

@app.post("/analyze-report/")
async def analyze_report(file: UploadFile = File(...)):
    """
    Real PDF parser endpoint for n8n.

    Input:
      multipart/form-data
      field name: file

    Output:
      {
        success,
        record_id,
        filename,
        detectedAdapter,
        extractedIssues,
        findings,
        parser_debug
      }

    n8n flow:
      Webhook
      → Determine Input Type
      → FastAPI PDF Parser and Adapter Extraction
      → Transform Parser Output to Normalized Format
      → Send Parser Findings to FastAPI
      → /process-inspection
      → RDS alerts/tasks
    """

    try:
        content = await file.read()

        if not content:
            raise HTTPException(status_code=400, detail="Uploaded PDF was empty")

        filename = file.filename or "inspection-report.pdf"

        pages = _extract_pdf_pages_from_bytes(content)
        detected_adapter = _classify_report_from_pages(pages)
        extracted_issues = _extract_issue_candidates_from_pages(pages)

        findings = [
            _normalize_extracted_issue_to_finding(issue)
            for issue in extracted_issues
        ]

        # If text extraction succeeds but issue detection finds nothing,
        # return a visible review item instead of silently returning empty findings.
        if not findings:
            combined_text = "\n".join((p.get("text") or "") for p in pages[:3])
            preview = _clean_line(combined_text[:600])

            extracted_issues = [
                {
                    "issueTitle": "Manual inspection report review needed",
                    "severity": "medium",
                    "system": "General",
                    "component": "Inspection Report",
                    "description": (
                        "The PDF was received and text was extracted, but no specific "
                        "defect lines were confidently detected by the current parser."
                    ),
                    "recommendation": "Review this report manually or improve the adapter parser.",
                    "page": 1,
                    "type": "general_issue",
                }
            ]

            findings = [
                {
                    "type": "general_issue",
                    "severity": "medium",
                    "location": "General",
                    "notes": (
                        "Manual inspection report review needed — "
                        "The PDF was received, but the current parser did not confidently "
                        f"extract specific defects. Preview: {preview}"
                    ),
                }
            ]

        record_id = _make_pdf_record_id(filename)

        return {
            "success": True,
            "record_id": record_id,
            "filename": filename,
            "message": "PDF parsed by HomeFax parser endpoint",
            "size_bytes": len(content),
            "page_count": len(pages),
            "detectedAdapter": detected_adapter,
            "extractedIssues": extracted_issues,
            "findings": findings,
            "parser_debug": {
                "text_pages": len(pages),
                "detected_adapter": detected_adapter,
                "extracted_issue_count": len(extracted_issues),
                "normalized_finding_count": len(findings),
            },
        }

    except HTTPException:
        raise

    except Exception as e:
        print("ANALYZE REPORT ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))
