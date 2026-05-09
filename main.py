# =========================
# HomeFax FastAPI Backend
# Main API Service
# Parser Quality Pass 1 + Verified Issues
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

load_dotenv(dotenv_path=".env")

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
    description="HomeFax AI ingestion, parser, automation, alerts, and verified issue backend.",
    version="1.2.0",
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

    Uses:
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
    Confirms that FastAPI can reach AWS RDS.
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
# ISSUE NORMALIZATION HELPERS
# =========================

def derive_issue_title(finding_type: str, location: str, notes: str) -> str:
    """
    Creates a readable issue title for verified_issues.

    Example notes:
      Report item 9.8.1 — Missing GFCI — GFCI protection missing...

    Output:
      Missing GFCI
    """
    notes = notes or ""
    finding_type = finding_type or "general_issue"
    location = location or "General"

    normalized_notes = notes.replace(" - ", " — ")

    parts = [
        part.strip()
        for part in normalized_notes.split("—")
        if part and part.strip()
    ]

    for part in parts:
        lowered = part.lower()

        if lowered.startswith("report item"):
            continue

        if len(part) < 4:
            continue

        return part[:180]

    readable_type = finding_type.replace("_", " ").title()
    readable_location = str(location).title()

    return f"{readable_location}: {readable_type}"[:180]


def derive_risk_fields(severity: str):
    """
    Converts severity into verified_issues risk fields.
    """
    severity = (severity or "low").lower().strip()

    if severity == "critical":
        return {
            "risk_score": 95,
            "risk_level": "CRITICAL",
            "priority": "urgent",
        }

    if severity == "high":
        return {
            "risk_score": 80,
            "risk_level": "HIGH",
            "priority": "repair",
        }

    if severity == "medium":
        return {
            "risk_score": 50,
            "risk_level": "MEDIUM",
            "priority": "review",
        }

    return {
        "risk_score": 20,
        "risk_level": "LOW",
        "priority": "monitor",
    }


def verified_issue_exists(cursor, record_id: str, title: str, summary: str) -> bool:
    """
    Prevents duplicate verified_issues without requiring a database unique index.
    """
    cursor.execute(
        """
        SELECT id
        FROM verified_issues
        WHERE record_id = %s
          AND title = %s
          AND summary = %s
        LIMIT 1
        """,
        (
            record_id,
            title,
            summary,
        ),
    )

    return cursor.fetchone() is not None


# =========================
# AI INGESTION — JSON FROM N8N
# =========================

@app.post("/process-inspection")
def process_inspection(data: InspectionProcessRequest):
    """
    Receives normalized findings from n8n and creates:

      1. alerts
      2. automation_tasks
      3. verified_issues

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

    Dedupe:
      alerts/tasks use record_id + type + location + notes hash

    Verified issues:
      one verified_issues row per distinct parsed finding
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
        verified_issues_created = 0
        verified_issues_existing = 0
        processed_findings = []

        for f in findings:
            finding_type = (f.type or "unknown").lower().strip()
            finding_type = re.sub(r"\s+", "_", finding_type)

            severity = (f.severity or "low").lower().strip()
            location = (f.location or "unknown").strip()
            location_key = location.lower().strip()
            notes = f.notes or ""

            if severity not in ["low", "medium", "high", "critical"]:
                severity = "low"

            # -------------------------------------------------
            # 1. Create alert
            # -------------------------------------------------

            notes_hash = hashlib.sha1(notes.encode("utf-8")).hexdigest()[:10]
            alert_key = f"{record_id}:{finding_type}:{location_key}:{notes_hash}".lower()

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

            # -------------------------------------------------
            # 2. Create automation task
            # -------------------------------------------------

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
                    f"{finding_type.replace('_', ' ').title()} issue",
                    notes,
                    "general_home_service",
                    task_key,
                ),
            )

            if cursor.rowcount > 0:
                tasks_created += 1

            # -------------------------------------------------
            # 3. Create verified issue
            # -------------------------------------------------

            issue_title = derive_issue_title(
                finding_type=finding_type,
                location=location,
                notes=notes,
            )

            risk_fields = derive_risk_fields(severity)

            if verified_issue_exists(cursor, record_id, issue_title, notes):
                verified_issues_existing += 1
                verified_issue_created = False

            else:
                cursor.execute(
                    """
                    INSERT INTO verified_issues
                    (
                        record_id,
                        section,
                        title,
                        summary,
                        image_url,
                        severity,
                        status,
                        homeowner_decision,
                        homeowner_note,
                        admin_review_status,
                        admin_note,
                        baseline_locked,
                        baseline_locked_at,
                        current_status,
                        resolved_by_event_id,
                        risk_score,
                        risk_level,
                        priority,
                        created_at,
                        updated_at
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        '',
                        %s,
                        'new',
                        'unreviewed',
                        '',
                        'pending',
                        '',
                        'no',
                        NULL,
                        'open',
                        NULL,
                        %s,
                        %s,
                        %s,
                        NOW(),
                        NOW()
                    )
                    """,
                    (
                        record_id,
                        location,
                        issue_title,
                        notes,
                        severity,
                        risk_fields["risk_score"],
                        risk_fields["risk_level"],
                        risk_fields["priority"],
                    ),
                )

                verified_issues_created += 1
                verified_issue_created = True

            processed_findings.append(
                {
                    "type": finding_type,
                    "severity": severity,
                    "location": location,
                    "notes": notes,
                    "alert_key": alert_key,
                    "task_key": task_key,
                    "verified_issue_title": issue_title,
                    "verified_issue_created": verified_issue_created,
                    "risk_score": risk_fields["risk_score"],
                    "risk_level": risk_fields["risk_level"],
                    "priority": risk_fields["priority"],
                }
            )

        conn.commit()

        return {
            "success": True,
            "record_id": record_id,
            "findings_count": len(findings),
            "alerts_created": alerts_created,
            "tasks_created": tasks_created,
            "verified_issues_created": verified_issues_created,
            "verified_issues_existing": verified_issues_existing,
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
# Parser Quality Pass 1
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

    Later, this can be replaced with a real record_id from:
      - intake form
      - property record
      - upload metadata
      - inspections table
    """
    base = _safe_slug(filename.rsplit(".", 1)[0] if filename else "inspection")
    suffix = uuid.uuid4().hex[:8]
    return f"pdf-{base}-{suffix}"


def _clean_line(line: str) -> str:
    line = line or ""
    line = line.replace("\u2014", " - ")
    line = line.replace("\u2013", " - ")
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def _extract_pdf_pages_from_bytes(content: bytes) -> List[dict]:
    """
    Extract text from PDF bytes.

    Preferred:
      PyMuPDF / fitz

    Fallback:
      pypdf

    Make sure requirements.txt includes:
      pypdf==5.1.0
    """
    pages = []

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
        or "big ben home inspections" in text
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


def _is_noise_line(line: str) -> bool:
    """
    Removes cover-page, table-of-contents, header/footer, and generic label noise.
    """
    cleaned = _clean_line(line)
    low = cleaned.lower()

    if not cleaned:
        return True

    if len(cleaned) < 4:
        return True

    noise_contains = [
        "lateef home inspection services",
        "lateefinspection",
        "lateef home inspection",
        "www.",
        "http://",
        "https://",
        ".com",
        "@",
        "prepared for",
        "prepared by",
        "inspector:",
        "client:",
        "property:",
        "inspection date",
        "report id",
        "page ",
        "table of contents",
        "contents",
        "inspection detail",
        "inspection details",
        "home inspection report",
        "residential inspection report",
        "standards of practice",
        "inter-nachi",
        "internachi",
        "copyright",
        "confidential",
        "agreement",
        "invoice",
        "summary",
        "overview",
        "definitions",
        "legend",
    ]

    if any(fragment in low for fragment in noise_contains):
        return True

    exact_noise = {
        "minor defect",
        "major defect",
        "material defect",
        "safety hazard",
        "maintenance item",
        "monitor",
        "inspected",
        "not inspected",
        "not present",
        "not applicable",
        "limitation",
        "limitations",
        "recommendation",
        "recommendations",
        "deficient",
        "defect",
        "defects",
        "repair",
        "repairs",
        "notes",
        "comments",
        "description",
        "descriptions",
        "observations",
        "observation",
        "information",
        "general information",
    }

    if low in exact_noise:
        return True

    if re.fullmatch(r"[-_=|•\s]+", cleaned):
        return True

    if re.fullmatch(r"\d+\.?\s+[A-Za-z &/-]{3,40}", cleaned):
        return True

    return False


def _normalize_section_name(value: str) -> str:
    value = _clean_line(value)

    if not value:
        return "General"

    aliases = {
        "ac": "HVAC",
        "a/c": "HVAC",
        "heating": "HVAC",
        "cooling": "HVAC",
        "roofing": "Roof",
        "exteriors": "Exterior",
        "interiors": "Interior",
        "electric": "Electrical",
        "plumb": "Plumbing",
    }

    low = value.lower().strip()

    if low in aliases:
        return aliases[low]

    return value[:80]


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
                "roof-covering",
                "roof covering",
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
                "grading",
                "vegetation",
                "doorbell",
            ],
        ),
        (
            "Basement",
            [
                "basement",
                "crawlspace",
                "crawl space",
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
                "floor structure",
                "wall structure",
            ],
        ),
        (
            "Electrical",
            [
                "electrical",
                "breaker",
                "panel",
                "panelboard",
                "gfci",
                "gfcis",
                "outlet",
                "receptacle",
                "wiring",
                "ground",
                "bonding",
                "service drop",
                "service conductors",
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
                "main water shut",
                "hose bib",
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
                "thermostat",
                "cooling system",
                "heating system",
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
                "cabinet",
                "countertop",
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
                "refrigerator",
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
                "fall hazard",
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
                "sump",
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
                "roof-covering",
                "roof covering",
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
                "panelboard",
                "gfci",
                "gfcis",
                "outlet",
                "receptacle",
                "wiring",
                "ground",
                "bonding",
                "knockout",
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
                "hose bib",
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
                "cooling",
                "heating",
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
                "co detector",
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
            "fire hazard",
            "shock hazard",
        ]
    ):
        return "critical"

    if any(
        k in value
        for k in [
            "high",
            "major",
            "material defect",
            "deficient",
            "repair",
            "further evaluation",
            "correction",
            "not functional",
            "active leak",
            "missing gfci",
            "open breaker",
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
    if _is_noise_line(text):
        return False

    value = (text or "").lower()

    if re.match(r"^\d+(\.\d+){1,3}\s+", value):
        return True

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
        "open breaker",
        "gfci",
        "knockout",
    ]

    return any(keyword in value for keyword in issue_keywords)


def _parse_numbered_defect_line(line: str) -> Optional[dict]:
    """
    Parses lines shaped like:

      9.5.1 Electrical - Panelboards & Breakers: Open Breaker Knockout
      9.8.1 Electrical - GFCIs: Missing GFCI
      8.1.1 Plumbing - Main Water Shut-Off Valve: Active Water Leak at Valve
    """
    cleaned = _clean_line(line)

    pattern = re.compile(
        r"^(?P<number>\d+(?:\.\d+){1,3})\s+"
        r"(?P<section>[A-Za-z][A-Za-z &/+-]{2,80})"
        r"(?:\s+-\s+(?P<component>[^:]{2,140}))?"
        r"(?:\s*:\s*(?P<title>.+))?$"
    )

    match = pattern.match(cleaned)

    if not match:
        return None

    number = _clean_line(match.group("number") or "")
    section = _normalize_section_name(match.group("section") or "")
    component = _clean_line(match.group("component") or "")
    title = _clean_line(match.group("title") or "")

    if not title and not component:
        return None

    if _is_noise_line(section) or _is_noise_line(title):
        return None

    if not title:
        title = component or cleaned

    combined = f"{section} {component} {title}"

    return {
        "number": number,
        "section": section,
        "component": component or section,
        "issueTitle": title,
        "type": _guess_type(combined),
        "severity": _guess_severity(combined),
    }


def _build_issue_from_context(
    title: str,
    page: int,
    context_lines: List[str],
    parsed: Optional[dict] = None,
) -> dict:
    context_lines = [
        _clean_line(line)
        for line in context_lines
        if line and not _is_noise_line(line)
    ]

    context = " — ".join(context_lines)
    context = context[:1200]

    parsed = parsed or {}

    section = (
        parsed.get("section")
        or _guess_system(f"{title} {context}")
        or "General"
    )

    component = (
        parsed.get("component")
        or section
        or "General"
    )

    issue_title = (
        parsed.get("issueTitle")
        or title
        or "Inspection issue"
    )

    combined = f"{issue_title} {section} {component} {context}"

    return {
        "issueTitle": issue_title[:180],
        "severity": _guess_severity(combined),
        "system": section,
        "component": component,
        "description": context or issue_title,
        "recommendation": "Review and correct as recommended by a qualified contractor.",
        "page": page,
        "type": parsed.get("type") or _guess_type(combined),
        "source_number": parsed.get("number"),
    }


def _extract_issue_candidates_from_pages(
    pages: List[dict],
    max_issues: int = 60,
) -> List[dict]:
    """
    Parser Quality Pass 1.

    Strategy:
      1. Extract clean lines from PDF text.
      2. Prefer numbered defect lines.
      3. Attach nearby context.
      4. Skip cover page / TOC / footer noise.
      5. Return cleaner structured issues.
    """

    all_lines = []

    for page in pages:
        page_num = page.get("page")
        raw_text = page.get("text") or ""

        for raw_line in raw_text.splitlines():
            line = _clean_line(raw_line)

            if _is_noise_line(line):
                continue

            all_lines.append(
                {
                    "page": page_num,
                    "text": line,
                }
            )

    issues = []
    seen = set()

    # Pass A: strong numbered defect extraction.
    for index, item in enumerate(all_lines):
        line = item["text"]
        parsed = _parse_numbered_defect_line(line)

        if not parsed:
            continue

        start = max(0, index)
        end = min(len(all_lines), index + 6)
        context_lines = [x["text"] for x in all_lines[start:end]]

        issue = _build_issue_from_context(
            title=parsed.get("issueTitle") or line,
            page=item.get("page"),
            context_lines=context_lines,
            parsed=parsed,
        )

        dedupe_base = (
            f"{issue.get('source_number')}|"
            f"{issue.get('system')}|"
            f"{issue.get('component')}|"
            f"{issue.get('issueTitle')}"
        ).lower()

        dedupe = hashlib.sha1(dedupe_base.encode("utf-8")).hexdigest()[:16]

        if dedupe in seen:
            continue

        seen.add(dedupe)
        issues.append(issue)

        if len(issues) >= max_issues:
            break

    # Pass B: fallback keyword extraction if numbered issues are too few.
    if len(issues) < 5:
        for index, item in enumerate(all_lines):
            line = item["text"]

            if not _looks_like_issue_text(line):
                continue

            parsed = _parse_numbered_defect_line(line)

            start = max(0, index - 1)
            end = min(len(all_lines), index + 5)
            context_lines = [x["text"] for x in all_lines[start:end]]

            issue = _build_issue_from_context(
                title=line,
                page=item.get("page"),
                context_lines=context_lines,
                parsed=parsed,
            )

            dedupe_base = (
                f"{issue.get('page')}|"
                f"{issue.get('system')}|"
                f"{issue.get('component')}|"
                f"{issue.get('issueTitle')}|"
                f"{issue.get('description')[:180]}"
            ).lower()

            dedupe = hashlib.sha1(dedupe_base.encode("utf-8")).hexdigest()[:16]

            if dedupe in seen:
                continue

            seen.add(dedupe)
            issues.append(issue)

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

    section = (
        issue.get("system")
        or issue.get("section")
        or issue.get("location")
        or "General"
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

    source_number = issue.get("source_number")

    notes_parts = []

    if source_number:
        notes_parts.append(f"Report item {source_number}")

    notes_parts.extend(
        [
            str(title).strip(),
            str(description).strip(),
            str(recommendation).strip(),
        ]
    )

    notes_parts = [part for part in notes_parts if part]

    notes = " — ".join(notes_parts)

    combined_text = f"{title} {description} {recommendation} {section}"

    return {
        "type": issue.get("type") or _guess_type(combined_text),
        "severity": _guess_severity(str(issue.get("severity") or combined_text)),
        "location": str(section),
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
      → RDS alerts/tasks/verified_issues
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
                    "source_number": None,
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
