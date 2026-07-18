# =========================
# HomeFax AI FastAPI Backend
# Parser + Verified Issues + Dashboard API + Image Verification Contract
# Restore Previous Image Matching Workflow Pass 1
# =========================

import os
import re
import json
import uuid
import hashlib
import subprocess
try:
    from tools.candidate_image_filter_v1 import clean_issue_candidate_images
except Exception:
    clean_issue_candidate_images = None
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel


# =========================
# ENV
# =========================

load_dotenv(dotenv_path=".env")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "homefax_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "homefax")
APP_ENV = os.getenv("APP_ENV", "development")


# =========================
# OPTIONAL IMAGE MATCHER IMPORT
# =========================

try:
    from image_matcher import attach_images_to_issues, image_path_to_url
except Exception as image_import_error:
    print("IMAGE MATCHER IMPORT WARNING:", image_import_error)

    def image_path_to_url(value):
        if not value:
            return ""

        raw = str(value).strip()

        if raw.startswith("http://") or raw.startswith("https://"):
            return raw

        filename = Path(raw).name

        if filename:
            return f"/inspection-images/{filename}"

        return ""

    def attach_images_to_issues(issues, extracted=None):
        return issues


# =========================
# APP
# =========================

app = FastAPI(
    title="HomeFax AI Backend",
    description="HomeFax AI backend for parsing, issue ingestion, verified issues, dashboard API, image serving, and image verification.",
    version="1.6.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# PATHS / STATIC IMAGES
# =========================

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_IMAGES_DIR = OUTPUT_DIR / "images"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

app.mount(
    "/inspection-images",
    StaticFiles(directory=str(OUTPUT_IMAGES_DIR)),
    name="inspection-images",
)


# =========================
# DATABASE
# =========================

def get_db_connection():
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


def get_table_columns(cursor, table_name: str) -> set:
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    return {row["Field"] for row in cursor.fetchall()}


def add_column_if_missing(cursor, table_name: str, column_name: str, column_sql: str):
    columns = get_table_columns(cursor, table_name)

    if column_name not in columns:
        cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN {column_sql}")


def ensure_core_tables():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                record_id VARCHAR(255) NOT NULL,
                alert_type VARCHAR(100) DEFAULT '',
                severity VARCHAR(50) DEFAULT 'low',
                message TEXT,
                status VARCHAR(50) DEFAULT 'active',
                dedupe_key VARCHAR(512) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_alert_dedupe (dedupe_key),
                INDEX idx_alerts_record_id (record_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_tasks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                record_id VARCHAR(255) NOT NULL,
                task_type VARCHAR(100) DEFAULT '',
                priority VARCHAR(50) DEFAULT 'low',
                title VARCHAR(255) DEFAULT '',
                description TEXT,
                source VARCHAR(100) DEFAULT 'ai_ingestion',
                status VARCHAR(50) DEFAULT 'open',
                dedupe_key VARCHAR(512) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_task_dedupe (dedupe_key),
                INDEX idx_tasks_record_id (record_id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS verified_issues (
                id INT AUTO_INCREMENT PRIMARY KEY,
                record_id VARCHAR(255) NOT NULL,
                section VARCHAR(255) DEFAULT '',
                title VARCHAR(255) DEFAULT '',
                summary TEXT,
                image_url TEXT,
                severity VARCHAR(50) DEFAULT 'low',
                status VARCHAR(50) DEFAULT 'new',
                homeowner_decision VARCHAR(100) DEFAULT 'unreviewed',
                homeowner_note TEXT,
                admin_review_status VARCHAR(100) DEFAULT 'pending',
                admin_note TEXT,
                baseline_locked VARCHAR(10) DEFAULT 'no',
                baseline_locked_at DATETIME NULL,
                current_status VARCHAR(100) DEFAULT 'open',
                resolved_by_event_id VARCHAR(255) NULL,
                risk_score INT DEFAULT 20,
                risk_level VARCHAR(50) DEFAULT 'LOW',
                priority VARCHAR(50) DEFAULT 'monitor',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_verified_record_id (record_id),
                INDEX idx_verified_status (status),
                INDEX idx_verified_current_status (current_status),
                INDEX idx_verified_severity (severity)
            )
            """
        )

        add_column_if_missing(
            cursor,
            "verified_issues",
            "image_match_status",
            "image_match_status VARCHAR(50) DEFAULT 'suggested'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "image_match_confidence",
            "image_match_confidence VARCHAR(100) DEFAULT 'page_fallback'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "needs_image_review",
            "needs_image_review VARCHAR(10) DEFAULT 'yes'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "verified_image_url",
            "verified_image_url TEXT NULL",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "candidate_image_urls",
            "candidate_image_urls JSON NULL",
        )

        conn.commit()

    finally:
        cursor.close()
        conn.close()


# =========================
# MODELS
# =========================

class Finding(BaseModel):
    type: Optional[str] = "unknown"
    severity: Optional[str] = "low"
    location: Optional[str] = "unknown"
    notes: Optional[str] = ""

    title: Optional[str] = None
    issueTitle: Optional[str] = None
    issue_title: Optional[str] = None
    findingTitle: Optional[str] = None

    system: Optional[str] = None
    component: Optional[str] = None
    source_number: Optional[str] = None
    sourceNumber: Optional[str] = None
    issueCode: Optional[str] = None
    page: Optional[Any] = None
    page_number: Optional[Any] = None
    summary_page: Optional[Any] = None
    detail_page: Optional[Any] = None
    recommendation: Optional[str] = None

    detectedAdapter: Optional[str] = None
    detected_adapter: Optional[str] = None

    image_url: Optional[str] = ""
    imageUrl: Optional[str] = ""
    verified_image_url: Optional[str] = ""
    verifiedImageUrl: Optional[str] = ""
    matched_image_url: Optional[str] = ""
    matchedImageUrl: Optional[str] = ""
    photo_url: Optional[str] = ""
    photoUrl: Optional[str] = ""
    verified_image_path: Optional[str] = ""
    verifiedImagePath: Optional[str] = ""
    image_path: Optional[str] = ""
    imagePath: Optional[str] = ""

    candidate_image_paths: Optional[List[str]] = []
    candidateImagePaths: Optional[List[str]] = []
    all_page_image_paths: Optional[List[str]] = []
    allPageImagePaths: Optional[List[str]] = []

    candidate_image_urls: Optional[Any] = None
    candidateImageUrls: Optional[Any] = None

    image_match_status: Optional[str] = "suggested"
    imageMatchStatus: Optional[str] = "suggested"
    image_match_confidence: Optional[str] = "page_fallback"
    imageMatchConfidence: Optional[str] = "page_fallback"
    needs_image_review: Optional[str] = "yes"
    needsImageReview: Optional[str] = "yes"

    model_config = {"extra": "allow"}


class InspectionProcessRequest(BaseModel):
    record_id: str
    tenant_id: Optional[str] = ""
    homeowner_user_id: Optional[str] = ""
    homeowner_email: Optional[str] = ""
    property_id: Optional[str] = ""
    property_address: Optional[str] = ""
    inspection_id: Optional[str] = ""
    skip_s3_upload: bool = False
    processing_mode: Optional[str] = ""
    skip_notifications: bool = False
    findings: List[Finding]


class VerifiedIssueStatusUpdate(BaseModel):
    status: Optional[str] = None
    current_status: Optional[str] = None
    homeowner_decision: Optional[str] = None
    homeowner_note: Optional[str] = None
    admin_review_status: Optional[str] = None
    admin_note: Optional[str] = None


class ImageVerificationUpdate(BaseModel):
    image_match_status: str
    verified_image_url: Optional[str] = ""
    admin_note: Optional[str] = ""


# =========================
# HELPERS
# =========================

def model_to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj

    if hasattr(obj, "model_dump"):
        return obj.model_dump()

    if hasattr(obj, "dict"):
        return obj.dict()

    return dict(obj)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_key(value: Any) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def slugify(value: Any) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "item"


def short_hash(value: Any, length: int = 10) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:length]


def make_pdf_record_id(filename: str) -> str:
    base = Path(filename or "inspection-report.pdf").stem
    return f"pdf-{slugify(base)}-by-{uuid.uuid4().hex[:8]}"


def title_case(value: Any) -> str:
    text = clean_text(value).replace("_", " ")

    if not text:
        return ""

    small_words = {"and", "or", "the", "a", "an", "at", "of", "for", "to", "in", "on"}

    parts = []

    for i, word in enumerate(text.split()):
        lower = word.lower()

        if i > 0 and lower in small_words:
            parts.append(lower)
        else:
            parts.append(lower[:1].upper() + lower[1:])

    return " ".join(parts)


def make_public_image_url(value: Any) -> str:
    if not value:
        return ""

    raw = str(value).strip()

    if not raw:
        return ""

    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    if raw.startswith("/inspection-images/"):
        return raw

    if raw.startswith("inspection-images/"):
        return f"/{raw}"

    filename = Path(raw).name

    if filename:
        return f"/inspection-images/{filename}"

    return ""


def extract_image_url_from_dict(finding: Dict[str, Any]) -> str:
    value = (
        finding.get("image_url")
        or finding.get("imageUrl")
        or finding.get("verified_image_url")
        or finding.get("verifiedImageUrl")
        or finding.get("matched_image_url")
        or finding.get("matchedImageUrl")
        or finding.get("photo_url")
        or finding.get("photoUrl")
        or finding.get("verified_image_path")
        or finding.get("verifiedImagePath")
        or finding.get("image_path")
        or finding.get("imagePath")
        or ""
    )

    return make_public_image_url(value)


def normalize_severity(value: Any, title: str = "", summary: str = "") -> str:
    source = f"{value or ''} {title or ''} {summary or ''}".lower()

    if any(word in source for word in ["critical", "unsafe", "hazard", "fire", "shock"]):
        return "critical"

    if any(word in source for word in ["high", "active leak", "leak", "missing gfci", "breaker", "water", "electrical"]):
        return "high"

    if any(word in source for word in ["low", "minor", "monitor", "maintenance"]):
        return "low"

    return "medium"


def risk_fields_from_severity(severity: str) -> Dict[str, Any]:
    severity = normalize_severity(severity)

    if severity == "critical":
        return {"risk_score": 95, "risk_level": "CRITICAL", "priority": "urgent"}

    if severity == "high":
        return {"risk_score": 80, "risk_level": "HIGH", "priority": "repair"}

    if severity == "medium":
        return {"risk_score": 50, "risk_level": "MEDIUM", "priority": "review"}

    return {"risk_score": 20, "risk_level": "LOW", "priority": "monitor"}


def normalize_issue_type(source: str) -> str:
    value = str(source or "").lower()

    if any(word in value for word in ["water", "leak", "plumbing", "gutter", "downspout", "drain"]):
        return "water_leak"

    if any(word in value for word in ["electric", "gfci", "breaker", "wiring", "panel", "receptacle"]):
        return "electrical_issue"

    if any(word in value for word in ["roof", "flashing", "shingle"]):
        return "roof_damage"

    if any(word in value for word in ["foundation", "crawlspace", "basement", "structural"]):
        return "foundation_issue"

    if any(word in value for word in ["hvac", "furnace", "cooling", "heating", "air conditioner"]):
        return "hvac_issue"

    if any(word in value for word in ["exterior", "siding", "wall-covering", "wall covering"]):
        return "exterior_issue"

    return "general_issue"


def derive_issue_title(finding: Dict[str, Any]) -> str:
    rich_title = (
        finding.get("issueTitle")
        or finding.get("issue_title")
        or finding.get("title")
        or finding.get("findingTitle")
        or ""
    )

    if rich_title:
        return title_case(rich_title)[:255]

    notes = clean_text(finding.get("notes") or finding.get("summary") or finding.get("description") or "")

    patterns = [
        r"Report item\s+[0-9.]+\s*[—:-]\s*([^—\n\r]+)",
        r"^\d+(?:\.\d+)+\s+[^:]+:\s*([^—\n\r]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, notes, re.IGNORECASE)

        if match:
            return title_case(match.group(1))[:255]

    return title_case(finding.get("type") or "Inspection issue")[:255]


def derive_section(finding: Dict[str, Any]) -> str:
    return clean_text(
        finding.get("component")
        or finding.get("system")
        or finding.get("location")
        or "General"
    )[:255]


def build_summary(finding: Dict[str, Any], title: str, section: str) -> str:
    existing = clean_text(finding.get("notes") or finding.get("summary") or finding.get("description"))

    if existing:
        return existing

    source_number = clean_text(finding.get("source_number") or finding.get("sourceNumber") or finding.get("issueCode"))
    system = clean_text(finding.get("system"))
    component = clean_text(finding.get("component") or section)
    recommendation = clean_text(
        finding.get("recommendation")
        or "Review and correct as recommended by a qualified contractor."
    )

    parts = [
        f"Report item {source_number}" if source_number else "",
        title,
        f"System: {system}" if system else "",
        f"Component: {component}" if component else "",
        recommendation,
    ]

    return " — ".join([part for part in parts if part])


def make_dedupe_key(record_id: str, finding: Dict[str, Any], title: str, section: str) -> str:
    source_number = clean_text(finding.get("source_number") or finding.get("sourceNumber") or finding.get("issueCode"))
    basis = f"{record_id}:{source_number}:{title}:{section}:{finding.get('type', '')}".lower()
    return f"{record_id}:{slugify(title)}:{slugify(section)}:{short_hash(basis, 10)}"


def notify_record_owner(record_id: str, subject: str, message: str):
    print("NOTIFY_RECORD_OWNER")
    print("record_id:", record_id)
    print("subject:", subject)
    print("message:", message)


def to_json_or_none(value: Any):
    if value is None:
        return None

    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except Exception:
            return json.dumps([value])

    return json.dumps(value)


# =========================
# IMAGE EXTRACTION / MATCHING HELPERS
# =========================

def extract_page_from_image_filename(path: str) -> Optional[int]:
    match = re.search(r"page[_-](\d+)[_-]img", str(path), re.IGNORECASE)

    if not match:
        return None

    try:
        return int(match.group(1))
    except Exception:
        return None


def collect_images_by_page() -> Dict[int, List[str]]:
    images_by_page: Dict[int, List[str]] = {}

    image_paths = sorted(
        list(OUTPUT_IMAGES_DIR.glob("*.png"))
        + list(OUTPUT_IMAGES_DIR.glob("*.jpg"))
        + list(OUTPUT_IMAGES_DIR.glob("*.jpeg"))
        + list(OUTPUT_IMAGES_DIR.glob("*.webp"))
    )

    for path in image_paths:
        page = extract_page_from_image_filename(path.name)

        if page is None:
            continue

        images_by_page.setdefault(page, []).append(str(path))

    return images_by_page


def score_image_for_issue(image_path: str, issue_title: str = "") -> int:
    filename = Path(image_path).name.lower()
    title = str(issue_title or "").lower()

    score = 10

    if "f98855376075" in filename:
        score -= 8

    if filename.endswith((".jpg", ".jpeg")):
        score += 3

    if filename.endswith(".png"):
        score -= 1

    if any(word in title for word in ["roof", "flashing", "gutter", "downspout"]):
        score += 3

    if any(word in title for word in ["water", "plumbing", "valve", "pipe", "drain", "leak"]):
        score += 3

    if any(word in title for word in ["electric", "gfci", "breaker", "panel", "wiring"]):
        score += 3

    return score


def safe_int(value: Any) -> Optional[int]:
    if value in [None, ""]:
        return None

    try:
        return int(value)
    except Exception:
        return None


def attach_images_locally_if_needed(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    images_by_page = collect_images_by_page()

    if not images_by_page:
        return issues

    enriched = []

    for issue in issues:
        if not isinstance(issue, dict):
            continue

        current_image = issue.get("image_url") or issue.get("imageUrl")

        if current_image:
            enriched.append(issue)
            continue

        title = (
            issue.get("issueTitle")
            or issue.get("issue_title")
            or issue.get("title")
            or issue.get("findingTitle")
            or issue.get("type")
            or ""
        )

        page = safe_int(
            issue.get("detail_page")
            or issue.get("summary_page")
            or issue.get("page")
            or issue.get("page_number")
        )

        if page:
            candidate_pages = [page, page + 1, page - 1, page + 2, page - 2]
        else:
            candidate_pages = sorted(images_by_page.keys())[:3]

        candidate_paths = []

        for candidate_page in candidate_pages:
            for image_path in images_by_page.get(candidate_page, []):
                if image_path not in candidate_paths:
                    candidate_paths.append(image_path)

        if candidate_paths:
            ranked = sorted(candidate_paths, key=lambda path: score_image_for_issue(path, title), reverse=True)
            best_path = ranked[0]
        else:
            ranked = []
            best_path = None

        next_issue = dict(issue)
        next_issue["candidate_image_paths"] = ranked[:10]
        next_issue["candidate_image_urls"] = [make_public_image_url(path) for path in ranked[:10]]
        next_issue["all_page_image_paths"] = images_by_page.get(page, []) if page else []
        next_issue["verified_image_path"] = best_path
        next_issue["verified_image_url"] = ""
        next_issue["image_url"] = make_public_image_url(best_path)

        if best_path:
            next_issue["image_match_status"] = "suggested"
            next_issue["image_match_confidence"] = "local_detail_or_summary_page_candidate"
            next_issue["needs_image_review"] = "yes"
        else:
            next_issue["image_match_status"] = "none"
            next_issue["image_match_confidence"] = "no_candidate_found"
            next_issue["needs_image_review"] = "yes"

        enriched.append(next_issue)

    return enriched


# =========================
# PDF PARSER HELPERS
# =========================

def save_pdf_images_with_fitz(doc) -> int:
    count = 0

    for page_index in range(len(doc)):
        page_number = page_index + 1
        page = doc[page_index]
        images = page.get_images(full=True)

        for image_index, image_info in enumerate(images, start=1):
            try:
                xref = image_info[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image.get("image")
                ext = (base_image.get("ext") or "png").lower()

                if not image_bytes:
                    continue

                digest = hashlib.sha256(image_bytes).hexdigest()[:12]
                filename = f"page_{page_number}_img_{image_index}_{digest}.{ext}"
                path = OUTPUT_IMAGES_DIR / filename

                if not path.exists():
                    path.write_bytes(image_bytes)

                count += 1

            except Exception as image_error:
                print("PDF IMAGE EXTRACT WARNING:", image_error)

    return count


def extract_pdf_pages(content: bytes) -> tuple[List[Dict[str, Any]], int]:
    pages = []
    image_count = 0

    try:
        import fitz

        doc = fitz.open(stream=content, filetype="pdf")

        try:
            image_count = save_pdf_images_with_fitz(doc)

            for idx, page in enumerate(doc):
                text = page.get_text("text") or ""
                pages.append({"page": idx + 1, "text": text})

        finally:
            doc.close()

        return pages, image_count

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

            pages.append({"page": idx + 1, "text": text})

        return pages, image_count

    except Exception as pypdf_error:
        print("pypdf extraction failed:", pypdf_error)
        raise HTTPException(
            status_code=500,
            detail="PDF text extraction failed. Install PyMuPDF or pypdf.",
        )


def clean_line(line: str) -> str:
    line = str(line or "")
    line = line.replace("\u2014", " — ")
    line = line.replace("\u2013", " — ")
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def is_noise_line(line: str) -> bool:
    low = clean_line(line).lower()

    if not low or len(low) < 4:
        return True

    noise = [
        "table of contents",
        "inspection details",
        "inspection detail",
        "prepared for",
        "prepared by",
        "client:",
        "inspector:",
        "property:",
        "inspection date",
        "standards of practice",
        "internachi",
        "copyright",
        "lateef home inspection",
        "home inspection report",
        "residential inspection report",
    ]

    return any(item in low for item in noise)


def parse_numbered_issue_line(line: str) -> Optional[Dict[str, Any]]:
    line = clean_line(line)

    pattern = re.compile(
        r"^(?P<number>\d+(?:\.\d+){1,3})\s+"
        r"(?P<section>[A-Za-z][A-Za-z &/+-]{2,120})"
        r"(?:\s+-\s+(?P<component>[^:]{2,180}))?"
        r"\s*:\s*(?P<title>.+?)\s*$"
    )

    match = pattern.match(line)

    if not match:
        return None

    number = clean_text(match.group("number"))
    section = clean_text(match.group("section"))
    component = clean_text(match.group("component") or section)
    title = clean_text(match.group("title"))

    if not number or not title or is_noise_line(title):
        return None

    system = section
    combined = f"{system} {component} {title}"

    return {
        "issueTitle": title_case(title),
        "title": title_case(title),
        "severity": normalize_severity("", title, combined),
        "system": system,
        "component": component,
        "description": (
            f"{number} {component}: {title} — "
            "Review and correct as recommended by a qualified contractor."
        ),
        "recommendation": "Review and correct as recommended by a qualified contractor.",
        "type": normalize_issue_type(combined),
        "source_number": number,
    }


def classify_report(filename: str, pages: List[Dict[str, Any]]) -> str:
    text = "\n".join((page.get("text") or "") for page in pages[:8]).lower()
    source = f"{filename or ''}\n{text}".lower()

    if "internachi" in source or "big ben" in source or "big ben inspections" in source:
        return "bigben_internachi"

    if "spectora" in source:
        return "spectora"

    if "amerispec" in source:
        return "amerispec"

    return "section_based"


def extract_issues_from_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issues = []
    seen = set()

    for page in pages:
        page_number = page.get("page")
        text = page.get("text") or ""

        for raw_line in text.splitlines():
            line = clean_line(raw_line)

            if is_noise_line(line):
                continue

            parsed = parse_numbered_issue_line(line)

            if not parsed:
                continue

            parsed["page"] = page_number
            parsed["summary_page"] = page_number
            parsed["detail_page"] = page_number

            dedupe_basis = (
                f"{parsed.get('source_number')}|"
                f"{parsed.get('system')}|"
                f"{parsed.get('component')}|"
                f"{parsed.get('issueTitle')}"
            ).lower()

            dedupe = short_hash(dedupe_basis, 16)

            if dedupe in seen:
                continue

            seen.add(dedupe)
            issues.append(parsed)

    return issues


# =========================
# DETAIL PAGE RECOVERY
# Source Number Image Matching Pass 1
# =========================

def page_has_extracted_images(page_number: Any) -> bool:
    """
    Checks whether output/images has extracted images for a PDF page.
    """
    page = safe_int(page_number)

    if page is None:
        return False

    if not OUTPUT_IMAGES_DIR.exists():
        return False

    patterns = [
        f"page_{page}_img_*",
        f"page-{page}-img-*",
    ]

    for pattern in patterns:
        if list(OUTPUT_IMAGES_DIR.glob(pattern)):
            return True

    return False


def source_number_pattern(source_number: str):
    """
    Builds a safe regex for source numbers like:
      2.4.5
      8.1.1
      AI.1

    We avoid overmatching inside longer numbers.
    """
    source_number = clean_text(source_number)

    if not source_number:
        return None

    escaped = re.escape(source_number)

    return re.compile(
        rf"(?<![A-Za-z0-9.]){escaped}(?![A-Za-z0-9.])",
        flags=re.IGNORECASE,
    )


def make_text_snippet(text: str, needle: str, window: int = 320) -> str:
    """
    Returns a short snippet around a source number/title hit.
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    needle = str(needle or "").strip()

    if not text:
        return ""

    if not needle:
        return text[: window * 2]

    idx = text.lower().find(needle.lower())

    if idx < 0:
        return text[: window * 2]

    start = max(0, idx - window)
    end = min(len(text), idx + window)

    return text[start:end].strip()


def title_token_score(title: str, snippet: str) -> int:
    """
    Scores how many useful title words appear near a page hit.
    """
    title = str(title or "").lower()
    snippet = str(snippet or "").lower()

    stopwords = {
        "at", "and", "or", "the", "a", "an", "to", "for", "in", "on",
        "of", "by", "with", "near", "from", "is", "are",
    }

    tokens = [
        re.sub(r"[^a-z0-9]+", "", token)
        for token in title.split()
    ]

    tokens = [
        token for token in tokens
        if token and len(token) >= 4 and token not in stopwords
    ]

    if not tokens:
        return 0

    return sum(1 for token in tokens if token in snippet)


def score_detail_page_candidate(
    page_number: int,
    summary_page: Optional[int],
    source_number: str,
    title: str,
    system: str,
    component: str,
    page_text: str,
) -> tuple[int, str]:
    """
    Higher score means the page is more likely to be the actual detail/photo page,
    not just the summary listing page.
    """
    text = re.sub(r"\s+", " ", str(page_text or "")).strip()
    low = text.lower()

    snippet = make_text_snippet(text, source_number or title)
    snippet_low = snippet.lower()

    score = 0
    reasons = []

    # Direct source number hit matters most.
    if source_number and source_number.lower() in low:
        score += 20
        reasons.append("source_number_hit")

    # Prefer pages after the summary page when available.
    if summary_page is not None:
        if page_number > summary_page:
            score += 14
            reasons.append("after_summary_page")
        elif page_number == summary_page:
            score += 2
            reasons.append("same_as_summary_page")
        else:
            score -= 5
            reasons.append("before_summary_page")

    # Pages with extracted images are more likely to be detail/photo pages.
    if page_has_extracted_images(page_number):
        score += 12
        reasons.append("page_has_images")

    # Title words near the hit are strong evidence.
    title_hits = title_token_score(title, snippet)
    if title_hits:
        score += title_hits * 4
        reasons.append(f"title_tokens_{title_hits}")

    # System/component terms near the hit help.
    for label, value in [("system", system), ("component", component)]:
        value = str(value or "").lower().strip()
        if value and value in snippet_low:
            score += 3
            reasons.append(f"{label}_near_hit")

    # Detail pages often contain recommendations, observations, or defect language.
    detail_terms = [
        "recommend",
        "correction",
        "repair",
        "defect",
        "deficient",
        "observed",
        "condition",
        "safety",
        "hazard",
        "monitor",
        "contact",
        "qualified",
    ]

    detail_hits = sum(1 for term in detail_terms if term in snippet_low)
    if detail_hits:
        score += min(detail_hits, 4) * 2
        reasons.append(f"detail_terms_{detail_hits}")

    # Summary pages often have many issue numbers packed together.
    issue_number_count = len(re.findall(r"\b\d+(?:\.\d+){1,3}\b", snippet))
    if issue_number_count >= 6:
        score -= 8
        reasons.append("many_issue_numbers_summary_like")
    elif issue_number_count >= 3:
        score -= 3
        reasons.append("some_issue_numbers_summary_like")

    # Penalize explicit summary/table-of-contents language.
    if any(term in snippet_low for term in ["table of contents", "summary", "items inspected"]):
        score -= 7
        reasons.append("summary_language")

    return score, ",".join(reasons)


def recover_detail_pages_for_issues(
    issues: List[Dict[str, Any]],
    pages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Uses source_number + title + page images to recover the true detail page.

    Product goal:
      summary_page = where issue was first listed
      detail_page  = best page for actual finding/photos

    This improves image matching because image_matcher uses detail_page first.
    """
    if not issues or not pages:
        return issues

    recovered = []

    for issue in issues:
        if not isinstance(issue, dict):
            continue

        next_issue = dict(issue)

        source_number = clean_text(
            next_issue.get("source_number")
            or next_issue.get("sourceNumber")
            or next_issue.get("issueCode")
        )

        title = clean_text(
            next_issue.get("issueTitle")
            or next_issue.get("issue_title")
            or next_issue.get("title")
            or ""
        )

        system = clean_text(next_issue.get("system") or "")
        component = clean_text(next_issue.get("component") or "")
        summary_page = safe_int(
            next_issue.get("summary_page")
            or next_issue.get("page")
            or next_issue.get("page_number")
        )

        if summary_page is not None:
            next_issue["summary_page"] = summary_page

        pattern = source_number_pattern(source_number)
        candidates = []

        # First pass: exact source number search.
        if pattern:
            for page_obj in pages:
                page_number = safe_int(page_obj.get("page") or page_obj.get("page_number"))
                page_text = page_obj.get("text") or ""

                if page_number is None:
                    continue

                if not pattern.search(page_text):
                    continue

                score, reasons = score_detail_page_candidate(
                    page_number=page_number,
                    summary_page=summary_page,
                    source_number=source_number,
                    title=title,
                    system=system,
                    component=component,
                    page_text=page_text,
                )

                candidates.append(
                    {
                        "page": page_number,
                        "score": score,
                        "reasons": reasons,
                        "snippet": make_text_snippet(page_text, source_number),
                    }
                )

        # Second pass: title search if source number only appears once or not at all.
        if len(candidates) <= 1 and title:
            title_words = [
                token for token in re.split(r"[^A-Za-z0-9]+", title.lower())
                if len(token) >= 5
            ]

            for page_obj in pages:
                page_number = safe_int(page_obj.get("page") or page_obj.get("page_number"))
                page_text = page_obj.get("text") or ""
                page_low = page_text.lower()

                if page_number is None:
                    continue

                title_hit_count = sum(1 for token in title_words if token in page_low)

                if title_words and title_hit_count < max(1, min(2, len(title_words))):
                    continue

                score, reasons = score_detail_page_candidate(
                    page_number=page_number,
                    summary_page=summary_page,
                    source_number=source_number,
                    title=title,
                    system=system,
                    component=component,
                    page_text=page_text,
                )

                # Title-only hits are useful but weaker than source-number hits.
                score -= 4
                reasons = f"{reasons},title_fallback"

                candidates.append(
                    {
                        "page": page_number,
                        "score": score,
                        "reasons": reasons,
                        "snippet": make_text_snippet(page_text, title),
                    }
                )

        # Dedupe candidates by page, keeping the highest score.
        by_page = {}

        for candidate in candidates:
            page = candidate["page"]

            if page not in by_page or candidate["score"] > by_page[page]["score"]:
                by_page[page] = candidate

        candidates = sorted(
            by_page.values(),
            key=lambda item: item["score"],
            reverse=True,
        )

        if candidates:
            best = candidates[0]
            next_issue["detail_page"] = best["page"]
            next_issue["detail_page_recovered"] = "yes"
            next_issue["detail_page_confidence"] = best["reasons"]
            next_issue["detail_page_candidates"] = candidates[:5]
        else:
            # Keep previous behavior if no better page is found.
            fallback_page = safe_int(
                next_issue.get("detail_page")
                or next_issue.get("summary_page")
                or next_issue.get("page")
            )

            next_issue["detail_page"] = fallback_page
            next_issue["detail_page_recovered"] = "no"
            next_issue["detail_page_confidence"] = "no_source_or_title_detail_page_found"
            next_issue["detail_page_candidates"] = []

        recovered.append(next_issue)

    return recovered



def normalize_extracted_issue_to_finding(issue: Dict[str, Any], detected_adapter: str) -> Dict[str, Any]:
    title = clean_text(
        issue.get("issueTitle")
        or issue.get("title")
        or "Inspection issue"
    )

    system = clean_text(issue.get("system") or "General")
    component = clean_text(issue.get("component") or system or "General")
    source_number = clean_text(issue.get("source_number") or issue.get("sourceNumber") or issue.get("issueCode"))
    recommendation = clean_text(
        issue.get("recommendation")
        or "Review and correct as recommended by a qualified contractor."
    )

    notes_parts = [
        f"Report item {source_number}" if source_number else "",
        title,
        f"System: {system}" if system else "",
        f"Component: {component}" if component else "",
        recommendation,
    ]

    return {
        "type": issue.get("type") or normalize_issue_type(f"{title} {system} {component}"),
        "severity": normalize_severity(issue.get("severity"), title, issue.get("description", "")),
        "location": component,
        "notes": " — ".join([part for part in notes_parts if part]),
        "title": title,
        "issueTitle": title,
        "system": system,
        "component": component,
        "source_number": source_number,
        "page": issue.get("page"),
        "summary_page": issue.get("summary_page"),
        "detail_page": issue.get("detail_page"),
        "recommendation": recommendation,
        "detectedAdapter": detected_adapter,
        "image_url": issue.get("image_url") or "",
        "verified_image_url": issue.get("verified_image_url") if issue.get("image_match_status") == "verified" else "",
        "verified_image_path": issue.get("verified_image_path") or "",
        "candidate_image_paths": issue.get("candidate_image_paths") or [],
        "candidate_image_urls": issue.get("candidate_image_urls") or [],
        "all_page_image_paths": issue.get("all_page_image_paths") or [],
        "image_match_status": issue.get("image_match_status") or "suggested",
        "image_match_confidence": issue.get("image_match_confidence") or "page_fallback",
        "needs_image_review": issue.get("needs_image_review") or "yes",
    }


# =========================
# ROUTES: HEALTH
# =========================

@app.get("/")
def root():
    return {
        "success": True,
        "service": "HomeFax AI Backend",
        "environment": APP_ENV,
        "db_host": DB_HOST,
        "db_name": DB_NAME,
        "image_serving": "/inspection-images/{filename}",
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "homefax-fastapi"}


@app.get("/db-health")
def db_health():
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
        raise HTTPException(status_code=500, detail=f"Database connection failed: {str(e)}")


# =========================
# ROUTE: CURRENT PDF ANALYZE
# =========================

@app.post("/analyze-report/")
async def analyze_report(file: UploadFile = File(...)):
    try:
        content = await file.read()

        if not content:
            raise HTTPException(status_code=400, detail="Uploaded PDF was empty")

        filename = file.filename or "inspection-report.pdf"

        pages, image_count = extract_pdf_pages(content)
        detected_adapter = classify_report(filename, pages)

        extracted_issues = extract_issues_from_pages(pages)

        try:
            extracted_issues = recover_detail_pages_for_issues(extracted_issues, pages)
        except Exception as detail_error:
            print("DETAIL PAGE RECOVERY WARNING:", detail_error)

        try:
            extracted_issues = attach_images_to_issues(extracted_issues)
        except Exception as image_error:
            print("IMAGE MATCHER WARNING:", image_error)

        try:
            extracted_issues = attach_images_locally_if_needed(extracted_issues)
        except Exception as image_error:
            print("LOCAL IMAGE LINK WARNING:", image_error)

        findings = [
            normalize_extracted_issue_to_finding(issue, detected_adapter)
            for issue in extracted_issues
        ]

        if not findings:
            findings = [
                {
                    "type": "general_issue",
                    "severity": "medium",
                    "location": "General",
                    "notes": "Manual inspection report review needed — No specific defect lines were confidently detected.",
                    "title": "Manual inspection report review needed",
                    "issueTitle": "Manual inspection report review needed",
                    "system": "General",
                    "component": "Inspection Report",
                    "source_number": "",
                    "page": 1,
                    "summary_page": 1,
                    "detail_page": 1,
                    "recommendation": "Review this report manually or improve the adapter parser.",
                    "image_url": "",
                    "verified_image_url": "",
                    "candidate_image_urls": [],
                    "image_match_status": "none",
                    "image_match_confidence": "no_candidate_found",
                    "needs_image_review": "yes",
                }
            ]

        # Image verification contract:
        # Any parser-proposed image is only suggested until admin verifies it.
        for finding in findings:
            if not isinstance(finding, dict):
                continue

            status = clean_text(finding.get("image_match_status") or "suggested").lower()

            if status != "verified":
                finding["verified_image_url"] = ""

            if finding.get("image_url") and not finding.get("image_match_status"):
                finding["image_match_status"] = "suggested"

            if finding.get("image_url") and not finding.get("needs_image_review"):
                finding["needs_image_review"] = "yes"

        extracted_issues = findings
        record_id = make_pdf_record_id(filename)

        response_payload = {
            "success": True,
            "record_id": record_id,
            "filename": filename,
            "message": "PDF parsed by HomeFax parser endpoint",
            "size_bytes": len(content),
            "page_count": len(pages),
            "image_count": image_count,
            "detectedAdapter": detected_adapter,
            "extractedIssues": extracted_issues,
            "findings": findings,
            "findings_count": len(findings),
            "issuesWithImagesCount": sum(
                1 for item in findings
                if isinstance(item, dict) and item.get("image_url")
            ),
            "parser_debug": {
                "workflow": "current_parser_with_image_contract",
                "text_pages": len(pages),
                "detected_adapter": detected_adapter,
                "extracted_issue_count": len(extracted_issues),
                "normalized_finding_count": len(findings),
                "image_count": image_count,
                "image_linking_enabled": True,
                "image_contract": "suggested_until_admin_verified",
                "inspection_images_url_base": "/inspection-images/",
                "dynamic_profile_auto_apply_enabled": True,
            },
        }

        # -------------------------------------------------
        # Dynamic Adapter Rule Application Pass 2B
        # Auto-apply promoted dynamic profile hints.
        #
        # Non-destructive:
        # - does not overwrite issue titles
        # - does not overwrite summaries
        # - does not overwrite severities
        # - does not fill verified_image_url
        # -------------------------------------------------
        try:
            if "dynamic_match_best_profile" in globals() and "dynamic_apply_profile_hints_to_result" in globals():
                original_response_payload = json.loads(json.dumps(response_payload, default=str))

                dynamic_match_result = dynamic_match_best_profile(
                    response_payload,
                    threshold=55,
                )

                response_payload = dynamic_apply_profile_hints_to_result(
                    response_payload,
                    dynamic_match_result,
                )

                dynamic_event_id = None

                if "dynamic_log_profile_match_event" in globals():
                    dynamic_event_id = dynamic_log_profile_match_event(
                        original_response_payload,
                        dynamic_match_result,
                        applied_result=response_payload,
                    )

                response_payload.setdefault("parser_debug", {})
                response_payload["parser_debug"]["dynamic_adapter_profile_match_event_id"] = dynamic_event_id
            else:
                response_payload.setdefault("parser_debug", {})
                response_payload["parser_debug"]["dynamic_adapter_profile_matched"] = False
                response_payload["parser_debug"]["dynamic_adapter_profile_error"] = "dynamic adapter functions not loaded"

        except Exception as dynamic_profile_error:
            print("DYNAMIC PROFILE AUTO-APPLY WARNING:", dynamic_profile_error)

            response_payload.setdefault("parser_debug", {})
            response_payload["parser_debug"]["dynamic_adapter_profile_matched"] = False
            response_payload["parser_debug"]["dynamic_adapter_profile_error"] = str(dynamic_profile_error)

        # Re-enforce image verification contract after dynamic hint application.
        for key in ["extractedIssues", "findings"]:
            if isinstance(response_payload.get(key), list):
                for issue in response_payload[key]:
                    if not isinstance(issue, dict):
                        continue

                    if clean_text(issue.get("image_match_status") or "").lower() != "verified":
                        issue["verified_image_url"] = ""

        # Optional AI adapter learning log. This does not auto-trust AI.
        try:
            if "log_ai_adapter_learning_run" in globals():
                learning_run_id = log_ai_adapter_learning_run(response_payload)
                response_payload["ai_adapter_learning_run_id"] = learning_run_id
            else:
                response_payload["ai_adapter_learning_run_id"] = None
        except Exception as learning_error:
            print("AI ADAPTER LEARNING WARNING:", learning_error)
            response_payload["ai_adapter_learning_run_id"] = None

        return response_payload

    except HTTPException:
        raise

    except Exception as e:
        print("ANALYZE REPORT ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================
# ROUTE: RESTORED PREVIOUS IMAGE MATCHING WORKFLOW
# =========================

def run_restore_command(cmd: List[str], cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            timeout=240,
        )

        return True, result.stdout

    except subprocess.CalledProcessError as e:
        return False, e.stderr or e.stdout or str(e)

    except Exception as e:
        return False, str(e)


def load_restored_issue_records() -> List[Dict[str, Any]]:
    issue_file = OUTPUT_DIR / "issue_records_v1.json"

    if not issue_file.exists():
        return []

    with open(issue_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data

    return []


def normalize_restored_issue_to_finding(item: Dict[str, Any], detected_adapter: str) -> Dict[str, Any]:
    issue_code = clean_text(item.get("issue_code") or item.get("source_number"))
    title = clean_text(item.get("issue_title") or item.get("issueTitle") or item.get("title") or "Inspection issue")
    system = clean_text(item.get("system") or "General")
    component = clean_text(item.get("component") or system)
    severity = clean_text(item.get("report_severity") or item.get("severity") or "medium").lower()
    summary = clean_text(item.get("homeowner_summary") or item.get("source_text") or title)
    recommendation = clean_text(
        item.get("next_action")
        or item.get("recommendation_text")
        or item.get("recommendation")
        or "Review and correct as recommended by a qualified contractor."
    )

    verified_image_path = item.get("verified_image_path")
    image_url = make_public_image_url(verified_image_path)

    candidate_image_paths = item.get("candidate_image_paths") or []
    all_page_image_paths = item.get("all_page_image_paths") or []

    candidate_image_urls = [
        make_public_image_url(path) for path in candidate_image_paths if make_public_image_url(path)
    ]

    notes_parts = [
        f"Report item {issue_code}" if issue_code else "",
        title,
        f"System: {system}" if system else "",
        f"Component: {component}" if component else "",
        recommendation,
    ]

    notes = " — ".join([part for part in notes_parts if part])

    return {
        "type": normalize_issue_type(f"{title} {system} {component}"),
        "severity": severity if severity in ["low", "medium", "high", "critical"] else normalize_severity(severity, title, summary),
        "location": component,
        "notes": notes,
        "title": title,
        "issueTitle": title,
        "system": system,
        "component": component,
        "source_number": issue_code,
        "page": item.get("summary_page") or item.get("detail_page"),
        "summary_page": item.get("summary_page"),
        "detail_page": item.get("detail_page"),
        "recommendation": recommendation,
        "detectedAdapter": detected_adapter,
        "image_url": image_url,
        "verified_image_url": image_url,
        "verified_image_path": verified_image_path or "",
        "candidate_image_paths": candidate_image_paths,
        "candidate_image_urls": candidate_image_urls,
        "all_page_image_paths": all_page_image_paths,
        "image_match_status": "suggested" if image_url else "none",
        "image_match_confidence": "restored_issue_record_match" if image_url else "no_candidate_found",
        "needs_image_review": "yes",
    }


@app.post("/analyze-report-restored/")
async def analyze_report_restored(file: UploadFile = File(...)):
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Uploaded file must have a filename")

        content = await file.read()

        if not content:
            raise HTTPException(status_code=400, detail="Uploaded PDF was empty")

        record_id = make_pdf_record_id(file.filename)

        tmp_upload_path = OUTPUT_DIR / "latest_uploaded_report.pdf"
        tmp_upload_path.write_bytes(content)

        ok, details = run_restore_command(
            ["python", "extract_findings.py", str(tmp_upload_path)],
            PROJECT_ROOT,
        )

        if not ok:
            raise HTTPException(
                status_code=500,
                detail=f"extract_findings.py failed: {details}",
            )

        ok, details = run_restore_command(
            ["python", "build_issue_records.py"],
            PROJECT_ROOT,
        )

        if not ok:
            raise HTTPException(
                status_code=500,
                detail=f"build_issue_records.py failed: {details}",
            )

        issue_records = load_restored_issue_records()
        detected_adapter = issue_records[0].get("adapter_name") if issue_records else "unknown"

        findings = [
            normalize_restored_issue_to_finding(item, detected_adapter)
            for item in issue_records
        ]

        extracted_issues = []

        for item, finding in zip(issue_records, findings):
            extracted_issues.append(
                {
                    **item,
                    "record_id": record_id,
                    "issueTitle": finding["issueTitle"],
                    "title": finding["title"],
                    "severity": finding["severity"],
                    "system": finding["system"],
                    "component": finding["component"],
                    "source_number": finding["source_number"],
                    "page": finding["page"],
                    "summary_page": finding["summary_page"],
                    "detail_page": finding["detail_page"],
                    "recommendation": finding["recommendation"],
                    "image_url": finding["image_url"],
                    "verified_image_url": finding["verified_image_url"],
                    "verified_image_path": finding["verified_image_path"],
                    "candidate_image_paths": finding["candidate_image_paths"],
                    "candidate_image_urls": finding["candidate_image_urls"],
                    "all_page_image_paths": finding["all_page_image_paths"],
                    "image_match_status": finding["image_match_status"],
                    "image_match_confidence": finding["image_match_confidence"],
                    "needs_image_review": finding["needs_image_review"],
                }
            )

        return {
            "success": True,
            "record_id": record_id,
            "filename": file.filename,
            "message": "PDF parsed by restored previous image-matching workflow",
            "detectedAdapter": detected_adapter,
            "extractedIssues": extracted_issues,
            "findings": findings,
            "findings_count": len(findings),
            "issuesWithImagesCount": sum(1 for item in findings if item.get("image_url")),
            "parser_debug": {
                "workflow": "restored_previous_image_matching_pass_1",
                "used_extract_findings_py": True,
                "used_build_issue_records_py": True,
                "issue_records_count": len(issue_records),
                "image_contract": "suggested_until_admin_verified",
            },
        }

    except HTTPException:
        raise

    except Exception as e:
        print("ANALYZE RESTORED REPORT ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================
# ROUTE: PROCESS INSPECTION
# =========================

@app.post("/process-inspection")
def process_inspection(data: InspectionProcessRequest):
    ensure_core_tables()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        tenant_metadata = get_process_inspection_tenant_metadata(data)
        record_id = clean_text(data.record_id)

        if not record_id:
            raise HTTPException(status_code=400, detail="record_id is required")

        findings = [model_to_dict(finding) for finding in (data.findings or [])]
        skip_s3_upload = bool(getattr(data, "skip_s3_upload", False))

        processing_mode = clean_text(getattr(data, "processing_mode", "")).lower()
        skip_notifications = bool(getattr(data, "skip_notifications", False))

        if processing_mode == "test":
            skip_notifications = True

        if skip_notifications:
            print(f"NOTIFICATION INFO: notifications suppressed for record_id={record_id}")

        if skip_s3_upload:
            print(f"S3 IMAGE INFO: skipping inline S3 upload for record_id={record_id}")
        else:
            findings = upload_findings_images_to_s3(record_id, findings)

        alerts_created = 0
        tasks_created = 0
        verified_issues_created = 0
        verified_issues_existing = 0
        processed_findings = []

        for finding in findings:
            rich_text = " ".join(
                clean_text(finding.get(key))
                for key in ["type", "title", "issueTitle", "system", "component", "location", "notes"]
            )

            finding_type = normalize_key(finding.get("type") or normalize_issue_type(rich_text))
            title = derive_issue_title(finding)
            section = derive_section(finding)
            summary = build_summary(finding, title, section)
            severity = normalize_severity(finding.get("severity"), title, summary)
            risk = risk_fields_from_severity(severity)
            image_url = extract_image_url_from_dict(finding)

            source_number = clean_text(finding.get("source_number") or finding.get("sourceNumber") or finding.get("issueCode"))
            system_name = clean_text(finding.get("system"))
            component_name = clean_text(finding.get("component"))
            page = finding.get("page") or finding.get("page_number") or finding.get("summary_page") or finding.get("detail_page")
            recommendation = clean_text(finding.get("recommendation"))
            detected_adapter = clean_text(finding.get("detectedAdapter") or finding.get("detected_adapter"))

            image_match_status = clean_text(
                finding.get("image_match_status")
                or finding.get("imageMatchStatus")
                or ("suggested" if image_url else "none")
            ).lower()

            image_match_confidence = clean_text(
                finding.get("image_match_confidence")
                or finding.get("imageMatchConfidence")
                or ("page_fallback" if image_url else "no_candidate_found")
            )

            needs_image_review = clean_text(
                finding.get("needs_image_review")
                or finding.get("needsImageReview")
                or "yes"
            ).lower()

            verified_image_url = clean_text(
                finding.get("verified_image_url")
                or finding.get("verifiedImageUrl")
                or ""
            )

            if image_match_status != "verified":
                verified_image_url = ""

            if image_match_status == "verified" and not verified_image_url:
                verified_image_url = image_url

            candidate_image_urls = (
                finding.get("candidate_image_urls")
                or finding.get("candidateImageUrls")
                or finding.get("candidate_image_paths")
                or finding.get("candidateImagePaths")
                or []
            )

            clean_candidate_image_urls = []

            for candidate in candidate_image_urls:
                candidate_url = make_public_image_url(candidate)

                if candidate_url and candidate_url not in clean_candidate_image_urls:
                    clean_candidate_image_urls.append(candidate_url)

            candidate_image_urls = clean_candidate_image_urls

            # Candidate Image Cleanup / Decorative Image Filter Pass 2
            # Remove obvious decorative/report assets before storing new records.
            # Product safety rules:
            # - candidate_image_urls remains an array
            # - verified_image_url is not changed
            # - image_match_status is not marked verified
            # - S3 files are not deleted
            if clean_issue_candidate_images:
                try:
                    image_cleanup_issue = {
                        "image_url": image_url,
                        "candidate_image_urls": candidate_image_urls,
                    }

                    image_cleanup_issue = clean_issue_candidate_images(image_cleanup_issue)

                    candidate_image_urls = image_cleanup_issue.get("candidate_image_urls") or []

                    cleaned_image_url = image_cleanup_issue.get("image_url")
                    if cleaned_image_url:
                        image_url = cleaned_image_url

                except Exception as image_cleanup_error:
                    print("CANDIDATE IMAGE CLEANUP WARNING:", image_cleanup_error)

            candidate_image_urls_json = to_json_or_none(candidate_image_urls)
            dedupe_key = make_dedupe_key(record_id, finding, title, section)
            alert_key = f"{dedupe_key}:alert"
            task_key = f"{dedupe_key}:task"

            cursor.execute(
                """
                INSERT IGNORE INTO alerts
                (record_id, alert_type, severity, message, dedupe_key, status)
                VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (record_id, finding_type, severity, summary, alert_key),
            )

            if cursor.rowcount > 0:
                alerts_created += 1

                if severity in ["high", "critical"]:
                    if skip_notifications:
                        print(
                            "NOTIFICATION SUPPRESSED",
                            f"record_id={record_id}",
                            f"severity={severity}",
                            f"title={title}",
                        )
                    else:
                        notify_record_owner(
                            record_id,
                            f"HomeFax Alert: {title}",
                            f"{title} detected at {section}. {summary}",
                        )
            cursor.execute(
                """
                INSERT IGNORE INTO automation_tasks
                (record_id, task_type, priority, title, description, source, status, dedupe_key)
                VALUES (%s, %s, %s, %s, %s, 'ai_ingestion', 'open', %s)
                """,
                (record_id, finding_type, severity, title, summary, task_key),
            )

            if cursor.rowcount > 0:
                tasks_created += 1

            cursor.execute(
                """
                SELECT id
                FROM verified_issues
                WHERE record_id = %s
                  AND title = %s
                  AND summary = %s
                LIMIT 1
                """,
                (record_id, title, summary),
            )

            existing = cursor.fetchone()

            if existing:
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
                        image_match_status,
                        image_match_confidence,
                        needs_image_review,
                        verified_image_url,
                        candidate_image_urls,
                        created_at,
                        updated_at
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
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
                        %s,
                        %s,
                        %s,
                        %s,
                        CAST(%s AS JSON),
                        NOW(),
                        NOW()
                    )
                    """,
                    (
                        record_id,
                        section,
                        title,
                        summary,
                        image_url,
                        severity,
                        risk["risk_score"],
                        risk["risk_level"],
                        risk["priority"],
                        image_match_status,
                        image_match_confidence,
                        needs_image_review,
                        verified_image_url,
                        candidate_image_urls_json,
                    ),
                )

                verified_issues_created += 1
                verified_issue_created = True

            processed_findings.append(
                {
                    "type": finding_type,
                    "severity": severity,
                    "location": finding.get("location") or section,
                    "section": section,
                    "title": title,
                    "issueTitle": title,
                    "summary": summary,
                    "notes": summary,
                    "image_url": image_url,
                    "verified_image_url": verified_image_url,
                    "image_match_status": image_match_status,
                    "image_match_confidence": image_match_confidence,
                    "needs_image_review": needs_image_review,
                    "candidate_image_urls": candidate_image_urls,
                    "system": system_name,
                    "component": component_name,
                    "source_number": source_number,
                    "page": page,
                    "recommendation": recommendation,
                    "detectedAdapter": detected_adapter,
                    "alert_key": alert_key,
                    "task_key": task_key,
                    "verified_issue_created": verified_issue_created,
                    "risk_score": risk["risk_score"],
                    "risk_level": risk["risk_level"],
                    "priority": risk["priority"],
                }
            )

        conn.commit()

        tenant_metadata_rows_updated = apply_tenant_metadata_to_record(record_id, tenant_metadata)
        tenant_metadata_applied = tenant_metadata_rows_updated > 0

        if skip_s3_upload:
            s3_image_urls_rewritten = 0
        else:
            s3_image_urls_rewritten = rewrite_record_image_urls_to_s3_proxy(record_id)

        return {
            "success": True,
            "tenant_metadata": tenant_metadata,
            "tenant_metadata_applied": tenant_metadata_applied,
            "tenant_metadata_rows_updated": tenant_metadata_rows_updated,
            "s3_image_urls_rewritten": s3_image_urls_rewritten,
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
# VERIFIED ISSUES API
# =========================

def normalize_verified_issue_row(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}

    def iso(value):
        return value.isoformat() if value else None

    candidate_image_urls = row.get("candidate_image_urls")

    if isinstance(candidate_image_urls, str):
        try:
            candidate_image_urls = json.loads(candidate_image_urls)
        except Exception:
            candidate_image_urls = []

    return {
        "id": row.get("id"),
        "record_id": row.get("record_id"),
        "section": row.get("section"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "image_url": row.get("image_url") or "",
        "verified_image_url": row.get("verified_image_url") or "",
        "image_match_status": row.get("image_match_status") or "suggested",
        "image_match_confidence": row.get("image_match_confidence") or "page_fallback",
        "needs_image_review": row.get("needs_image_review") or "yes",
        "candidate_image_urls": candidate_image_urls or [],
        "severity": row.get("severity"),
        "status": row.get("status"),
        "homeowner_decision": row.get("homeowner_decision"),
        "homeowner_note": row.get("homeowner_note") or "",
        # Verified Issues Homeowner Selected Image Normalizer Patch
        "homeowner_image_decision": row.get("homeowner_image_decision") or "unreviewed",
        "homeowner_selected_image_url": row.get("homeowner_selected_image_url") or "",
        "homeowner_selected_image_note": row.get("homeowner_selected_image_note") or "",
        "homeowner_selected_image_updated_at": row.get("homeowner_selected_image_updated_at").isoformat() if row.get("homeowner_selected_image_updated_at") else None,
        "homeowner_reviewed_at": (
            row.get("homeowner_reviewed_at").isoformat()
            if row.get("homeowner_reviewed_at")
            else None
        ),
        "admin_review_status": row.get("admin_review_status"),
        "admin_image_decision": row.get("admin_image_decision") or "pending",
        "admin_reviewed_at": (
            row.get("admin_reviewed_at").isoformat()
            if row.get("admin_reviewed_at")
            else None
        ),
        "admin_note": row.get("admin_note") or "",
        "final_approval_status": row.get("final_approval_status") or "not_approved",
        "final_approved_at": (
            row.get("final_approved_at").isoformat()
            if row.get("final_approved_at")
            else None
        ),
        "final_approved_by": row.get("final_approved_by") or "",
        "baseline_locked": row.get("baseline_locked"),
        "baseline_locked_at": iso(row.get("baseline_locked_at")),
        "current_status": row.get("current_status"),
        "resolved_by_event_id": row.get("resolved_by_event_id"),
        "risk_score": row.get("risk_score"),
        "risk_level": row.get("risk_level"),
        "priority": row.get("priority"),
        "created_at": iso(row.get("created_at")),
        "updated_at": iso(row.get("updated_at")),
    }


@app.get("/verified-issues")
def list_verified_issues(limit: int = 200, offset: int = 0):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    ensure_core_tables()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) AS total FROM verified_issues")
        total = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT *
            FROM verified_issues
            ORDER BY updated_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )

        rows = cursor.fetchall()

        return {
            "success": True,
            "total": total,
            "limit": limit,
            "offset": offset,
            "count": len(rows),
            "issues": [normalize_verified_issue_row(row) for row in rows],
        }

    except Exception as e:
        print("ERROR IN /verified-issues:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.get("/verified-issues-records")
def list_verified_issue_records():
    ensure_core_tables()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT
                record_id,
                COUNT(*) AS count,
                MAX(updated_at) AS latest_updated_at
            FROM verified_issues
            GROUP BY record_id
            ORDER BY latest_updated_at DESC
            LIMIT 250
            """
        )

        rows = cursor.fetchall()

        records = [
            {
                "record_id": row["record_id"],
                "label": row["record_id"],
                "count": row["count"],
                "latest_updated_at": row["latest_updated_at"].isoformat() if row["latest_updated_at"] else None,
            }
            for row in rows
        ]

        return {
            "success": True,
            "records": records,
        }

    except Exception as e:
        print("ERROR IN /verified-issues-records:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.get("/verified-issues/{record_id}")
def get_verified_issues_by_record(record_id: str):
    ensure_core_tables()

    record_id = clean_text(record_id)

    if not record_id:
        raise HTTPException(status_code=400, detail="record_id is required")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM verified_issues
            WHERE record_id = %s
            ORDER BY
                CASE
                    WHEN risk_level = 'CRITICAL' THEN 1
                    WHEN risk_level = 'HIGH' THEN 2
                    WHEN risk_level = 'MEDIUM' THEN 3
                    WHEN risk_level = 'LOW' THEN 4
                    ELSE 5
                END,
                id ASC
            """,
            (record_id,),
        )

        rows = cursor.fetchall()

        return {
            "success": True,
            "record_id": record_id,
            "count": len(rows),
            "issues": [normalize_verified_issue_row(row) for row in rows],
        }

    except Exception as e:
        print("ERROR IN /verified-issues/{record_id}:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.get("/verified-issue/{issue_id}")
def get_verified_issue(issue_id: int):
    ensure_core_tables()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Verified issue not found")

        return {
            "success": True,
            "issue": normalize_verified_issue_row(row),
        }

    except HTTPException:
        raise

    except Exception as e:
        print("ERROR IN /verified-issue/{issue_id}:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


def update_verified_issue_common(issue_id: int, update: VerifiedIssueStatusUpdate):
    # Baseline Lock API Guard Pass 1B
    _hf_guard_verified_issue_not_baseline_locked(issue_id, "legacy-status-alias")
    fields = []
    params = []

    allowed_statuses = {"new", "active", "dismissed", "resolved"}
    allowed_current_statuses = {
        "open",
        "monitoring",
        "scheduled",
        "repaired",
        "verified",
        "resolved",
        "needs_repair",
        "repair_scheduled",
    }
    allowed_homeowner_decisions = {
        "unreviewed",
        "accepted",
        "rejected",
        "needs_help",
        "needs_repair",
        "monitor",
        "already_fixed",
        "not_a_concern",
        "image_mismatch",
    }
    allowed_admin_review_statuses = {
        "pending",
        "approved",
        "rejected",
        "needs_review",
        "resolved",
    }

    if update.status is not None:
        value = update.status.lower().strip()
        if value not in allowed_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status: {value}")
        fields.append("status = %s")
        params.append(value)

    if update.current_status is not None:
        value = update.current_status.lower().strip()
        if value not in allowed_current_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid current_status: {value}")
        fields.append("current_status = %s")
        params.append(value)

        if value == "resolved":
            fields.append("status = %s")
            params.append("resolved")

    if update.homeowner_decision is not None:
        value = update.homeowner_decision.lower().strip()
        if value not in allowed_homeowner_decisions:
            raise HTTPException(status_code=400, detail=f"Invalid homeowner_decision: {value}")
        fields.append("homeowner_decision = %s")
        params.append(value)

    if update.homeowner_note is not None:
        fields.append("homeowner_note = %s")
        params.append(update.homeowner_note)

    if update.admin_review_status is not None:
        value = update.admin_review_status.lower().strip()
        if value not in allowed_admin_review_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid admin_review_status: {value}")
        fields.append("admin_review_status = %s")
        params.append(value)

    if update.admin_note is not None:
        fields.append("admin_note = %s")
        params.append(update.admin_note)

    if not fields:
        raise HTTPException(status_code=400, detail="No update fields provided")

    fields.append("updated_at = NOW()")
    params.append(issue_id)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        existing = cursor.fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Verified issue not found")

        cursor.execute(
            f"""
            UPDATE verified_issues
            SET {", ".join(fields)}
            WHERE id = %s
            """,
            params,
        )

        conn.commit()

        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        row = cursor.fetchone()

        return {
            "success": True,
            "message": "Verified issue updated",
            "issue": normalize_verified_issue_row(row),
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("ERROR updating verified issue:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.patch("/verified-issue/{issue_id}/status")
def update_verified_issue_status(issue_id: int, update: VerifiedIssueStatusUpdate):
    return update_verified_issue_common(issue_id, update)


@app.patch("/verified-issues/{issue_id}")
def patch_verified_issue_alias(issue_id: int, update: VerifiedIssueStatusUpdate):
    return update_verified_issue_common(issue_id, update)


@app.put("/verified-issues/{issue_id}")
def put_verified_issue_alias(issue_id: int, update: VerifiedIssueStatusUpdate):
    return update_verified_issue_common(issue_id, update)



# Baseline Lock API Guard Pass 1
def _hf_truthy_lock_value(value):
    return str(value or "").strip().lower() in {
        "yes",
        "true",
        "1",
        "locked",
        "approved",
        "final_approved",
    }


def _hf_guard_verified_issue_not_baseline_locked(issue_id: int, action_name: str = "update"):
    """
    Prevent mutation of verified issues after final baseline lock.

    This protects the audit trail. Once an issue is final-approved or baseline-locked,
    client/UI bugs, direct curl calls, or automation retries cannot mutate review state.
    """
    conn = None

    try:
        conn = _hf_mon_get_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    baseline_locked,
                    final_approval_status,
                    title
                FROM verified_issues
                WHERE id = %s
                LIMIT 1
                """,
                (issue_id,),
            )
            row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Verified issue {issue_id} was not found.",
            )

        baseline_locked = row.get("baseline_locked")
        final_approval_status = row.get("final_approval_status")

        if _hf_truthy_lock_value(baseline_locked) or _hf_truthy_lock_value(final_approval_status):
            raise HTTPException(
                status_code=409,
                detail={
                    "success": False,
                    "error": "baseline_locked",
                    "message": "This issue is baseline locked and cannot be changed.",
                    "issue_id": issue_id,
                    "title": row.get("title"),
                    "action": action_name,
                    "baseline_locked": baseline_locked,
                    "final_approval_status": final_approval_status,
                },
            )

        return row

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "baseline_lock_guard_failed",
                "message": str(exc),
                "issue_id": issue_id,
                "action": action_name,
            },
        )
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass



@app.patch("/verified-issue/{issue_id}/image-verification")
def update_issue_image_verification(issue_id: int, update: ImageVerificationUpdate):
    _hf_guard_verified_issue_not_baseline_locked(issue_id, "image-verification")
    ensure_core_tables()

    allowed_statuses = {"none", "suggested", "verified", "mismatch"}

    status = clean_text(update.image_match_status).lower()

    if status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image_match_status. Allowed: {sorted(allowed_statuses)}",
        )

    verified_image_url = clean_text(update.verified_image_url or "")
    admin_note = clean_text(update.admin_note or "")
    needs_image_review = "no" if status == "verified" else "yes"

    if status == "verified":
        admin_image_decision = "approved"
    elif status == "mismatch":
        admin_image_decision = "mismatch"
    elif status == "suggested":
        admin_image_decision = "needs_review"
    else:
        admin_image_decision = "pending"

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT id, image_url
            FROM verified_issues
            WHERE id = %s
            LIMIT 1
            """,
            (issue_id,),
        )

        existing = cursor.fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Verified issue not found")

        if status == "verified" and not verified_image_url:
            verified_image_url = existing.get("image_url") or ""

        cursor.execute(
            """
            UPDATE verified_issues
            SET
                image_match_status = %s,
                verified_image_url = %s,
                needs_image_review = %s,
                admin_image_decision = %s,
                admin_note = CASE
                    WHEN %s != '' THEN %s
                    ELSE admin_note
                END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                status,
                verified_image_url,
                needs_image_review,
                admin_image_decision,
                admin_note,
                admin_note,
                issue_id,
            ),
        )

        conn.commit()

        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        row = cursor.fetchone()

        return {
            "success": True,
            "message": "Image verification updated",
            "issue": normalize_verified_issue_row(row),
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("ERROR IN /verified-issue/{issue_id}/image-verification:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# =========================
# HOMEOWNER + ADMIN VERIFICATION WORKFLOW CONTRACT PASS 1
# =========================

class HomeownerReviewUpdate(BaseModel):
    homeowner_decision: str
    homeowner_image_decision: Optional[str] = "unreviewed"
    homeowner_note: Optional[str] = ""


class AdminReviewUpdate(BaseModel):
    admin_review_status: str
    admin_image_decision: Optional[str] = "pending"
    verified_image_url: Optional[str] = ""
    admin_note: Optional[str] = ""


class FinalApprovalUpdate(BaseModel):
    final_approval_status: str = "approved"
    final_approved_by: Optional[str] = "admin"
    admin_note: Optional[str] = ""


def ensure_review_workflow_schema():
    """
    Adds homeowner/admin/final approval fields without dropping existing data.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        add_column_if_missing(
            cursor,
            "verified_issues",
            "homeowner_image_decision",
            "homeowner_image_decision VARCHAR(100) DEFAULT 'unreviewed'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "homeowner_reviewed_at",
            "homeowner_reviewed_at DATETIME NULL",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "admin_image_decision",
            "admin_image_decision VARCHAR(100) DEFAULT 'pending'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "admin_reviewed_at",
            "admin_reviewed_at DATETIME NULL",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "final_approval_status",
            "final_approval_status VARCHAR(100) DEFAULT 'not_approved'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "final_approved_at",
            "final_approved_at DATETIME NULL",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "final_approved_by",
            "final_approved_by VARCHAR(255) DEFAULT ''",
        )

        conn.commit()

    finally:
        cursor.close()
        conn.close()


def normalize_issue_with_review_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keeps compatibility with the existing dashboard response while adding
    homeowner/admin/final approval workflow fields.
    """
    issue = normalize_verified_issue_row(row)

    def iso(value):
        return value.isoformat() if value else None

    issue.update(
        {
            "homeowner_image_decision": row.get("homeowner_image_decision") or "unreviewed",
            "homeowner_reviewed_at": iso(row.get("homeowner_reviewed_at")),
            "admin_image_decision": row.get("admin_image_decision") or "pending",
            "admin_reviewed_at": iso(row.get("admin_reviewed_at")),
            "final_approval_status": row.get("final_approval_status") or "not_approved",
            "final_approved_at": iso(row.get("final_approved_at")),
            "final_approved_by": row.get("final_approved_by") or "",
        }
    )

    return issue


def fetch_verified_issue_row(issue_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        return cursor.fetchone()

    finally:
        cursor.close()
        conn.close()


@app.patch("/verified-issue/{issue_id}/homeowner-review")
def submit_homeowner_issue_review(issue_id: int, update: HomeownerReviewUpdate):
    _hf_guard_verified_issue_not_baseline_locked(issue_id, "homeowner-review")
    """
    Homeowner review route.

    Homeowner can:
      - confirm issue
      - say needs repair
      - monitor
      - already fixed
      - not a concern
      - flag image mismatch
      - add note

    Homeowner does NOT final-approve or lock baseline.
    """
    ensure_core_tables()
    ensure_review_workflow_schema()

    allowed_homeowner_decisions = {
        "unreviewed",
        "confirmed",
        "accepted",
        "needs_repair",
        "monitor",
        "already_fixed",
        "not_a_concern",
        "image_mismatch",
        "rejected",
        "needs_help",
    }

    allowed_image_decisions = {
        "unreviewed",
        "accepted",
        "mismatch",
        "needs_review",
        "no_image",
    }

    homeowner_decision = clean_text(update.homeowner_decision).lower()
    homeowner_image_decision = clean_text(update.homeowner_image_decision or "unreviewed").lower()
    homeowner_note = clean_text(update.homeowner_note or "")

    if homeowner_decision not in allowed_homeowner_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid homeowner_decision. Allowed: {sorted(allowed_homeowner_decisions)}",
        )

    if homeowner_image_decision not in allowed_image_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid homeowner_image_decision. Allowed: {sorted(allowed_image_decisions)}",
        )

    # Map homeowner decision to current status.
    current_status = "open"

    if homeowner_decision == "needs_repair":
        current_status = "needs_repair"
    elif homeowner_decision == "monitor":
        current_status = "monitoring"
    elif homeowner_decision == "already_fixed":
        current_status = "repaired"
    elif homeowner_decision in {"not_a_concern", "rejected"}:
        current_status = "needs_review"
    elif homeowner_decision in {"confirmed", "accepted"}:
        current_status = "open"

    # Homeowner review always sends to admin queue.
    admin_review_status = "needs_review"

    image_match_status_sql = ""
    image_match_params = []

    if homeowner_decision == "image_mismatch" or homeowner_image_decision == "mismatch":
        image_match_status_sql = """
            image_match_status = %s,
            needs_image_review = %s,
            verified_image_url = %s,
        """
        image_match_params = ["mismatch", "yes", ""]

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        existing = cursor.fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Verified issue not found")

        cursor.execute(
            f"""
            UPDATE verified_issues
            SET
                homeowner_decision = %s,
                homeowner_image_decision = %s,
                homeowner_note = %s,
                homeowner_reviewed_at = NOW(),
                admin_review_status = %s,
                current_status = %s,
                {image_match_status_sql}
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                homeowner_decision,
                homeowner_image_decision,
                homeowner_note,
                admin_review_status,
                current_status,
                *image_match_params,
                issue_id,
            ),
        )

        conn.commit()

        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        row = cursor.fetchone()

        return {
            "success": True,
            "message": "Homeowner review saved. Issue is now queued for admin review.",
            "issue": normalize_issue_with_review_fields(row),
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("ERROR IN /verified-issue/{issue_id}/homeowner-review:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.patch("/verified-issue/{issue_id}/admin-review")
def submit_admin_issue_review(issue_id: int, update: AdminReviewUpdate):
    _hf_guard_verified_issue_not_baseline_locked(issue_id, "admin-review")
    """
    Admin review route.

    Admin can:
      - approve finding
      - reject finding
      - request more review
      - approve image
      - mark image mismatch

    This does NOT lock baseline yet. Final approval route does that.
    """
    ensure_core_tables()
    ensure_review_workflow_schema()

    allowed_admin_statuses = {
        "pending",
        "approved",
        "rejected",
        "needs_review",
        "send_back",
        "dismissed",
        "resolved",
    }

    allowed_admin_image_decisions = {
        "pending",
        "approved",
        "mismatch",
        "needs_review",
        "no_image",
    }

    admin_review_status = clean_text(update.admin_review_status).lower()
    admin_image_decision = clean_text(update.admin_image_decision or "pending").lower()
    verified_image_url = clean_text(update.verified_image_url or "")
    admin_note = clean_text(update.admin_note or "")

    if admin_review_status not in allowed_admin_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid admin_review_status. Allowed: {sorted(allowed_admin_statuses)}",
        )

    if admin_image_decision not in allowed_admin_image_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid admin_image_decision. Allowed: {sorted(allowed_admin_image_decisions)}",
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        existing = cursor.fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Verified issue not found")

        image_match_status = existing.get("image_match_status") or "suggested"
        needs_image_review = existing.get("needs_image_review") or "yes"

        if admin_image_decision == "approved":
            image_match_status = "verified"
            needs_image_review = "no"

            if not verified_image_url:
                verified_image_url = existing.get("image_url") or ""

        elif admin_image_decision == "mismatch":
            image_match_status = "mismatch"
            needs_image_review = "yes"
            verified_image_url = ""

        elif admin_image_decision in {"needs_review", "pending"}:
            if image_match_status != "verified":
                image_match_status = "suggested" if existing.get("image_url") else "none"
                needs_image_review = "yes"
                verified_image_url = ""

        # Do not baseline-lock here.
        final_approval_status = "not_approved"

        if admin_review_status in {"rejected", "dismissed"}:
            final_approval_status = "rejected"

        cursor.execute(
            """
            UPDATE verified_issues
            SET
                admin_review_status = %s,
                admin_image_decision = %s,
                admin_note = CASE
                    WHEN %s != '' THEN %s
                    ELSE admin_note
                END,
                admin_reviewed_at = NOW(),
                image_match_status = %s,
                verified_image_url = %s,
                needs_image_review = %s,
                final_approval_status = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                admin_review_status,
                admin_image_decision,
                admin_note,
                admin_note,
                image_match_status,
                verified_image_url,
                needs_image_review,
                final_approval_status,
                issue_id,
            ),
        )

        conn.commit()

        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        row = cursor.fetchone()

        return {
            "success": True,
            "message": "Admin review saved. Use final approval to lock this issue into the baseline.",
            "issue": normalize_issue_with_review_fields(row),
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("ERROR IN /verified-issue/{issue_id}/admin-review:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.patch("/verified-issue/{issue_id}/final-approval")
def final_approve_verified_issue(issue_id: int, update: FinalApprovalUpdate):
    _hf_guard_verified_issue_not_baseline_locked(issue_id, "final-approval")
    """
    Final platform approval route.

    This is the official admin lock:
      admin_review_status must be approved
      image must either be verified, no_image, or intentionally needs_review
      baseline_locked becomes yes
    """
    ensure_core_tables()
    ensure_review_workflow_schema()

    allowed_final_statuses = {
        "approved",
        "rejected",
        "needs_review",
    }

    final_approval_status = clean_text(update.final_approval_status or "approved").lower()
    final_approved_by = clean_text(update.final_approved_by or "admin")
    admin_note = clean_text(update.admin_note or "")

    if final_approval_status not in allowed_final_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid final_approval_status. Allowed: {sorted(allowed_final_statuses)}",
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        existing = cursor.fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Verified issue not found")

        current_admin_status = existing.get("admin_review_status") or "pending"

        if final_approval_status == "approved" and current_admin_status != "approved":
            raise HTTPException(
                status_code=400,
                detail="Admin review must be approved before final approval.",
            )

        if final_approval_status == "approved":
            baseline_locked = "yes"
            status = "active"
            current_status = existing.get("current_status") or "open"
            baseline_locked_at_sql = "NOW()"
            final_approved_at_sql = "NOW()"
        elif final_approval_status == "rejected":
            baseline_locked = "no"
            status = "dismissed"
            current_status = "closed"
            baseline_locked_at_sql = "NULL"
            final_approved_at_sql = "NOW()"
        else:
            baseline_locked = "no"
            status = existing.get("status") or "new"
            current_status = "needs_review"
            baseline_locked_at_sql = "NULL"
            final_approved_at_sql = "NULL"

        cursor.execute(
            f"""
            UPDATE verified_issues
            SET
                final_approval_status = %s,
                final_approved_by = %s,
                final_approved_at = {final_approved_at_sql},
                baseline_locked = %s,
                baseline_locked_at = {baseline_locked_at_sql},
                status = %s,
                current_status = %s,
                admin_note = CASE
                    WHEN %s != '' THEN %s
                    ELSE admin_note
                END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                final_approval_status,
                final_approved_by,
                baseline_locked,
                status,
                current_status,
                admin_note,
                admin_note,
                issue_id,
            ),
        )

        conn.commit()

        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        row = cursor.fetchone()

        # HomeFax Monitoring Lifecycle Backend Pass 2
        # Auto-create/update a monitoring plan when a monitored issue is final approved and baseline locked.
        monitoring_lifecycle = {
            "checked": True,
            "created_or_updated": False,
            "reason": "",
            "monitoring_plan": None,
            "allowed_capabilities": [],
        }

        try:
            final_is_approved = final_approval_status == "approved"
            baseline_is_locked = baseline_locked == "yes"

            if final_is_approved and baseline_is_locked:
                issue_for_monitoring = row or existing
                should_monitor = _hf_mon_issue_should_monitor(issue_for_monitoring, force=False)

                if should_monitor:
                    monitoring_result = _hf_mon_create_or_update_plan_from_issue(issue_id, force=True)
                    monitoring_lifecycle = {
                        "checked": True,
                        "created_or_updated": True,
                        "reason": "final_approval_lock_monitoring_issue",
                        "monitoring_plan": monitoring_result.get("plan"),
                        "allowed_capabilities": monitoring_result.get("allowed_capabilities", []),
                    }

                    # Re-fetch row after monitoring_plan_id/current_status update.
                    cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
                    row = cursor.fetchone()
                else:
                    monitoring_lifecycle["reason"] = "issue_not_marked_for_monitoring"
            else:
                monitoring_lifecycle["reason"] = "final_approval_not_locked_or_not_approved"

        except Exception as monitoring_error:
            monitoring_lifecycle = {
                "checked": True,
                "created_or_updated": False,
                "reason": "monitoring_plan_creation_failed",
                "error": str(monitoring_error),
                "monitoring_plan": None,
                "allowed_capabilities": [],
            }

        return {
            "success": True,
            "message": "Final approval status updated.",
            "monitoring_lifecycle": monitoring_lifecycle,
            "issue": normalize_issue_with_review_fields(row),
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("ERROR IN /verified-issue/{issue_id}/final-approval:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.get("/verified-issues-review-queue")
def get_verified_issues_review_queue(limit: int = 200, offset: int = 0):
    """
    Admin queue route.

    Returns issues where homeowner has responded or image/finding needs admin review,
    but final approval is not complete.
    """
    ensure_core_tables()
    ensure_review_workflow_schema()

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM verified_issues
            WHERE
                COALESCE(final_approval_status, 'not_approved') != 'approved'
                AND COALESCE(hidden_from_review_queue, 'no') != 'yes'
                AND (
                    COALESCE(homeowner_decision, 'unreviewed') != 'unreviewed'
                    OR COALESCE(homeowner_image_decision, 'unreviewed') != 'unreviewed'
                    OR COALESCE(admin_review_status, 'pending') IN ('needs_review', 'pending')
                    OR COALESCE(needs_image_review, 'yes') = 'yes'
                    OR COALESCE(image_match_status, 'suggested') IN ('suggested', 'mismatch')
                )
            """
        )

        total = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT *
            FROM verified_issues
            WHERE
                COALESCE(final_approval_status, 'not_approved') != 'approved'
                AND COALESCE(hidden_from_review_queue, 'no') != 'yes'
                AND (
                    COALESCE(homeowner_decision, 'unreviewed') != 'unreviewed'
                    OR COALESCE(homeowner_image_decision, 'unreviewed') != 'unreviewed'
                    OR COALESCE(admin_review_status, 'pending') IN ('needs_review', 'pending')
                    OR COALESCE(needs_image_review, 'yes') = 'yes'
                    OR COALESCE(image_match_status, 'suggested') IN ('suggested', 'mismatch')
                )
            ORDER BY
                CASE
                    WHEN risk_level = 'CRITICAL' THEN 1
                    WHEN risk_level = 'HIGH' THEN 2
                    WHEN risk_level = 'MEDIUM' THEN 3
                    WHEN risk_level = 'LOW' THEN 4
                    ELSE 5
                END,
                updated_at DESC,
                id DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )

        rows = cursor.fetchall()

        return {
            "success": True,
            "total": total,
            "limit": limit,
            "offset": offset,
            "count": len(rows),
            "issues": [normalize_issue_with_review_fields(row) for row in rows],
        }

    except Exception as e:
        print("ERROR IN /verified-issues-review-queue:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.get("/verification-workflow-health")
def verification_workflow_health():
    """
    Quick schema/status check for the homeowner/admin verification workflow.
    """
    ensure_core_tables()
    ensure_review_workflow_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        columns = get_table_columns(cursor, "verified_issues")

        required = [
            "homeowner_image_decision",
            "homeowner_reviewed_at",
            "admin_image_decision",
            "admin_reviewed_at",
            "final_approval_status",
            "final_approved_at",
            "final_approved_by",
        ]

        missing = [column for column in required if column not in columns]

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(homeowner_decision, 'unreviewed') != 'unreviewed' THEN 1 ELSE 0 END) AS homeowner_reviewed,
                SUM(CASE WHEN COALESCE(admin_review_status, 'pending') = 'approved' THEN 1 ELSE 0 END) AS admin_approved,
                SUM(CASE WHEN COALESCE(final_approval_status, 'not_approved') = 'approved' THEN 1 ELSE 0 END) AS final_approved,
                SUM(CASE WHEN COALESCE(baseline_locked, 'no') = 'yes' THEN 1 ELSE 0 END) AS baseline_locked
            FROM verified_issues
            """
        )

        stats = cursor.fetchone()

        return {
            "success": True,
            "schema_ready": len(missing) == 0,
            "missing_columns": missing,
            "stats": stats,
        }

    finally:
        cursor.close()
        conn.close()


# =========================
# REVIEW QUEUE CLEANUP + TEST RECORD HYGIENE PASS 1
# =========================

class ReviewQueueCleanupRequest(BaseModel):
    record_id: Optional[str] = None
    reason: Optional[str] = "cleanup"
    admin_note: Optional[str] = ""
    cleanup_mode: Optional[str] = "hide_from_review_queue"


class ReviewQueueDismissIssueRequest(BaseModel):
    reason: Optional[str] = "dismissed_from_review_queue"
    admin_note: Optional[str] = ""


def ensure_review_queue_cleanup_schema():
    """
    Adds cleanup/hygiene fields without deleting historical records.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        add_column_if_missing(
            cursor,
            "verified_issues",
            "hidden_from_review_queue",
            "hidden_from_review_queue VARCHAR(10) DEFAULT 'no'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "cleanup_reason",
            "cleanup_reason VARCHAR(255) DEFAULT ''",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "cleanup_at",
            "cleanup_at DATETIME NULL",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "cleanup_by",
            "cleanup_by VARCHAR(255) DEFAULT ''",
        )

        conn.commit()

    finally:
        cursor.close()
        conn.close()


@app.get("/review-queue-cleanup-health")
def review_queue_cleanup_health():
    """
    Confirms cleanup fields exist and gives queue hygiene stats.
    """
    ensure_core_tables()
    ensure_review_workflow_schema()
    ensure_review_queue_cleanup_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        columns = get_table_columns(cursor, "verified_issues")

        required = [
            "hidden_from_review_queue",
            "cleanup_reason",
            "cleanup_at",
            "cleanup_by",
        ]

        missing = [column for column in required if column not in columns]

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(hidden_from_review_queue, 'no') = 'yes' THEN 1 ELSE 0 END) AS hidden_from_queue,
                SUM(CASE WHEN record_id LIKE 'detail-recovery-test-%' THEN 1 ELSE 0 END) AS detail_recovery_tests,
                SUM(CASE WHEN record_id LIKE 'hybrid-image-match-test-%' THEN 1 ELSE 0 END) AS hybrid_tests,
                SUM(CASE WHEN record_id LIKE 'restored-image-match-test-%' THEN 1 ELSE 0 END) AS restored_tests,
                SUM(CASE WHEN record_id LIKE 'pdf-fullreportforupload-%' THEN 1 ELSE 0 END) AS old_fullreport_uploads,
                SUM(CASE WHEN title LIKE '%%inspector is not required%%' THEN 1 ELSE 0 END) AS inspector_not_required_noise,
                SUM(CASE WHEN title LIKE '%%Inspect erosion control%%' THEN 1 ELSE 0 END) AS erosion_control_noise
            FROM verified_issues
            """
        )

        stats = cursor.fetchone()

        return {
            "success": True,
            "schema_ready": len(missing) == 0,
            "missing_columns": missing,
            "stats": stats,
        }

    finally:
        cursor.close()
        conn.close()


@app.post("/review-queue-cleanup/hide-old-noise")
def hide_old_noise_from_review_queue():
    """
    Hides known old parser-noise records from the admin review queue.

    This does NOT delete records.
    It marks them hidden and dismissed so the review queue is useful again.
    """
    ensure_core_tables()
    ensure_review_workflow_schema()
    ensure_review_queue_cleanup_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE verified_issues
            SET
                hidden_from_review_queue = 'yes',
                cleanup_reason = 'old_parser_noise_hidden',
                cleanup_at = NOW(),
                cleanup_by = 'admin_cleanup_pass_1',
                admin_review_status = CASE
                    WHEN COALESCE(admin_review_status, 'pending') = 'approved' THEN admin_review_status
                    ELSE 'dismissed'
                END,
                final_approval_status = CASE
                    WHEN COALESCE(final_approval_status, 'not_approved') = 'approved' THEN final_approval_status
                    ELSE 'rejected'
                END,
                status = CASE
                    WHEN COALESCE(status, 'new') = 'active' THEN status
                    ELSE 'dismissed'
                END,
                current_status = CASE
                    WHEN COALESCE(current_status, 'open') IN ('resolved', 'repaired') THEN current_status
                    ELSE 'closed'
                END,
                admin_note = CASE
                    WHEN COALESCE(admin_note, '') = '' THEN 'Hidden by review queue cleanup pass: old parser/test noise.'
                    ELSE CONCAT(admin_note, ' | Hidden by review queue cleanup pass: old parser/test noise.')
                END,
                updated_at = NOW()
            WHERE
                COALESCE(final_approval_status, 'not_approved') != 'approved'
                AND COALESCE(baseline_locked, 'no') != 'yes'
                AND (
                    record_id LIKE 'pdf-fullreportforupload-%%'
                    OR title LIKE '%%inspector is not required%%'
                    OR title LIKE '%%Inspect erosion control%%'
                    OR title LIKE '%%Perform a water test%%'
                    OR title LIKE '%%warrant or certify%%'
                    OR summary LIKE '%%The inspector is not required to%%'
                    OR summary LIKE '%%Standards of Practice%%'
                    OR summary LIKE '%%Inspect erosion control%%'
                )
            """
        )

        affected = cursor.rowcount
        conn.commit()

        return {
            "success": True,
            "message": "Old parser/test noise hidden from review queue.",
            "affected_rows": affected,
        }

    except Exception as e:
        conn.rollback()
        print("ERROR IN /review-queue-cleanup/hide-old-noise:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.post("/review-queue-cleanup/hide-record")
def hide_record_from_review_queue(update: ReviewQueueCleanupRequest):
    """
    Hides every non-final-approved issue for a specific record_id.

    Use this for test records you do not want in the active admin queue.
    """
    ensure_core_tables()
    ensure_review_workflow_schema()
    ensure_review_queue_cleanup_schema()

    record_id = clean_text(update.record_id or "")
    reason = clean_text(update.reason or "record_hidden_from_review_queue")
    admin_note = clean_text(update.admin_note or "")

    if not record_id:
        raise HTTPException(status_code=400, detail="record_id is required")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE verified_issues
            SET
                hidden_from_review_queue = 'yes',
                cleanup_reason = %s,
                cleanup_at = NOW(),
                cleanup_by = 'admin_cleanup_pass_1',
                admin_review_status = CASE
                    WHEN COALESCE(admin_review_status, 'pending') = 'approved' THEN admin_review_status
                    ELSE 'dismissed'
                END,
                final_approval_status = CASE
                    WHEN COALESCE(final_approval_status, 'not_approved') = 'approved' THEN final_approval_status
                    ELSE 'rejected'
                END,
                status = CASE
                    WHEN COALESCE(status, 'new') = 'active' THEN status
                    ELSE 'dismissed'
                END,
                current_status = CASE
                    WHEN COALESCE(current_status, 'open') IN ('resolved', 'repaired') THEN current_status
                    ELSE 'closed'
                END,
                admin_note = CASE
                    WHEN %s != '' THEN %s
                    WHEN COALESCE(admin_note, '') = '' THEN 'Hidden by review queue cleanup pass.'
                    ELSE CONCAT(admin_note, ' | Hidden by review queue cleanup pass.')
                END,
                updated_at = NOW()
            WHERE
                record_id = %s
                AND COALESCE(final_approval_status, 'not_approved') != 'approved'
                AND COALESCE(baseline_locked, 'no') != 'yes'
            """,
            (
                reason,
                admin_note,
                admin_note,
                record_id,
            ),
        )

        affected = cursor.rowcount
        conn.commit()

        return {
            "success": True,
            "message": "Record hidden from review queue.",
            "record_id": record_id,
            "affected_rows": affected,
        }

    except Exception as e:
        conn.rollback()
        print("ERROR IN /review-queue-cleanup/hide-record:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.patch("/verified-issue/{issue_id}/hide-from-review-queue")
def hide_single_issue_from_review_queue(issue_id: int, update: ReviewQueueDismissIssueRequest):
    _hf_guard_verified_issue_not_baseline_locked(issue_id, "hide-from-review-queue")
    """
    Hides one issue from the admin review queue without deleting it.
    """
    ensure_core_tables()
    ensure_review_workflow_schema()
    ensure_review_queue_cleanup_schema()

    reason = clean_text(update.reason or "issue_hidden_from_review_queue")
    admin_note = clean_text(update.admin_note or "")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        existing = cursor.fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail="Verified issue not found")

        if existing.get("final_approval_status") == "approved" or existing.get("baseline_locked") == "yes":
            raise HTTPException(
                status_code=400,
                detail="Final-approved/baseline-locked issues cannot be hidden by cleanup.",
            )

        cursor.execute(
            """
            UPDATE verified_issues
            SET
                hidden_from_review_queue = 'yes',
                cleanup_reason = %s,
                cleanup_at = NOW(),
                cleanup_by = 'admin_cleanup_pass_1',
                admin_review_status = 'dismissed',
                final_approval_status = 'rejected',
                status = 'dismissed',
                current_status = 'closed',
                admin_note = CASE
                    WHEN %s != '' THEN %s
                    WHEN COALESCE(admin_note, '') = '' THEN 'Hidden by review queue cleanup pass.'
                    ELSE CONCAT(admin_note, ' | Hidden by review queue cleanup pass.')
                END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                reason,
                admin_note,
                admin_note,
                issue_id,
            ),
        )

        conn.commit()

        cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
        row = cursor.fetchone()

        return {
            "success": True,
            "message": "Issue hidden from review queue.",
            "issue": normalize_issue_with_review_fields(row),
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("ERROR IN /verified-issue/{issue_id}/hide-from-review-queue:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# =========================
# DYNAMIC AI ADAPTER LEARNING PASS 1
# =========================
#
# Purpose:
#   Unknown / weakly matched report
#   -> AI fallback extracts findings
#   -> system saves adapter-learning run metadata
#   -> admin reviews learning run
#   -> admin can promote it into a reusable dynamic adapter profile
#
# Product rule:
#   AI can suggest.
#   Admin must approve/promote.
#   Only approved profiles become reusable.
#
# This pass creates database-backed profiles.
# It does NOT automatically generate Python adapter files yet.

import json
from typing import Any, Dict, Optional

from fastapi import HTTPException
from pydantic import BaseModel


class AIAdapterLearningReview(BaseModel):
    admin_status: str = "reviewed"
    admin_note: Optional[str] = ""
    quality_score: Optional[int] = None


class AIAdapterProfilePromotion(BaseModel):
    profile_name: str
    report_family: Optional[str] = "ai_dynamic"
    vendor_name: Optional[str] = ""
    admin_note: Optional[str] = ""


class AIAdapterLearningLogPayload(BaseModel):
    result: Dict[str, Any]
    force: Optional[bool] = False


def ai_learning_clean_text(value: Any) -> str:
    """
    Local safe text helper.

    Uses existing clean_text if available, otherwise falls back.
    """
    try:
        return clean_text(value)  # type: ignore[name-defined]
    except Exception:
        if value is None:
            return ""
        return " ".join(str(value).strip().split())


def ai_learning_safe_int(value: Any) -> Optional[int]:
    """
    Local safe integer helper.

    Uses existing safe_int if available, otherwise falls back.
    """
    try:
        return safe_int(value)  # type: ignore[name-defined]
    except Exception:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except Exception:
            return None


def ai_learning_get_table_columns(cursor, table_name: str):
    """
    Returns table columns.

    Uses existing get_table_columns if available, otherwise direct SHOW COLUMNS.
    """
    try:
        return get_table_columns(cursor, table_name)  # type: ignore[name-defined]
    except Exception:
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        rows = cursor.fetchall()

        columns = set()

        for row in rows:
            if isinstance(row, dict):
                columns.add(row.get("Field"))
            else:
                columns.add(row[0])

        return columns


def ai_learning_add_column_if_missing(cursor, table_name: str, column_name: str, column_definition: str):
    """
    Adds a column only if missing.

    Uses existing add_column_if_missing if available, otherwise local implementation.
    """
    try:
        add_column_if_missing(cursor, table_name, column_name, column_definition)  # type: ignore[name-defined]
        return
    except NameError:
        pass
    except Exception:
        # If existing helper exists but fails, use local fallback.
        pass

    columns = ai_learning_get_table_columns(cursor, table_name)

    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def json_safe(value: Any) -> str:
    """
    Converts Python values into JSON text safe for MySQL JSON columns.
    """
    try:
        return json.dumps(value, default=str)
    except Exception:
        return json.dumps({"raw": str(value)})


def json_load_safe(value: Any, fallback: Any = None):
    """
    Safely loads JSON values from MySQL JSON/TEXT columns.
    """
    if value is None:
        return fallback

    if isinstance(value, (dict, list)):
        return value

    try:
        return json.loads(value)
    except Exception:
        return fallback if fallback is not None else value


def ensure_ai_adapter_learning_schema():
    """
    Creates tables used to learn from unknown or AI-assisted inspection formats.

    Important:
      This does not auto-trust AI.
      It stores AI parser runs for admin review and optional profile promotion.
    """
    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_adapter_learning_runs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                record_id VARCHAR(255) DEFAULT '',
                filename VARCHAR(500) DEFAULT '',
                detected_adapter VARCHAR(255) DEFAULT '',
                adapter_confidence VARCHAR(100) DEFAULT '',
                parser_mode VARCHAR(100) DEFAULT 'unknown',
                report_family VARCHAR(255) DEFAULT '',
                vendor_name VARCHAR(255) DEFAULT '',
                page_count INT DEFAULT 0,
                image_count INT DEFAULT 0,
                issue_count INT DEFAULT 0,
                issues_with_images_count INT DEFAULT 0,
                sample_issue_titles JSON NULL,
                extracted_schema JSON NULL,
                parser_debug JSON NULL,
                raw_result JSON NULL,
                admin_status VARCHAR(100) DEFAULT 'pending',
                admin_note TEXT NULL,
                quality_score INT NULL,
                promoted_profile_id INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_adapter_profiles (
                id INT AUTO_INCREMENT PRIMARY KEY,
                profile_name VARCHAR(255) NOT NULL,
                report_family VARCHAR(255) DEFAULT 'ai_dynamic',
                vendor_name VARCHAR(255) DEFAULT '',
                source_learning_run_id INT NULL,
                adapter_signature JSON NULL,
                extraction_rules JSON NULL,
                normalization_rules JSON NULL,
                image_matching_notes JSON NULL,
                status VARCHAR(100) DEFAULT 'active',
                admin_note TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # Defensive upgrades in case table existed from earlier partial pass.
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "adapter_confidence",
            "adapter_confidence VARCHAR(100) DEFAULT ''",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "parser_mode",
            "parser_mode VARCHAR(100) DEFAULT 'unknown'",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "report_family",
            "report_family VARCHAR(255) DEFAULT ''",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "vendor_name",
            "vendor_name VARCHAR(255) DEFAULT ''",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "sample_issue_titles",
            "sample_issue_titles JSON NULL",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "extracted_schema",
            "extracted_schema JSON NULL",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "parser_debug",
            "parser_debug JSON NULL",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "raw_result",
            "raw_result JSON NULL",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "admin_status",
            "admin_status VARCHAR(100) DEFAULT 'pending'",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "admin_note",
            "admin_note TEXT NULL",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "quality_score",
            "quality_score INT NULL",
        )
        ai_learning_add_column_if_missing(
            cursor,
            "ai_adapter_learning_runs",
            "promoted_profile_id",
            "promoted_profile_id INT NULL",
        )

        conn.commit()

    finally:
        cursor.close()
        conn.close()


def infer_adapter_learning_needed(result: Dict[str, Any], force: bool = False) -> bool:
    """
    Determines whether this parser run should be captured for dynamic adapter learning.
    """
    if force:
        return True

    detected_adapter = ai_learning_clean_text(
        result.get("detectedAdapter")
        or result.get("detected_adapter")
        or ""
    ).lower()

    parser_debug = result.get("parser_debug") or {}

    findings = result.get("findings") or []
    extracted_issues = result.get("extractedIssues") or []

    findings_count = (
        ai_learning_safe_int(result.get("findings_count"))
        or len(findings)
        or len(extracted_issues)
        or 0
    )

    # Unknown or AI-generated formats should be logged.
    if detected_adapter in {"", "unknown", "ai_extractor", "ai_dynamic", "dynamic_ai_adapter"}:
        return True

    # Explicit AI fallback flags.
    if parser_debug.get("used_ai_fallback") is True:
        return True

    if parser_debug.get("learning_enabled") is True and parser_debug.get("force_learning") is True:
        return True

    if parser_debug.get("workflow") in {
        "restored_previous_image_matching_pass_1",
        "ai_extractor",
        "dynamic_ai_adapter",
        "ai_or_unknown",
    }:
        return True

    # Very low issue count from a report can indicate weak adapter matching.
    if findings_count > 0 and findings_count < 8:
        return True

    return False


def build_adapter_signature_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Creates a reusable fingerprint/profile seed from a parser result.
    """
    issues = result.get("extractedIssues") or result.get("findings") or []

    sample_titles = []
    systems = []
    components = []
    source_patterns = []
    severities = []
    image_statuses = []

    for issue in issues[:50]:
        if not isinstance(issue, dict):
            continue

        title = (
            issue.get("issueTitle")
            or issue.get("issue_title")
            or issue.get("title")
            or ""
        )

        system = issue.get("system") or issue.get("section") or ""
        component = issue.get("component") or ""
        severity = issue.get("severity") or ""
        image_match_status = issue.get("image_match_status") or ""

        source_number = (
            issue.get("source_number")
            or issue.get("sourceNumber")
            or issue.get("issue_code")
            or issue.get("issueCode")
            or ""
        )

        if title:
            sample_titles.append(ai_learning_clean_text(title))

        if system:
            systems.append(ai_learning_clean_text(system))

        if component:
            components.append(ai_learning_clean_text(component))

        if source_number:
            source_patterns.append(ai_learning_clean_text(source_number))

        if severity:
            severities.append(ai_learning_clean_text(severity).lower())

        if image_match_status:
            image_statuses.append(ai_learning_clean_text(image_match_status).lower())

    def unique(values):
        output = []
        seen = set()

        for value in values:
            key = str(value).lower()

            if key in seen:
                continue

            seen.add(key)
            output.append(value)

        return output

    return {
        "detected_adapter": result.get("detectedAdapter") or result.get("detected_adapter"),
        "filename": result.get("filename"),
        "record_id": result.get("record_id"),
        "sample_titles": unique(sample_titles)[:25],
        "systems": unique(systems)[:25],
        "components": unique(components)[:25],
        "source_number_examples": unique(source_patterns)[:25],
        "severity_examples": unique(severities)[:10],
        "image_status_examples": unique(image_statuses)[:10],
        "finding_count": len(issues),
        "has_images": any(
            bool(issue.get("image_url"))
            for issue in issues
            if isinstance(issue, dict)
        ),
        "has_candidate_images": any(
            bool(issue.get("candidate_image_urls"))
            for issue in issues
            if isinstance(issue, dict)
        ),
        "schema_version": "dynamic_ai_adapter_learning_pass_1",
    }


def log_ai_adapter_learning_run(result: Dict[str, Any], force: bool = False) -> Optional[int]:
    """
    Stores parser output as a learning run when the report is unknown,
    weakly matched, or AI fallback assisted.

    Safe to call after /analyze-report/ builds its response.
    """
    try:
        if not infer_adapter_learning_needed(result, force=force):
            return None

        ensure_ai_adapter_learning_schema()

        issues = result.get("extractedIssues") or result.get("findings") or []
        parser_debug = result.get("parser_debug") or {}
        adapter_signature = build_adapter_signature_from_result(result)

        sample_issue_titles = adapter_signature.get("sample_titles", [])

        conn = get_db_connection()  # type: ignore[name-defined]
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO ai_adapter_learning_runs (
                    record_id,
                    filename,
                    detected_adapter,
                    adapter_confidence,
                    parser_mode,
                    report_family,
                    vendor_name,
                    page_count,
                    image_count,
                    issue_count,
                    issues_with_images_count,
                    sample_issue_titles,
                    extracted_schema,
                    parser_debug,
                    raw_result,
                    admin_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    ai_learning_clean_text(result.get("record_id") or ""),
                    ai_learning_clean_text(result.get("filename") or ""),
                    ai_learning_clean_text(
                        result.get("detectedAdapter")
                        or result.get("detected_adapter")
                        or "unknown"
                    ),
                    ai_learning_clean_text(parser_debug.get("adapter_confidence") or ""),
                    ai_learning_clean_text(
                        parser_debug.get("workflow")
                        or parser_debug.get("parser_mode")
                        or "ai_or_unknown"
                    ),
                    ai_learning_clean_text(parser_debug.get("report_family") or ""),
                    ai_learning_clean_text(parser_debug.get("vendor_name") or ""),
                    ai_learning_safe_int(result.get("page_count"))
                    or ai_learning_safe_int(parser_debug.get("page_count"))
                    or 0,
                    ai_learning_safe_int(result.get("image_count"))
                    or ai_learning_safe_int(parser_debug.get("image_count"))
                    or 0,
                    len(issues),
                    ai_learning_safe_int(result.get("issuesWithImagesCount"))
                    or sum(
                        1
                        for issue in issues
                        if isinstance(issue, dict) and issue.get("image_url")
                    ),
                    json_safe(sample_issue_titles),
                    json_safe(adapter_signature),
                    json_safe(parser_debug),
                    json_safe(result),
                ),
            )

            run_id = cursor.lastrowid
            conn.commit()
            return run_id

        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print("AI ADAPTER LEARNING LOG WARNING:", e)
        return None


@app.get("/ai-adapter-learning-health")
def ai_adapter_learning_health():
    """
    Health check for dynamic AI adapter learning.
    """
    try:
        ensure_core_tables()  # type: ignore[name-defined]
    except Exception:
        # Some installs may not need/allow this here. Schema creation below is enough.
        pass

    ensure_ai_adapter_learning_schema()

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) AS total FROM ai_adapter_learning_runs")
        runs_total = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN admin_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN admin_status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed,
                SUM(CASE WHEN admin_status = 'good_candidate' THEN 1 ELSE 0 END) AS good_candidate,
                SUM(CASE WHEN admin_status = 'promoted' THEN 1 ELSE 0 END) AS promoted,
                SUM(CASE WHEN admin_status = 'rejected' THEN 1 ELSE 0 END) AS rejected
            FROM ai_adapter_learning_runs
            """
        )
        run_stats = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) AS total FROM ai_adapter_profiles")
        profiles_total = cursor.fetchone()["total"]

        return {
            "success": True,
            "schema_ready": True,
            "runs_total": runs_total,
            "run_stats": run_stats,
            "profiles_total": profiles_total,
        }

    finally:
        cursor.close()
        conn.close()


@app.post("/ai-adapter-learning-runs/log-result")
def create_ai_adapter_learning_run_from_result(payload: AIAdapterLearningLogPayload):
    """
    Logs a parser result as an AI adapter learning run.

    This endpoint is useful for:
      - manual testing
      - n8n calling after /analyze-report/
      - future unknown-adapter pipelines

    Body:
      {
        "force": true,
        "result": { ...parser response... }
      }
    """
    ensure_ai_adapter_learning_schema()

    run_id = log_ai_adapter_learning_run(payload.result, force=bool(payload.force))

    if not run_id:
        return {
            "success": True,
            "created": False,
            "learning_run_id": None,
            "message": "Parser result did not meet learning criteria. Pass force=true to store anyway.",
        }

    return {
        "success": True,
        "created": True,
        "learning_run_id": run_id,
        "message": "AI adapter learning run stored.",
    }


@app.get("/ai-adapter-learning-runs")
def list_ai_adapter_learning_runs(limit: int = 100, offset: int = 0, status: str = ""):
    """
    Lists AI adapter learning runs for admin review.
    """
    ensure_ai_adapter_learning_schema()

    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    status = ai_learning_clean_text(status)

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        where = ""
        params = []

        if status:
            where = "WHERE admin_status = %s"
            params.append(status)

        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM ai_adapter_learning_runs
            {where}
            """,
            params,
        )

        total = cursor.fetchone()["total"]

        cursor.execute(
            f"""
            SELECT
                id,
                record_id,
                filename,
                detected_adapter,
                adapter_confidence,
                parser_mode,
                report_family,
                vendor_name,
                page_count,
                image_count,
                issue_count,
                issues_with_images_count,
                sample_issue_titles,
                admin_status,
                admin_note,
                quality_score,
                promoted_profile_id,
                created_at,
                updated_at
            FROM ai_adapter_learning_runs
            {where}
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )

        rows = cursor.fetchall()

        for row in rows:
            row["sample_issue_titles"] = json_load_safe(row.get("sample_issue_titles"), [])

            for key in ["created_at", "updated_at"]:
                if row.get(key):
                    row[key] = row[key].isoformat()

        return {
            "success": True,
            "total": total,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "runs": rows,
        }

    finally:
        cursor.close()
        conn.close()


@app.get("/ai-adapter-learning-runs/{run_id}")
def get_ai_adapter_learning_run(run_id: int):
    """
    Gets a single AI adapter learning run, including raw parser result.
    """
    ensure_ai_adapter_learning_schema()

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT * FROM ai_adapter_learning_runs WHERE id = %s LIMIT 1",
            (run_id,),
        )

        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="AI adapter learning run not found")

        for key in ["sample_issue_titles", "extracted_schema", "parser_debug", "raw_result"]:
            row[key] = json_load_safe(row.get(key), None)

        for key in ["created_at", "updated_at"]:
            if row.get(key):
                row[key] = row[key].isoformat()

        return {
            "success": True,
            "run": row,
        }

    finally:
        cursor.close()
        conn.close()


@app.post("/ai-adapter-learning-runs/{run_id}/admin-review")
def review_ai_adapter_learning_run(run_id: int, update: AIAdapterLearningReview):
    """
    Admin reviews a learning run.

    AI is not trusted automatically. Admin labels whether this run is useful.
    """
    ensure_ai_adapter_learning_schema()

    allowed_statuses = {
        "pending",
        "reviewed",
        "good_candidate",
        "needs_more_samples",
        "rejected",
    }

    admin_status = ai_learning_clean_text(update.admin_status).lower()
    admin_note = ai_learning_clean_text(update.admin_note or "")
    quality_score = update.quality_score

    if admin_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid admin_status. Allowed: {sorted(allowed_statuses)}",
        )

    if quality_score is not None and (quality_score < 0 or quality_score > 100):
        raise HTTPException(status_code=400, detail="quality_score must be between 0 and 100")

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE ai_adapter_learning_runs
            SET
                admin_status = %s,
                admin_note = %s,
                quality_score = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (admin_status, admin_note, quality_score, run_id),
        )

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="AI adapter learning run not found")

        conn.commit()

        return {
            "success": True,
            "message": "AI adapter learning run reviewed.",
            "run_id": run_id,
            "admin_status": admin_status,
            "quality_score": quality_score,
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.post("/ai-adapter-learning-runs/{run_id}/promote-profile")
def promote_ai_adapter_learning_run(run_id: int, update: AIAdapterProfilePromotion):
    """
    Promotes a reviewed AI learning run into a reusable dynamic adapter profile.

    This does not create a Python adapter file yet.
    It creates a database-backed adapter profile seed that can be used
    for future matching and admin review.
    """
    ensure_ai_adapter_learning_schema()

    profile_name = ai_learning_clean_text(update.profile_name)
    report_family = ai_learning_clean_text(update.report_family or "ai_dynamic")
    vendor_name = ai_learning_clean_text(update.vendor_name or "")
    admin_note = ai_learning_clean_text(update.admin_note or "")

    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT * FROM ai_adapter_learning_runs WHERE id = %s LIMIT 1",
            (run_id,),
        )

        run = cursor.fetchone()

        if not run:
            raise HTTPException(status_code=404, detail="AI adapter learning run not found")

        adapter_signature = json_load_safe(run.get("extracted_schema"), {}) or {}
        parser_debug = json_load_safe(run.get("parser_debug"), {}) or {}

        extraction_rules = {
            "strategy": "dynamic_ai_profile_seed",
            "source": "ai_adapter_learning_run",
            "source_learning_run_id": run_id,
            "detected_adapter": run.get("detected_adapter"),
            "parser_mode": run.get("parser_mode"),
            "preferred_source_number_patterns": adapter_signature.get("source_number_examples", []),
            "systems_seen": adapter_signature.get("systems", []),
            "components_seen": adapter_signature.get("components", []),
            "sample_titles": adapter_signature.get("sample_titles", []),
            "parser_debug": parser_debug,
            "notes": (
                "Profile created from AI fallback/admin-reviewed extraction. "
                "Future pass should convert this into deterministic extraction rules."
            ),
        }

        normalization_rules = {
            "target_schema": "verified_issue",
            "required_fields": [
                "title",
                "summary",
                "severity",
                "system",
                "component",
                "source_number",
                "summary_page",
                "detail_page",
                "image_url",
                "candidate_image_urls",
            ],
            "human_verification_required": True,
            "homeowner_review_required": True,
            "admin_final_approval_required": True,
        }

        image_matching_notes = {
            "detail_page_recovery": True,
            "candidate_images_required": True,
            "image_status_default": "suggested",
            "verified_image_url_default": "",
            "admin_verification_required": True,
        }

        cursor.execute(
            """
            INSERT INTO ai_adapter_profiles (
                profile_name,
                report_family,
                vendor_name,
                source_learning_run_id,
                adapter_signature,
                extraction_rules,
                normalization_rules,
                image_matching_notes,
                status,
                admin_note
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
            """,
            (
                profile_name,
                report_family,
                vendor_name,
                run_id,
                json_safe(adapter_signature),
                json_safe(extraction_rules),
                json_safe(normalization_rules),
                json_safe(image_matching_notes),
                admin_note,
            ),
        )

        profile_id = cursor.lastrowid

        cursor.execute(
            """
            UPDATE ai_adapter_learning_runs
            SET
                admin_status = 'promoted',
                promoted_profile_id = %s,
                admin_note = CASE
                    WHEN %s != '' THEN %s
                    ELSE admin_note
                END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                profile_id,
                admin_note,
                admin_note,
                run_id,
            ),
        )

        conn.commit()

        return {
            "success": True,
            "message": "AI adapter learning run promoted to dynamic adapter profile.",
            "run_id": run_id,
            "profile_id": profile_id,
            "profile_name": profile_name,
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.get("/ai-adapter-profiles")
def list_ai_adapter_profiles(limit: int = 100, offset: int = 0, status: str = ""):
    """
    Lists reusable dynamic adapter profile seeds.
    """
    ensure_ai_adapter_learning_schema()

    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    status = ai_learning_clean_text(status)

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        where = ""
        params = []

        if status:
            where = "WHERE status = %s"
            params.append(status)

        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM ai_adapter_profiles
            {where}
            """,
            params,
        )

        total = cursor.fetchone()["total"]

        cursor.execute(
            f"""
            SELECT *
            FROM ai_adapter_profiles
            {where}
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )

        rows = cursor.fetchall()

        for row in rows:
            for key in [
                "adapter_signature",
                "extraction_rules",
                "normalization_rules",
                "image_matching_notes",
            ]:
                row[key] = json_load_safe(row.get(key), None)

            for key in ["created_at", "updated_at"]:
                if row.get(key):
                    row[key] = row[key].isoformat()

        return {
            "success": True,
            "total": total,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "profiles": rows,
        }

    finally:
        cursor.close()
        conn.close()

# =========================
# DYNAMIC ADAPTER RULE APPLICATION PASS 2
# =========================
#
# Purpose:
#   Take promoted ai_adapter_profiles from Pass 1 and use them as
#   reusable matching/application hints for future parser results.
#
# Product rule:
#   Profiles can guide extraction/normalization/image matching.
#   Profiles do NOT automatically make findings official.
#   Homeowner review + admin final approval still control baseline truth.
#
# This pass:
#   - scores parser results against active ai_adapter_profiles
#   - chooses the best profile above threshold
#   - enriches parser_debug and findings with profile metadata
#   - logs profile match/application events
#
# This pass does NOT generate Python adapter files yet.

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel


class DynamicAdapterMatchPayload(BaseModel):
    result: Dict[str, Any]
    threshold: Optional[int] = 55
    log_event: Optional[bool] = True


class DynamicAdapterApplyPayload(BaseModel):
    result: Dict[str, Any]
    threshold: Optional[int] = 55
    log_event: Optional[bool] = True
    apply_hints: Optional[bool] = True


def dynamic_clean_text(value: Any) -> str:
    """
    Safe local clean text helper.
    Uses existing clean_text if present.
    """
    try:
        return clean_text(value)  # type: ignore[name-defined]
    except Exception:
        if value is None:
            return ""
        return " ".join(str(value).strip().split())


def dynamic_safe_int(value: Any) -> Optional[int]:
    """
    Safe local integer helper.
    Uses existing safe_int if present.
    """
    try:
        return safe_int(value)  # type: ignore[name-defined]
    except Exception:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except Exception:
            return None


def dynamic_json_load_safe(value: Any, fallback: Any = None):
    """
    Safely load JSON/TEXT column values.
    """
    if value is None:
        return fallback

    if isinstance(value, (dict, list)):
        return value

    try:
        return json.loads(value)
    except Exception:
        return fallback if fallback is not None else value


def dynamic_json_safe(value: Any) -> str:
    """
    JSON dump helper for DB JSON columns.
    """
    try:
        return json.dumps(value, default=str)
    except Exception:
        return json.dumps({"raw": str(value)})


def ensure_dynamic_adapter_rule_application_schema():
    """
    Creates profile application/match event table.

    Requires ai_adapter_profiles from Dynamic AI Adapter Learning Pass 1.
    """
    try:
        ensure_ai_adapter_learning_schema()  # type: ignore[name-defined]
    except Exception:
        # If the Pass 1 function is not loaded, this endpoint will still fail later
        # when ai_adapter_profiles does not exist. This keeps import safe.
        pass

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_adapter_profile_match_events (
                id INT AUTO_INCREMENT PRIMARY KEY,
                record_id VARCHAR(255) DEFAULT '',
                filename VARCHAR(500) DEFAULT '',
                selected_profile_id INT NULL,
                selected_profile_name VARCHAR(255) DEFAULT '',
                selected_report_family VARCHAR(255) DEFAULT '',
                selected_vendor_name VARCHAR(255) DEFAULT '',
                match_score INT DEFAULT 0,
                threshold_score INT DEFAULT 55,
                matched VARCHAR(10) DEFAULT 'no',
                score_breakdown JSON NULL,
                matched_features JSON NULL,
                applied_hints JSON NULL,
                parser_debug_before JSON NULL,
                parser_debug_after JSON NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()

    finally:
        cursor.close()
        conn.close()


def dynamic_tokenize(value: Any) -> List[str]:
    """
    Lowercase tokenization for profile matching.
    Keeps meaningful words and removes tiny tokens.
    """
    text = dynamic_clean_text(value).lower()
    words = re.findall(r"[a-z0-9]+", text)

    stopwords = {
        "the",
        "and",
        "or",
        "at",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "by",
        "a",
        "an",
        "is",
        "are",
        "be",
        "was",
        "were",
        "this",
        "that",
        "report",
        "inspection",
        "system",
        "component",
    }

    return [w for w in words if len(w) > 2 and w not in stopwords]


def dynamic_extract_source_numbers_from_text(text: Any) -> List[str]:
    """
    Extract common report section/source numbers:
      2.1.1
      8.4.2
      S1.1
      A-2
    """
    raw = dynamic_clean_text(text)

    patterns = []
    patterns.extend(re.findall(r"\b\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\b", raw))
    patterns.extend(re.findall(r"\b[A-Z]\d{1,2}\.\d{1,2}\b", raw))
    patterns.extend(re.findall(r"\b[A-Z]-\d{1,3}\b", raw))

    output = []
    seen = set()

    for item in patterns:
        key = item.lower()

        if key in seen:
            continue

        seen.add(key)
        output.append(item)

    return output[:100]


def dynamic_unique(values: List[Any]) -> List[str]:
    output = []
    seen = set()

    for value in values:
        cleaned = dynamic_clean_text(value)

        if not cleaned:
            continue

        key = cleaned.lower()

        if key in seen:
            continue

        seen.add(key)
        output.append(cleaned)

    return output


def dynamic_get_result_issues(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    issues = result.get("extractedIssues") or result.get("findings") or []

    if not isinstance(issues, list):
        return []

    return [issue for issue in issues if isinstance(issue, dict)]


def dynamic_build_result_signature(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Creates a signature from a fresh parser result to compare against profiles.
    """
    issues = dynamic_get_result_issues(result)
    parser_debug = result.get("parser_debug") or {}

    filename = dynamic_clean_text(result.get("filename") or "")
    record_id = dynamic_clean_text(result.get("record_id") or "")
    detected_adapter = dynamic_clean_text(
        result.get("detectedAdapter")
        or result.get("detected_adapter")
        or parser_debug.get("detected_adapter")
        or ""
    )

    titles = []
    systems = []
    components = []
    severities = []
    source_numbers = []
    issue_text_blob_parts = []

    for issue in issues:
        title = (
            issue.get("issueTitle")
            or issue.get("issue_title")
            or issue.get("title")
            or ""
        )

        system = issue.get("system") or issue.get("section") or ""
        component = issue.get("component") or ""
        severity = issue.get("severity") or ""

        source_number = (
            issue.get("source_number")
            or issue.get("sourceNumber")
            or issue.get("issue_code")
            or issue.get("issueCode")
            or ""
        )

        summary = issue.get("summary") or issue.get("notes") or issue.get("description") or ""

        if title:
            titles.append(title)
            issue_text_blob_parts.append(title)

        if system:
            systems.append(system)
            issue_text_blob_parts.append(system)

        if component:
            components.append(component)
            issue_text_blob_parts.append(component)

        if severity:
            severities.append(severity)

        if source_number:
            source_numbers.append(source_number)

        if summary:
            issue_text_blob_parts.append(summary)

    full_blob = " ".join([filename, detected_adapter, *issue_text_blob_parts])

    # Add source numbers detected in text blob too.
    source_numbers.extend(dynamic_extract_source_numbers_from_text(full_blob))

    all_tokens = []
    all_tokens.extend(dynamic_tokenize(filename))
    all_tokens.extend(dynamic_tokenize(detected_adapter))

    for value in titles[:50] + systems[:50] + components[:50]:
        all_tokens.extend(dynamic_tokenize(value))

    return {
        "record_id": record_id,
        "filename": filename,
        "detected_adapter": detected_adapter,
        "finding_count": len(issues),
        "systems": dynamic_unique(systems)[:50],
        "components": dynamic_unique(components)[:50],
        "sample_titles": dynamic_unique(titles)[:50],
        "severity_examples": dynamic_unique([s.lower() for s in severities])[:20],
        "source_number_examples": dynamic_unique(source_numbers)[:100],
        "has_images": any(bool(issue.get("image_url")) for issue in issues),
        "has_candidate_images": any(bool(issue.get("candidate_image_urls")) for issue in issues),
        "tokens": dynamic_unique(all_tokens)[:300],
    }


def dynamic_load_active_profiles() -> List[Dict[str, Any]]:
    """
    Loads active ai_adapter_profiles.
    """
    ensure_dynamic_adapter_rule_application_schema()

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM ai_adapter_profiles
            WHERE COALESCE(status, 'active') = 'active'
            ORDER BY id DESC
            """
        )

        rows = cursor.fetchall()

        profiles = []

        for row in rows:
            row["adapter_signature"] = dynamic_json_load_safe(row.get("adapter_signature"), {}) or {}
            row["extraction_rules"] = dynamic_json_load_safe(row.get("extraction_rules"), {}) or {}
            row["normalization_rules"] = dynamic_json_load_safe(row.get("normalization_rules"), {}) or {}
            row["image_matching_notes"] = dynamic_json_load_safe(row.get("image_matching_notes"), {}) or {}
            profiles.append(row)

        return profiles

    finally:
        cursor.close()
        conn.close()


def dynamic_overlap_score(
    result_values: List[str],
    profile_values: List[str],
    weight: int,
) -> Tuple[int, Dict[str, Any]]:
    """
    Scores overlap between result signature lists and profile lists.
    """
    result_set = {dynamic_clean_text(v).lower() for v in result_values if dynamic_clean_text(v)}
    profile_set = {dynamic_clean_text(v).lower() for v in profile_values if dynamic_clean_text(v)}

    if not result_set or not profile_set:
        return 0, {
            "matched": [],
            "result_count": len(result_set),
            "profile_count": len(profile_set),
            "ratio": 0,
            "points": 0,
        }

    matched = sorted(result_set.intersection(profile_set))
    ratio = len(matched) / max(1, min(len(result_set), len(profile_set)))
    points = round(ratio * weight)

    return points, {
        "matched": matched[:50],
        "result_count": len(result_set),
        "profile_count": len(profile_set),
        "ratio": ratio,
        "points": points,
    }


def dynamic_token_similarity_score(
    result_tokens: List[str],
    profile_values: List[str],
    weight: int,
) -> Tuple[int, Dict[str, Any]]:
    """
    Soft similarity between result tokens and profile titles/systems/components.
    """
    result_set = {dynamic_clean_text(v).lower() for v in result_tokens if dynamic_clean_text(v)}

    profile_tokens = []
    for value in profile_values:
        profile_tokens.extend(dynamic_tokenize(value))

    profile_set = {v.lower() for v in profile_tokens if v}

    if not result_set or not profile_set:
        return 0, {
            "matched_tokens": [],
            "result_token_count": len(result_set),
            "profile_token_count": len(profile_set),
            "ratio": 0,
            "points": 0,
        }

    matched = sorted(result_set.intersection(profile_set))
    ratio = len(matched) / max(1, min(len(result_set), len(profile_set)))
    points = round(ratio * weight)

    return points, {
        "matched_tokens": matched[:75],
        "result_token_count": len(result_set),
        "profile_token_count": len(profile_set),
        "ratio": ratio,
        "points": points,
    }


def dynamic_score_profile_against_result(
    result_signature: Dict[str, Any],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Scores one active profile against a parser result signature.

    Score is 0-100-ish:
      source numbers: 30
      systems: 20
      components: 15
      title/token similarity: 20
      image contract: 5
      filename/vendor/profile hints: 10
    """
    adapter_signature = profile.get("adapter_signature") or {}
    extraction_rules = profile.get("extraction_rules") or {}
    image_notes = profile.get("image_matching_notes") or {}

    profile_source_numbers = (
        adapter_signature.get("source_number_examples")
        or extraction_rules.get("preferred_source_number_patterns")
        or []
    )

    profile_systems = adapter_signature.get("systems") or extraction_rules.get("systems_seen") or []
    profile_components = adapter_signature.get("components") or extraction_rules.get("components_seen") or []
    profile_titles = adapter_signature.get("sample_titles") or extraction_rules.get("sample_titles") or []

    score = 0
    breakdown = {}

    points, detail = dynamic_overlap_score(
        result_signature.get("source_number_examples") or [],
        profile_source_numbers,
        weight=30,
    )
    score += points
    breakdown["source_number_overlap"] = detail

    points, detail = dynamic_overlap_score(
        result_signature.get("systems") or [],
        profile_systems,
        weight=20,
    )
    score += points
    breakdown["system_overlap"] = detail

    points, detail = dynamic_overlap_score(
        result_signature.get("components") or [],
        profile_components,
        weight=15,
    )
    score += points
    breakdown["component_overlap"] = detail

    points, detail = dynamic_token_similarity_score(
        result_signature.get("tokens") or [],
        list(profile_titles) + list(profile_systems) + list(profile_components),
        weight=20,
    )
    score += points
    breakdown["token_similarity"] = detail

    image_points = 0
    if result_signature.get("has_images") and adapter_signature.get("has_images"):
        image_points += 2
    if result_signature.get("has_candidate_images") and adapter_signature.get("has_candidate_images"):
        image_points += 2
    if image_notes.get("detail_page_recovery"):
        image_points += 1

    score += image_points
    breakdown["image_contract"] = {
        "points": image_points,
        "result_has_images": bool(result_signature.get("has_images")),
        "profile_has_images": bool(adapter_signature.get("has_images")),
        "result_has_candidate_images": bool(result_signature.get("has_candidate_images")),
        "profile_has_candidate_images": bool(adapter_signature.get("has_candidate_images")),
    }

    filename_vendor_points = 0
    filename = dynamic_clean_text(result_signature.get("filename") or "").lower()
    vendor = dynamic_clean_text(profile.get("vendor_name") or "").lower()
    profile_name = dynamic_clean_text(profile.get("profile_name") or "").lower()
    report_family = dynamic_clean_text(profile.get("report_family") or "").lower()

    for token in dynamic_tokenize(vendor):
        if token and token in filename:
            filename_vendor_points += 3
            break

    for token in dynamic_tokenize(profile_name):
        if token and token in filename:
            filename_vendor_points += 2
            break

    detected_adapter = dynamic_clean_text(result_signature.get("detected_adapter") or "").lower()

    if report_family and report_family in detected_adapter:
        filename_vendor_points += 3

    if "internachi" in report_family and "internachi" in detected_adapter:
        filename_vendor_points += 2

    filename_vendor_points = min(filename_vendor_points, 10)
    score += filename_vendor_points

    breakdown["filename_vendor_hints"] = {
        "points": filename_vendor_points,
        "filename": filename,
        "vendor": vendor,
        "profile_name": profile_name,
        "report_family": report_family,
        "detected_adapter": detected_adapter,
    }

    return {
        "profile_id": profile.get("id"),
        "profile_name": profile.get("profile_name"),
        "report_family": profile.get("report_family"),
        "vendor_name": profile.get("vendor_name"),
        "score": int(score),
        "breakdown": breakdown,
    }


def dynamic_match_best_profile(
    result: Dict[str, Any],
    threshold: int = 55,
) -> Dict[str, Any]:
    """
    Finds best active profile for parser result.
    """
    threshold = max(0, min(100, dynamic_safe_int(threshold) or 55))

    result_signature = dynamic_build_result_signature(result)
    profiles = dynamic_load_active_profiles()

    scored_profiles = []

    for profile in profiles:
        scored = dynamic_score_profile_against_result(result_signature, profile)
        scored_profiles.append(scored)

    scored_profiles.sort(key=lambda item: item.get("score", 0), reverse=True)

    best = scored_profiles[0] if scored_profiles else None
    matched = bool(best and best.get("score", 0) >= threshold)

    return {
        "success": True,
        "threshold": threshold,
        "matched": matched,
        "best_profile": best,
        "scores": scored_profiles[:10],
        "result_signature": result_signature,
    }


def dynamic_apply_profile_hints_to_result(
    result: Dict[str, Any],
    match_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Non-destructively applies profile hints to parser result.

    It does NOT overwrite issue title/summary/severity.
    It adds metadata and fills missing parser_debug fields.
    """
    output = json.loads(json.dumps(result, default=str))

    if not match_result.get("matched"):
        output.setdefault("parser_debug", {})
        output["parser_debug"]["dynamic_adapter_profile_matched"] = False
        output["parser_debug"]["dynamic_adapter_profile_score"] = (
            match_result.get("best_profile") or {}
        ).get("score")
        return output

    best = match_result.get("best_profile") or {}

    profile_id = best.get("profile_id")
    profile_name = best.get("profile_name")
    report_family = best.get("report_family")
    vendor_name = best.get("vendor_name")
    score = best.get("score")

    output.setdefault("parser_debug", {})
    output["parser_debug"]["dynamic_adapter_profile_matched"] = True
    output["parser_debug"]["dynamic_adapter_profile_id"] = profile_id
    output["parser_debug"]["dynamic_adapter_profile_name"] = profile_name
    output["parser_debug"]["dynamic_adapter_profile_score"] = score
    output["parser_debug"]["dynamic_adapter_report_family"] = report_family
    output["parser_debug"]["dynamic_adapter_vendor_name"] = vendor_name
    output["parser_debug"]["dynamic_adapter_rule_application"] = "pass_2_non_destructive_hints"

    # Keep known adapter if already present, but add profile suggestion.
    output["dynamicAdapterProfile"] = {
        "matched": True,
        "profile_id": profile_id,
        "profile_name": profile_name,
        "report_family": report_family,
        "vendor_name": vendor_name,
        "score": score,
        "threshold": match_result.get("threshold"),
    }

    issues_key = "extractedIssues" if isinstance(output.get("extractedIssues"), list) else "findings"

    if isinstance(output.get(issues_key), list):
        for issue in output[issues_key]:
            if not isinstance(issue, dict):
                continue

            issue["dynamic_adapter_profile_id"] = profile_id
            issue["dynamic_adapter_profile_name"] = profile_name
            issue["dynamic_adapter_profile_score"] = score
            issue["dynamic_adapter_report_family"] = report_family

            # Preserve image verification contract.
            if issue.get("image_url") and not issue.get("image_match_status"):
                issue["image_match_status"] = "suggested"
            if issue.get("image_url") and not issue.get("needs_image_review"):
                issue["needs_image_review"] = "yes"
            if not issue.get("verified_image_url"):
                issue["verified_image_url"] = ""

    # Keep mirrored findings/extractedIssues consistent when both exist.
    if issues_key == "extractedIssues" and isinstance(output.get("findings"), list):
        for issue in output["findings"]:
            if not isinstance(issue, dict):
                continue

            issue["dynamic_adapter_profile_id"] = profile_id
            issue["dynamic_adapter_profile_name"] = profile_name
            issue["dynamic_adapter_profile_score"] = score
            issue["dynamic_adapter_report_family"] = report_family
            if issue.get("image_url") and not issue.get("image_match_status"):
                issue["image_match_status"] = "suggested"
            if issue.get("image_url") and not issue.get("needs_image_review"):
                issue["needs_image_review"] = "yes"
            if not issue.get("verified_image_url"):
                issue["verified_image_url"] = ""

    return output


def dynamic_log_profile_match_event(
    original_result: Dict[str, Any],
    match_result: Dict[str, Any],
    applied_result: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    Logs profile matching/application event.
    """
    try:
        ensure_dynamic_adapter_rule_application_schema()

        best = match_result.get("best_profile") or {}
        result_signature = match_result.get("result_signature") or {}
        parser_debug_before = original_result.get("parser_debug") or {}
        parser_debug_after = (applied_result or {}).get("parser_debug") or {}

        applied_hints = {
            "applied": bool(applied_result),
            "application": "pass_2_non_destructive_hints" if applied_result else "match_only",
        }

        conn = get_db_connection()  # type: ignore[name-defined]
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO ai_adapter_profile_match_events (
                    record_id,
                    filename,
                    selected_profile_id,
                    selected_profile_name,
                    selected_report_family,
                    selected_vendor_name,
                    match_score,
                    threshold_score,
                    matched,
                    score_breakdown,
                    matched_features,
                    applied_hints,
                    parser_debug_before,
                    parser_debug_after
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    dynamic_clean_text(original_result.get("record_id") or ""),
                    dynamic_clean_text(original_result.get("filename") or ""),
                    best.get("profile_id"),
                    dynamic_clean_text(best.get("profile_name") or ""),
                    dynamic_clean_text(best.get("report_family") or ""),
                    dynamic_clean_text(best.get("vendor_name") or ""),
                    dynamic_safe_int(best.get("score")) or 0,
                    dynamic_safe_int(match_result.get("threshold")) or 55,
                    "yes" if match_result.get("matched") else "no",
                    dynamic_json_safe(best.get("breakdown") or {}),
                    dynamic_json_safe(result_signature),
                    dynamic_json_safe(applied_hints),
                    dynamic_json_safe(parser_debug_before),
                    dynamic_json_safe(parser_debug_after),
                ),
            )

            event_id = cursor.lastrowid
            conn.commit()
            return event_id

        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print("DYNAMIC ADAPTER PROFILE MATCH LOG WARNING:", e)
        return None


@app.get("/dynamic-adapter-rule-application-health")
def dynamic_adapter_rule_application_health():
    """
    Health check for Dynamic Adapter Rule Application Pass 2.
    """
    ensure_dynamic_adapter_rule_application_schema()

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) AS total FROM ai_adapter_profiles WHERE COALESCE(status, 'active') = 'active'")
        active_profiles = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM ai_adapter_profile_match_events")
        match_events = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN matched = 'yes' THEN 1 ELSE 0 END) AS matched,
                SUM(CASE WHEN matched = 'no' THEN 1 ELSE 0 END) AS not_matched,
                MAX(match_score) AS max_score,
                AVG(match_score) AS avg_score
            FROM ai_adapter_profile_match_events
            """
        )
        event_stats = cursor.fetchone()

        return {
            "success": True,
            "schema_ready": True,
            "active_profiles": active_profiles,
            "match_events": match_events,
            "event_stats": event_stats,
        }

    finally:
        cursor.close()
        conn.close()


@app.post("/dynamic-adapter-profiles/match-result")
def dynamic_adapter_match_result(payload: DynamicAdapterMatchPayload):
    """
    Scores a parser result against active dynamic adapter profiles.

    Does not change the parser result.
    """
    threshold = dynamic_safe_int(payload.threshold) or 55
    match_result = dynamic_match_best_profile(payload.result, threshold=threshold)

    event_id = None
    if payload.log_event:
        event_id = dynamic_log_profile_match_event(payload.result, match_result, applied_result=None)

    match_result["event_id"] = event_id
    return match_result


@app.post("/dynamic-adapter-profiles/apply-to-result")
def dynamic_adapter_apply_to_result(payload: DynamicAdapterApplyPayload):
    """
    Scores a parser result against active dynamic profiles and returns an enriched result.

    This is non-destructive:
      - no titles overwritten
      - no summaries overwritten
      - no severity overwritten
      - no verified_image_url filled
    """
    threshold = dynamic_safe_int(payload.threshold) or 55
    match_result = dynamic_match_best_profile(payload.result, threshold=threshold)

    if payload.apply_hints:
        applied_result = dynamic_apply_profile_hints_to_result(payload.result, match_result)
    else:
        applied_result = payload.result

    event_id = None
    if payload.log_event:
        event_id = dynamic_log_profile_match_event(payload.result, match_result, applied_result=applied_result)

    return {
        "success": True,
        "matched": match_result.get("matched"),
        "event_id": event_id,
        "match": match_result,
        "result": applied_result,
    }


@app.get("/dynamic-adapter-profile-match-events")
def list_dynamic_adapter_profile_match_events(limit: int = 100, offset: int = 0):
    """
    Lists profile match/application events.
    """
    ensure_dynamic_adapter_rule_application_schema()

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) AS total FROM ai_adapter_profile_match_events")
        total = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT
                id,
                record_id,
                filename,
                selected_profile_id,
                selected_profile_name,
                selected_report_family,
                selected_vendor_name,
                match_score,
                threshold_score,
                matched,
                created_at
            FROM ai_adapter_profile_match_events
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )

        rows = cursor.fetchall()

        for row in rows:
            if row.get("created_at"):
                row["created_at"] = row["created_at"].isoformat()

        return {
            "success": True,
            "total": total,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "events": rows,
        }

    finally:
        cursor.close()
        conn.close()


@app.get("/dynamic-adapter-profile-match-events/{event_id}")
def get_dynamic_adapter_profile_match_event(event_id: int):
    """
    Gets one profile match/application event with breakdown.
    """
    ensure_dynamic_adapter_rule_application_schema()

    conn = get_db_connection()  # type: ignore[name-defined]
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT * FROM ai_adapter_profile_match_events WHERE id = %s LIMIT 1",
            (event_id,),
        )

        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Dynamic adapter profile match event not found")

        for key in [
            "score_breakdown",
            "matched_features",
            "applied_hints",
            "parser_debug_before",
            "parser_debug_after",
        ]:
            row[key] = dynamic_json_load_safe(row.get(key), None)

        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat()

        return {
            "success": True,
            "event": row,
        }

    finally:
        cursor.close()
        conn.close()

# =========================
# SAAS PORTAL SEPARATION + TENANT ISOLATION PASS 1
# =========================
#
# Purpose:
#   Add tenant/homeowner scoping to verified issues.
#   Homeowner endpoints only return records owned by that homeowner/tenant.
#   Admin endpoints remain global.
#
# Product rule:
#   Homeowner can review their own issues.
#   Admin can review all issues and final-approve.
#   Parser/n8n output is not tenant-safe unless tenant metadata is stored.

from typing import Optional
from fastapi import Header
from pydantic import BaseModel


class TenantProcessMetadata(BaseModel):
    tenant_id: Optional[str] = ""
    homeowner_user_id: Optional[str] = ""
    homeowner_email: Optional[str] = ""
    property_id: Optional[str] = ""
    property_address: Optional[str] = ""
    inspection_id: Optional[str] = ""


class HomeownerIssueReviewTenantRequest(BaseModel):
    homeowner_decision: str = "unreviewed"
    homeowner_image_decision: Optional[str] = "unreviewed"
    homeowner_note: Optional[str] = ""


def tenant_clean_text(value):
    try:
        return clean_text(value)
    except Exception:
        if value is None:
            return ""
        return " ".join(str(value).strip().split())


def tenant_normalize_email(value):
    return tenant_clean_text(value).lower()


def ensure_tenant_isolation_schema():
    """
    Adds tenant/homeowner/property fields to verified_issues.

    These fields allow us to separate homeowner portal data from admin portal data.
    """
    ensure_core_tables()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        add_column_if_missing(
            cursor,
            "verified_issues",
            "tenant_id",
            "tenant_id VARCHAR(255) DEFAULT ''",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "homeowner_user_id",
            "homeowner_user_id VARCHAR(255) DEFAULT ''",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "homeowner_email",
            "homeowner_email VARCHAR(255) DEFAULT ''",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "property_id",
            "property_id VARCHAR(255) DEFAULT ''",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "property_address",
            "property_address TEXT NULL",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "inspection_id",
            "inspection_id VARCHAR(255) DEFAULT ''",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "portal_visibility",
            "portal_visibility VARCHAR(50) DEFAULT 'homeowner_admin'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "created_by_source",
            "created_by_source VARCHAR(100) DEFAULT 'parser_pipeline'",
        )

        conn.commit()

    finally:
        cursor.close()
        conn.close()


def tenant_identity_from_headers(
    x_tenant_id: Optional[str] = None,
    x_homeowner_user_id: Optional[str] = None,
    x_homeowner_email: Optional[str] = None,
):
    """
    Dev/proxy-safe identity helper.

    In final Zite integration, these values should come from authenticated
    context.user, not from public client headers.

    For this FastAPI sidecar service, n8n/Zite can forward:
      X-Tenant-ID
      X-Homeowner-User-ID
      X-Homeowner-Email
    """
    tenant_id = tenant_clean_text(x_tenant_id or "")
    homeowner_user_id = tenant_clean_text(x_homeowner_user_id or "")
    homeowner_email = tenant_normalize_email(x_homeowner_email or "")

    if not tenant_id:
        tenant_id = homeowner_user_id or homeowner_email

    return {
        "tenant_id": tenant_id,
        "homeowner_user_id": homeowner_user_id,
        "homeowner_email": homeowner_email,
    }


def tenant_where_clause(identity):
    """
    Builds strict homeowner filter.

    A homeowner request must have at least one identity value.
    """
    tenant_id = tenant_clean_text(identity.get("tenant_id") or "")
    homeowner_user_id = tenant_clean_text(identity.get("homeowner_user_id") or "")
    homeowner_email = tenant_normalize_email(identity.get("homeowner_email") or "")

    conditions = []
    params = []

    if tenant_id:
        conditions.append("COALESCE(tenant_id, '') = %s")
        params.append(tenant_id)

    if homeowner_user_id:
        conditions.append("COALESCE(homeowner_user_id, '') = %s")
        params.append(homeowner_user_id)

    if homeowner_email:
        conditions.append("LOWER(COALESCE(homeowner_email, '')) = %s")
        params.append(homeowner_email)

    if not conditions:
        raise HTTPException(
            status_code=401,
            detail="Missing homeowner tenant identity. Provide X-Tenant-ID, X-Homeowner-User-ID, or X-Homeowner-Email.",
        )

    return "(" + " OR ".join(conditions) + ")", params


@app.get("/tenant-health")
def tenant_health():
    ensure_tenant_isolation_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        columns = get_table_columns(cursor, "verified_issues")

        required = [
            "tenant_id",
            "homeowner_user_id",
            "homeowner_email",
            "property_id",
            "property_address",
            "inspection_id",
            "portal_visibility",
            "created_by_source",
        ]

        missing = [column for column in required if column not in columns]

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_issues,
                SUM(CASE WHEN COALESCE(tenant_id, '') != '' THEN 1 ELSE 0 END) AS with_tenant_id,
                SUM(CASE WHEN COALESCE(homeowner_email, '') != '' THEN 1 ELSE 0 END) AS with_homeowner_email,
                COUNT(DISTINCT NULLIF(tenant_id, '')) AS tenant_count,
                COUNT(DISTINCT NULLIF(homeowner_email, '')) AS homeowner_email_count
            FROM verified_issues
            """
        )
        stats = cursor.fetchone()

        return {
            "success": True,
            "schema_ready": len(missing) == 0,
            "missing_columns": missing,
            "stats": stats,
            "rule": "homeowner endpoints must filter by tenant/homeowner identity; admin endpoints can query globally",
        }

    finally:
        cursor.close()
        conn.close()


@app.get("/homeowner/verified-issues")
def homeowner_verified_issues(
    limit: int = 100,
    offset: int = 0,
    x_tenant_id: Optional[str] = Header(default=None),
    x_homeowner_user_id: Optional[str] = Header(default=None),
    x_homeowner_email: Optional[str] = Header(default=None),
):
    """
    Homeowner-safe verified issue list.

    Returns only issues matching the homeowner/tenant identity.
    """
    ensure_tenant_isolation_schema()
    ensure_review_workflow_schema()

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    identity = tenant_identity_from_headers(
        x_tenant_id=x_tenant_id,
        x_homeowner_user_id=x_homeowner_user_id,
        x_homeowner_email=x_homeowner_email,
    )

    where_identity, identity_params = tenant_where_clause(identity)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM verified_issues
            WHERE {where_identity}
            AND COALESCE(portal_visibility, 'homeowner_admin') IN ('homeowner_admin', 'homeowner')
            """,
            identity_params,
        )
        total = cursor.fetchone()["total"]

        cursor.execute(
            f"""
            SELECT *
            FROM verified_issues
            WHERE {where_identity}
            AND COALESCE(portal_visibility, 'homeowner_admin') IN ('homeowner_admin', 'homeowner')
            ORDER BY
                CASE
                    WHEN risk_level = 'CRITICAL' THEN 1
                    WHEN risk_level = 'HIGH' THEN 2
                    WHEN risk_level = 'MEDIUM' THEN 3
                    WHEN risk_level = 'LOW' THEN 4
                    ELSE 5
                END,
                updated_at DESC,
                id DESC
            LIMIT %s OFFSET %s
            """,
            (*identity_params, limit, offset),
        )

        rows = cursor.fetchall()

        return {
            "success": True,
            "identity": identity,
            "total": total,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
            "issues": [normalize_issue_with_review_fields(row) for row in rows],
        }

    finally:
        cursor.close()
        conn.close()


@app.get("/homeowner/verified-issues/{record_id}")
def homeowner_verified_issues_by_record(
    record_id: str,
    x_tenant_id: Optional[str] = Header(default=None),
    x_homeowner_user_id: Optional[str] = Header(default=None),
    x_homeowner_email: Optional[str] = Header(default=None),
):
    """
    Homeowner-safe record view.

    Same record_id can only be returned when the tenant/homeowner identity matches.
    """
    ensure_tenant_isolation_schema()
    ensure_review_workflow_schema()

    identity = tenant_identity_from_headers(
        x_tenant_id=x_tenant_id,
        x_homeowner_user_id=x_homeowner_user_id,
        x_homeowner_email=x_homeowner_email,
    )

    where_identity, identity_params = tenant_where_clause(identity)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            f"""
            SELECT *
            FROM verified_issues
            WHERE record_id = %s
            AND {where_identity}
            AND COALESCE(portal_visibility, 'homeowner_admin') IN ('homeowner_admin', 'homeowner')
            ORDER BY
                CASE
                    WHEN risk_level = 'CRITICAL' THEN 1
                    WHEN risk_level = 'HIGH' THEN 2
                    WHEN risk_level = 'MEDIUM' THEN 3
                    WHEN risk_level = 'LOW' THEN 4
                    ELSE 5
                END,
                id ASC
            """,
            (record_id, *identity_params),
        )

        rows = cursor.fetchall()

        return {
            "success": True,
            "record_id": record_id,
            "identity": identity,
            "count": len(rows),
            "issues": [normalize_issue_with_review_fields(row) for row in rows],
        }

    finally:
        cursor.close()
        conn.close()


@app.patch("/homeowner/verified-issue/{issue_id}/review")
def homeowner_review_own_issue(
    issue_id: int,
    update: HomeownerIssueReviewTenantRequest,
    x_tenant_id: Optional[str] = Header(default=None),
    x_homeowner_user_id: Optional[str] = Header(default=None),
    x_homeowner_email: Optional[str] = Header(default=None),
):
    """
    Tenant-protected homeowner review endpoint.

    Homeowner can only review an issue if it belongs to their tenant identity.
    """
    ensure_tenant_isolation_schema()
    ensure_review_workflow_schema()

    allowed_decisions = {
        "unreviewed",
        "confirmed",
        "needs_repair",
        "monitor",
        "already_fixed",
        "not_a_concern",
        "image_mismatch",
    }

    allowed_image_decisions = {
        "unreviewed",
        "accepted",
        "mismatch",
        "unsure",
    }

    homeowner_decision = tenant_clean_text(update.homeowner_decision).lower()
    homeowner_image_decision = tenant_clean_text(update.homeowner_image_decision or "unreviewed").lower()
    homeowner_note = tenant_clean_text(update.homeowner_note or "")

    if homeowner_decision not in allowed_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid homeowner_decision. Allowed: {sorted(allowed_decisions)}",
        )

    if homeowner_image_decision not in allowed_image_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid homeowner_image_decision. Allowed: {sorted(allowed_image_decisions)}",
        )

    identity = tenant_identity_from_headers(
        x_tenant_id=x_tenant_id,
        x_homeowner_user_id=x_homeowner_user_id,
        x_homeowner_email=x_homeowner_email,
    )

    where_identity, identity_params = tenant_where_clause(identity)

    current_status = "open"

    if homeowner_decision == "needs_repair":
        current_status = "needs_repair"
    elif homeowner_decision == "monitor":
        current_status = "monitoring"
    elif homeowner_decision in {"already_fixed", "not_a_concern"}:
        current_status = "resolved"
    elif homeowner_decision == "image_mismatch":
        current_status = "needs_review"

    admin_review_status = "needs_review"

    if homeowner_decision == "unreviewed":
        admin_review_status = "pending"

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            f"""
            UPDATE verified_issues
            SET
                homeowner_decision = %s,
                homeowner_image_decision = %s,
                homeowner_note = %s,
                homeowner_reviewed_at = NOW(),
                admin_review_status = %s,
                current_status = %s,
                updated_at = NOW()
            WHERE id = %s
            AND {where_identity}
            """,
            (
                homeowner_decision,
                homeowner_image_decision,
                homeowner_note,
                admin_review_status,
                current_status,
                issue_id,
                *identity_params,
            ),
        )

        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(
                status_code=404,
                detail="Issue not found for this homeowner/tenant identity.",
            )

        conn.commit()

        cursor.execute(
            "SELECT * FROM verified_issues WHERE id = %s LIMIT 1",
            (issue_id,),
        )
        row = cursor.fetchone()

        return {
            "success": True,
            "message": "Homeowner review saved for tenant-scoped issue.",
            "identity": identity,
            "issue": normalize_issue_with_review_fields(row),
        }

    except HTTPException:
        raise

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.get("/admin/tenants")
def admin_list_tenants():
    """
    Admin-only style tenant overview.

    Pass 1 does not enforce auth here yet.
    Final Zite integration should gate this behind ZITE_ADMIN_EMAIL.
    """
    ensure_tenant_isolation_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT
                COALESCE(NULLIF(tenant_id, ''), NULLIF(homeowner_email, ''), 'unassigned') AS tenant_key,
                MAX(homeowner_user_id) AS homeowner_user_id,
                MAX(homeowner_email) AS homeowner_email,
                MAX(property_address) AS sample_property_address,
                COUNT(*) AS issue_count,
                COUNT(DISTINCT record_id) AS record_count,
                SUM(CASE WHEN COALESCE(baseline_locked, 'no') = 'yes' THEN 1 ELSE 0 END) AS baseline_locked_count,
                SUM(CASE WHEN COALESCE(homeowner_decision, 'unreviewed') != 'unreviewed' THEN 1 ELSE 0 END) AS homeowner_reviewed_count,
                MAX(updated_at) AS last_updated
            FROM verified_issues
            GROUP BY tenant_key
            ORDER BY last_updated DESC
            """
        )

        rows = cursor.fetchall()

        for row in rows:
            if row.get("last_updated"):
                row["last_updated"] = row["last_updated"].isoformat()

        return {
            "success": True,
            "count": len(rows),
            "tenants": rows,
        }

    finally:
        cursor.close()
        conn.close()


@app.get("/admin/tenant/{tenant_id}/verified-issues")
def admin_get_tenant_issues(tenant_id: str, limit: int = 100, offset: int = 0):
    """
    Admin tenant drill-down.
    """
    ensure_tenant_isolation_schema()
    ensure_review_workflow_schema()

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    tenant_id = tenant_clean_text(tenant_id)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM verified_issues
            WHERE COALESCE(tenant_id, '') = %s
            OR LOWER(COALESCE(homeowner_email, '')) = LOWER(%s)
            """,
            (tenant_id, tenant_id),
        )
        total = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT *
            FROM verified_issues
            WHERE COALESCE(tenant_id, '') = %s
            OR LOWER(COALESCE(homeowner_email, '')) = LOWER(%s)
            ORDER BY updated_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (tenant_id, tenant_id, limit, offset),
        )
        rows = cursor.fetchall()

        return {
            "success": True,
            "tenant_id": tenant_id,
            "total": total,
            "count": len(rows),
            "issues": [normalize_issue_with_review_fields(row) for row in rows],
        }

    finally:
        cursor.close()
        conn.close()



# =========================
# PROCESS INSPECTION TENANT METADATA PATCH
# =========================
#
# Purpose:
#   Store tenant/homeowner/property metadata during /process-inspection.
#   This removes the need for manual tenant backfill after n8n intake.

def get_process_inspection_tenant_metadata(payload):
    """
    Extract tenant/homeowner/property metadata from /process-inspection payload.

    Works with Pydantic model payloads.
    """

    def get_value(obj, key, default=""):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    try:
        tenant_id = clean_text(get_value(payload, "tenant_id", "") or "")
        homeowner_user_id = clean_text(get_value(payload, "homeowner_user_id", "") or "")
        homeowner_email = clean_text(get_value(payload, "homeowner_email", "") or "").lower()
        property_id = clean_text(get_value(payload, "property_id", "") or "")
        property_address = clean_text(get_value(payload, "property_address", "") or "")
        inspection_id = clean_text(get_value(payload, "inspection_id", "") or "")
        record_id = clean_text(get_value(payload, "record_id", "") or "")

        if not tenant_id:
            tenant_id = homeowner_user_id or homeowner_email

        if not inspection_id:
            inspection_id = record_id

        return {
            "tenant_id": tenant_id,
            "homeowner_user_id": homeowner_user_id,
            "homeowner_email": homeowner_email,
            "property_id": property_id,
            "property_address": property_address,
            "inspection_id": inspection_id,
            "portal_visibility": "homeowner_admin",
            "created_by_source": "n8n_process_inspection",
        }

    except Exception as e:
        print("TENANT METADATA EXTRACTION WARNING:", e)

        return {
            "tenant_id": "",
            "homeowner_user_id": "",
            "homeowner_email": "",
            "property_id": "",
            "property_address": "",
            "inspection_id": "",
            "portal_visibility": "homeowner_admin",
            "created_by_source": "n8n_process_inspection",
        }


def apply_tenant_metadata_to_record(record_id, tenant_metadata):
    """
    Applies tenant metadata to every verified_issues row for a record_id.

    This is intentionally done after /process-inspection creates/updates rows,
    so we do not have to rewrite every insert statement.
    """
    if not record_id:
        return 0

    ensure_tenant_isolation_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE verified_issues
            SET
                tenant_id = %s,
                homeowner_user_id = %s,
                homeowner_email = %s,
                property_id = %s,
                property_address = %s,
                inspection_id = %s,
                portal_visibility = %s,
                created_by_source = %s,
                updated_at = NOW()
            WHERE record_id = %s
            """,
            (
                tenant_metadata.get("tenant_id", ""),
                tenant_metadata.get("homeowner_user_id", ""),
                tenant_metadata.get("homeowner_email", ""),
                tenant_metadata.get("property_id", ""),
                tenant_metadata.get("property_address", ""),
                tenant_metadata.get("inspection_id", ""),
                tenant_metadata.get("portal_visibility", "homeowner_admin"),
                tenant_metadata.get("created_by_source", "n8n_process_inspection"),
                record_id,
            ),
        )

        updated = cursor.rowcount
        conn.commit()
        return updated

    except Exception as e:
        conn.rollback()
        print("TENANT METADATA APPLY WARNING:", e)
        return 0

    finally:
        cursor.close()
        conn.close()


# =========================
# S3 IMAGE STORAGE PASS 1
# =========================
#
# Purpose:
#   Move extracted inspection images from local ephemeral filesystem
#   into durable S3 storage and serve them through FastAPI.
#
# Why:
#   /inspection-images/... works locally on the Pi, but production Render
#   does not have those local files. S3 makes images durable and public-dashboard-safe.

import mimetypes
from pathlib import Path
from urllib.parse import unquote

try:
    import boto3
    from botocore.exceptions import ClientError
except Exception:
    boto3 = None
    ClientError = Exception


def s3_images_enabled():
    return bool(
        boto3
        and os.getenv("S3_INSPECTION_BUCKET", "").strip()
        and os.getenv("AWS_REGION", "").strip()
    )


def get_s3_client():
    if not boto3:
        raise RuntimeError("boto3 is not installed. Add boto3 to requirements.txt and install it.")

    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "us-east-1").strip(),
    )


def clean_s3_segment(value):
    value = clean_text(value or "")
    value = value.replace("\\", "/")
    value = value.replace("..", "")
    value = value.strip("/")

    safe = []
    for char in value:
        if char.isalnum() or char in "-_./":
            safe.append(char)
        else:
            safe.append("-")

    return "".join(safe).strip("/")


def local_image_path_from_url(image_url):
    """
    Converts /inspection-images/page_x_img_y.jpeg into a local file path.

    Expected local file:
      output/images/page_x_img_y.jpeg
    """
    image_url = clean_text(image_url or "")

    if not image_url:
        return None

    if image_url.startswith("http://") or image_url.startswith("https://"):
        return None

    marker = "/inspection-images/"
    if marker not in image_url:
        return None

    filename = image_url.split(marker, 1)[1].strip("/")
    filename = unquote(filename)

    if not filename:
        return None

    filename = filename.replace("\\", "/").split("/")[-1]

    path = Path("output/images") / filename

    if path.exists() and path.is_file():
        return path

    return None


def s3_proxy_url_for_key(s3_key):
    s3_key = clean_s3_segment(s3_key)
    return f"/inspection-images-s3/{s3_key}"


def upload_local_image_to_s3(record_id, image_url):
    """
    Uploads one local image referenced by /inspection-images/... to S3.

    Returns:
      /inspection-images-s3/<s3_key>
    or original image_url if upload is unavailable/fails.
    """
    image_url = clean_text(image_url or "")

    if not image_url:
        return ""

    if image_url.startswith("/inspection-images-s3/"):
        return image_url

    if image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url

    if not s3_images_enabled():
        return image_url

    local_path = local_image_path_from_url(image_url)

    if not local_path:
        print("S3 IMAGE WARNING: local image file not found for", image_url)
        return image_url

    bucket = os.getenv("S3_INSPECTION_BUCKET", "").strip()
    prefix = clean_s3_segment(os.getenv("S3_IMAGE_PREFIX", "inspection-images"))
    safe_record_id = clean_s3_segment(record_id or "unassigned-record")
    filename = local_path.name

    s3_key = f"{prefix}/{safe_record_id}/{filename}"

    content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"

    try:
        client = get_s3_client()

        client.upload_file(
            str(local_path),
            bucket,
            s3_key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=31536000",
            },
        )

        return s3_proxy_url_for_key(s3_key)

    except Exception as e:
        print("S3 IMAGE UPLOAD WARNING:", image_url, str(e))
        return image_url


def upload_issue_images_to_s3(record_id, finding):
    """
    Uploads image_url, verified_image_url, and candidate_image_urls for one finding.
    Rewrites URLs to FastAPI S3 proxy URLs.
    """
    if not isinstance(finding, dict):
        return finding

    updated = dict(finding)

    updated["image_url"] = upload_local_image_to_s3(
        record_id,
        updated.get("image_url") or "",
    )

    if updated.get("verified_image_url"):
        updated["verified_image_url"] = upload_local_image_to_s3(
            record_id,
            updated.get("verified_image_url") or "",
        )

    candidates = updated.get("candidate_image_urls") or []

    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except Exception:
            candidates = []

    if isinstance(candidates, list):
        uploaded_candidates = []
        seen = set()

        for candidate in candidates:
            new_url = upload_local_image_to_s3(record_id, candidate)

            if new_url and new_url not in seen:
                uploaded_candidates.append(new_url)
                seen.add(new_url)

        updated["candidate_image_urls"] = uploaded_candidates

    return updated


def upload_findings_images_to_s3(record_id, findings):
    """
    Applies S3 image upload/rewrite to all findings before DB storage.
    """
    if not findings:
        return findings

    if not s3_images_enabled():
        print("S3 IMAGE INFO: S3 image upload disabled. Missing boto3, AWS_REGION, or S3_INSPECTION_BUCKET.")
        return findings

    uploaded = []

    for finding in findings:
        uploaded.append(upload_issue_images_to_s3(record_id, finding))

    return uploaded


@app.get("/inspection-images-s3/{s3_key:path}")
def serve_s3_inspection_image(s3_key: str):
    """
    Private S3 image proxy.

    Dashboard can use:
      https://lateef-fastapi-docker.onrender.com/inspection-images-s3/<key>

    The S3 bucket can remain private.
    """
    if not s3_images_enabled():
        raise HTTPException(status_code=503, detail="S3 image storage is not configured.")

    s3_key = clean_s3_segment(s3_key)

    if not s3_key:
        raise HTTPException(status_code=400, detail="Missing S3 key.")

    bucket = os.getenv("S3_INSPECTION_BUCKET", "").strip()

    try:
        client = get_s3_client()
        obj = client.get_object(Bucket=bucket, Key=s3_key)

        body = obj["Body"].read()
        content_type = obj.get("ContentType") or mimetypes.guess_type(s3_key)[0] or "application/octet-stream"

        return Response(
            content=body,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=31536000",
            },
        )

    except ClientError as e:
        code = ""
        try:
            code = e.response.get("Error", {}).get("Code", "")
        except Exception:
            pass

        if code in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(status_code=404, detail="Image not found in S3.")

        print("S3 IMAGE SERVE ERROR:", str(e))
        raise HTTPException(status_code=500, detail="Could not load image from S3.")

    except Exception as e:
        print("S3 IMAGE SERVE ERROR:", str(e))
        raise HTTPException(status_code=500, detail="Could not load image from S3.")


# =========================
# S3 DB IMAGE URL REWRITE PATCH
# =========================
#
# Purpose:
#   If /process-inspection uploaded images to S3 but older DB insert logic
#   stored /inspection-images/... URLs, rewrite stored verified_issues image fields
#   to /inspection-images-s3/... after row creation.

def rewrite_single_image_url_to_s3_proxy(record_id, image_url):
    image_url = clean_text(image_url or "")

    if not image_url:
        return ""

    if image_url.startswith("/inspection-images-s3/"):
        return image_url

    if image_url.startswith("http://") or image_url.startswith("https://"):
        return image_url

    marker = "/inspection-images/"
    if marker not in image_url:
        return image_url

    filename = image_url.split(marker, 1)[1].strip("/").split("/")[-1]

    if not filename:
        return image_url

    prefix = clean_s3_segment(os.getenv("S3_IMAGE_PREFIX", "inspection-images"))
    safe_record_id = clean_s3_segment(record_id or "unassigned-record")
    s3_key = f"{prefix}/{safe_record_id}/{filename}"

    return s3_proxy_url_for_key(s3_key)


def rewrite_candidate_image_urls_to_s3_proxy(record_id, candidate_image_urls):
    candidates = candidate_image_urls or []

    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except Exception:
            return candidate_image_urls

    if not isinstance(candidates, list):
        return candidate_image_urls

    rewritten = []
    seen = set()

    for candidate in candidates:
        new_url = rewrite_single_image_url_to_s3_proxy(record_id, candidate)
        if new_url and new_url not in seen:
            rewritten.append(new_url)
            seen.add(new_url)

    return rewritten


def rewrite_record_image_urls_to_s3_proxy(record_id):
    """
    Rewrites stored DB image_url, verified_image_url, and candidate_image_urls
    for one record to FastAPI S3 proxy URLs.
    """
    if not record_id:
        return 0

    if not s3_images_enabled():
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    updated_count = 0

    try:
        cursor.execute(
            """
            SELECT id, image_url, verified_image_url, candidate_image_urls
            FROM verified_issues
            WHERE record_id = %s
            """,
            (record_id,),
        )

        rows = cursor.fetchall()

        for row in rows:
            issue_id = row.get("id")

            next_image_url = rewrite_single_image_url_to_s3_proxy(
                record_id,
                row.get("image_url") or "",
            )

            next_verified_image_url = rewrite_single_image_url_to_s3_proxy(
                record_id,
                row.get("verified_image_url") or "",
            )

            next_candidates = rewrite_candidate_image_urls_to_s3_proxy(
                record_id,
                row.get("candidate_image_urls"),
            )

            next_candidates_json = json.dumps(next_candidates or [])

            cursor.execute(
                """
                UPDATE verified_issues
                SET
                    image_url = %s,
                    verified_image_url = %s,
                    candidate_image_urls = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    next_image_url,
                    next_verified_image_url,
                    next_candidates_json,
                    issue_id,
                ),
            )

            updated_count += cursor.rowcount

        conn.commit()
        return updated_count

    except Exception as e:
        conn.rollback()
        print("S3 DB IMAGE REWRITE WARNING:", e)
        return 0

    finally:
        cursor.close()
        conn.close()


# =========================
# ASYNC S3 IMAGE FINALIZATION PASS 1
# =========================
#
# Purpose:
#   Allow /process-inspection to return fast with skip_s3_upload=true,
#   then finalize S3 image upload and DB URL rewrite in a separate call.

class S3FinalizeRequest(BaseModel):
    force: bool = False


def ensure_s3_finalization_schema():
    ensure_core_tables()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        add_column_if_missing(
            cursor,
            "verified_issues",
            "s3_finalization_status",
            "s3_finalization_status VARCHAR(50) DEFAULT 'not_started'",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "s3_finalized_at",
            "s3_finalized_at DATETIME NULL",
        )
        add_column_if_missing(
            cursor,
            "verified_issues",
            "s3_finalization_note",
            "s3_finalization_note TEXT NULL",
        )

        conn.commit()

    finally:
        cursor.close()
        conn.close()


def get_verified_issues_for_s3_finalization(record_id):
    ensure_s3_finalization_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM verified_issues
            WHERE record_id = %s
            ORDER BY id ASC
            """,
            (record_id,),
        )

        return cursor.fetchall()

    finally:
        cursor.close()
        conn.close()


def mark_s3_finalization_status(record_id, status, note=""):
    ensure_s3_finalization_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if status == "complete":
            cursor.execute(
                """
                UPDATE verified_issues
                SET
                    s3_finalization_status = %s,
                    s3_finalized_at = NOW(),
                    s3_finalization_note = %s,
                    updated_at = NOW()
                WHERE record_id = %s
                """,
                (status, note, record_id),
            )
        else:
            cursor.execute(
                """
                UPDATE verified_issues
                SET
                    s3_finalization_status = %s,
                    s3_finalization_note = %s,
                    updated_at = NOW()
                WHERE record_id = %s
                """,
                (status, note, record_id),
            )

        count = cursor.rowcount
        conn.commit()
        return count

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


def stored_issue_row_to_finding_for_s3(row):
    finding = dict(row)

    candidates = finding.get("candidate_image_urls") or []

    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except Exception:
            candidates = []

    finding["candidate_image_urls"] = candidates

    return finding


def finalize_record_s3_images(record_id, force=False):
    record_id = clean_text(record_id)

    if not record_id:
        raise HTTPException(status_code=400, detail="record_id is required")

    if not s3_images_enabled():
        raise HTTPException(status_code=503, detail="S3 image storage is not configured.")

    rows = get_verified_issues_for_s3_finalization(record_id)

    if not rows:
        raise HTTPException(status_code=404, detail="No verified issues found for record_id.")

    already_complete = all(
        clean_text(row.get("s3_finalization_status") or "") == "complete"
        for row in rows
    )

    if already_complete and not force:
        return {
            "success": True,
            "record_id": record_id,
            "already_complete": True,
            "issues_count": len(rows),
            "s3_image_urls_rewritten": 0,
            "message": "S3 image finalization already complete.",
        }

    mark_s3_finalization_status(record_id, "running", "S3 finalization started.")

    uploaded_count = 0

    try:
        for row in rows:
            finding = stored_issue_row_to_finding_for_s3(row)

            before_image = finding.get("image_url") or ""
            before_candidates = finding.get("candidate_image_urls") or []

            updated = upload_issue_images_to_s3(record_id, finding)

            after_image = updated.get("image_url") or ""
            after_candidates = updated.get("candidate_image_urls") or []

            if after_image != before_image:
                uploaded_count += 1

            if after_candidates != before_candidates:
                uploaded_count += 1

        rewritten_count = rewrite_record_image_urls_to_s3_proxy(record_id)

        mark_s3_finalization_status(
            record_id,
            "complete",
            f"S3 finalization complete. rewritten={rewritten_count}, uploaded_changes={uploaded_count}",
        )

        return {
            "success": True,
            "record_id": record_id,
            "already_complete": False,
            "issues_count": len(rows),
            "uploaded_changes": uploaded_count,
            "s3_image_urls_rewritten": rewritten_count,
            "message": "S3 image finalization complete.",
        }

    except HTTPException:
        mark_s3_finalization_status(record_id, "failed", "S3 finalization failed with HTTPException.")
        raise

    except Exception as e:
        mark_s3_finalization_status(record_id, "failed", str(e))
        raise HTTPException(status_code=500, detail=f"S3 finalization failed: {str(e)}")


@app.get("/records/{record_id}/s3-finalization-status")
def get_record_s3_finalization_status(record_id: str):
    ensure_s3_finalization_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT
                record_id,
                COUNT(*) AS issues_count,
                MAX(s3_finalization_status) AS status,
                MAX(s3_finalized_at) AS finalized_at,
                MAX(s3_finalization_note) AS note,
                SUM(CASE WHEN image_url LIKE '/inspection-images-s3/%%' THEN 1 ELSE 0 END) AS s3_image_count,
                SUM(CASE WHEN image_url LIKE '/inspection-images/%%' THEN 1 ELSE 0 END) AS local_image_count
            FROM verified_issues
            WHERE record_id = %s
            GROUP BY record_id
            """,
            (record_id,),
        )

        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="No verified issues found for record_id.")

        if row.get("finalized_at"):
            row["finalized_at"] = row["finalized_at"].isoformat()

        return {
            "success": True,
            "record": row,
        }

    finally:
        cursor.close()
        conn.close()


@app.post("/records/{record_id}/finalize-s3-images")
def finalize_s3_images_route(record_id: str, request: S3FinalizeRequest = S3FinalizeRequest()):
    result = finalize_record_s3_images(record_id, force=request.force)
    return result


# ============================================================
# HomeFax Image Intelligence Pass 1B
# Read-only image candidate cleanup / ranking preview endpoint
# ============================================================

import math as _hf_img_math
import hashlib as _hf_img_hashlib
from io import BytesIO as _hf_img_BytesIO
from typing import Any as _hf_Any, Dict as _hf_Dict, List as _hf_List, Optional as _hf_Optional

try:
    import requests as _hf_requests
except Exception:
    _hf_requests = None

try:
    from PIL import Image as _hf_Image
    from PIL import ImageFilter as _hf_ImageFilter
    from PIL import ImageStat as _hf_ImageStat
except Exception:
    _hf_Image = None
    _hf_ImageFilter = None
    _hf_ImageStat = None


def _hf_safe_text(value):
    if value is None:
        return ""
    return str(value)


def _hf_public_base_url():
    """
    Used only when the endpoint needs to fetch images from this same API.
    Prefer explicit env var in Render if available.
    """
    return (
        os.getenv("HOMEFAX_PUBLIC_API_BASE_URL")
        or os.getenv("PUBLIC_API_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or "https://lateef-fastapi-docker.onrender.com"
    ).rstrip("/")


def _hf_join_public_url(path_or_url):
    if not path_or_url:
        return ""

    value = str(path_or_url)

    if value.startswith("http://") or value.startswith("https://"):
        return value

    if not value.startswith("/"):
        value = "/" + value

    return _hf_public_base_url() + value


def _hf_issue_text(issue):
    parts = [
        issue.get("title"),
        issue.get("issueTitle"),
        issue.get("section"),
        issue.get("system"),
        issue.get("component"),
        issue.get("location"),
        issue.get("summary"),
        issue.get("type"),
    ]
    return " ".join(_hf_safe_text(p) for p in parts).lower()


def _hf_classify_issue_for_image_intelligence(issue):
    text = _hf_issue_text(issue)

    if any(
        k in text
        for k in [
            "gfci",
            "gfcis",
            "afci",
            "electrical",
            "breaker",
            "panel",
            "panelboard",
            "wiring",
            "meter",
            "disconnect",
            "receptacle",
            "outlet",
            "electric",
        ]
    ):
        return "electrical"

    if any(
        k in text
        for k in [
            "plumbing",
            "leak",
            "water",
            "valve",
            "supply",
            "shut",
            "pipe",
            "drain",
            "sink",
            "hot water",
            "water heater",
        ]
    ):
        return "plumbing"

    if any(k in text for k in ["roof", "shingle", "flashing", "gutter", "downspout"]):
        return "roof"

    if any(k in text for k in ["siding", "wall-covering", "exterior", "fascia", "soffit", "eaves"]):
        return "exterior"

    if any(k in text for k in ["hvac", "furnace", "heating", "cooling", "thermostat", "air conditioning"]):
        return "hvac"

    if any(k in text for k in ["deck", "porch", "railing", "handrail", "guardrail", "ledger"]):
        return "structure"

    return "general"


def _hf_entropy_from_histogram(hist, total):
    entropy = 0.0

    if total <= 0:
        return 0.0

    for count in hist:
        if count:
            p = count / total
            entropy -= p * _hf_img_math.log2(p)

    return entropy


def _hf_image_ahash(img, size=8):
    gray = img.convert("L").resize((size, size))
    pixels = list(gray.getdata())
    avg = sum(pixels) / max(1, len(pixels))
    bits = "".join("1" if p >= avg else "0" for p in pixels)
    return f"{int(bits, 2):016x}"


def _hf_hamming_hex(a, b):
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 64


def _hf_fetch_image_bytes(path_or_url, timeout=20):
    """
    Fetch image bytes from either:
    - /inspection-images-s3/... through local FastAPI proxy
    - absolute URL
    """
    if _hf_requests is None:
        raise RuntimeError("requests is not installed")

    url = _hf_join_public_url(path_or_url)

    res = _hf_requests.get(url, timeout=timeout)

    if not res.ok:
        raise RuntimeError(f"image_fetch_failed status={res.status_code}")

    content_type = res.headers.get("content-type", "").lower()

    if "image" not in content_type:
        raise RuntimeError(f"not_image content_type={content_type}")

    return res.content or b""


def _hf_analyze_image_candidate(path_or_url):
    """
    Deterministic image quality analysis.
    This does NOT decide semantic match yet.
    It only removes junk/blank/tiny/duplicate/low-information candidates.
    """
    result = {
        "ok": True,
        "url": path_or_url,
        "full_url": _hf_join_public_url(path_or_url),
        "bytes_len": 0,
        "width": 0,
        "height": 0,
        "aspect_ratio": 0,
        "mean_brightness": 0,
        "stddev_brightness": 0,
        "entropy": 0,
        "edge_mean": 0,
        "black_white_ratio": 0,
        "midtone_ratio": 0,
        "ahash": "",
        "sha1": "",
        "quality_score": 100,
        "reject_reasons": [],
        "warning_reasons": [],
        "error": "",
    }

    if _hf_Image is None or _hf_ImageFilter is None or _hf_ImageStat is None:
        result["ok"] = False
        result["quality_score"] = 0
        result["reject_reasons"].append("pillow_not_installed")
        return result

    try:
        data = _hf_fetch_image_bytes(path_or_url)
        result["bytes_len"] = len(data)
        result["sha1"] = _hf_img_hashlib.sha1(data).hexdigest()

        img = _hf_Image.open(_hf_img_BytesIO(data))
        img = img.convert("RGB")

        width, height = img.size
        result["width"] = width
        result["height"] = height
        result["aspect_ratio"] = round(width / height, 3) if height else 0

        gray = img.convert("L")
        stat = _hf_ImageStat.Stat(gray)
        result["mean_brightness"] = round(stat.mean[0], 2)
        result["stddev_brightness"] = round(stat.stddev[0], 2)

        hist = gray.histogram()
        total = width * height
        result["entropy"] = round(_hf_entropy_from_histogram(hist, total), 3)

        edges = gray.filter(_hf_ImageFilter.FIND_EDGES)
        edge_stat = _hf_ImageStat.Stat(edges)
        result["edge_mean"] = round(edge_stat.mean[0], 2)

        small_gray = gray.resize((128, 128))
        pixels = list(small_gray.getdata())
        total_small = max(1, len(pixels))

        black_white = sum(1 for p in pixels if p <= 25 or p >= 230)
        midtone = sum(1 for p in pixels if 40 < p < 215)

        result["black_white_ratio"] = round(black_white / total_small, 3)
        result["midtone_ratio"] = round(midtone / total_small, 3)
        result["ahash"] = _hf_image_ahash(img)

        # Hard rejects
        if result["bytes_len"] < 2500:
            result["reject_reasons"].append("very_small_file")

        if width < 140 or height < 100:
            result["reject_reasons"].append("very_small_dimensions")

        if result["stddev_brightness"] < 6:
            result["reject_reasons"].append("near_blank_low_detail")

        if result["entropy"] < 2.0:
            result["reject_reasons"].append("low_entropy_low_information")

        if result["black_white_ratio"] > 0.82 and result["midtone_ratio"] < 0.18:
            result["reject_reasons"].append("black_white_placeholder_like")

        if result["aspect_ratio"] > 5 or result["aspect_ratio"] < 0.2:
            result["reject_reasons"].append("extreme_aspect_ratio")

        # Soft warnings
        if result["edge_mean"] < 4:
            result["warning_reasons"].append("weak_edges_low_visual_detail")

        if result["stddev_brightness"] < 15:
            result["warning_reasons"].append("low_contrast")

        if result["entropy"] < 4:
            result["warning_reasons"].append("low_visual_information")

        score = 100
        score -= 35 * len(result["reject_reasons"])
        score -= 10 * len(result["warning_reasons"])

        if width >= 300 and height >= 200:
            score += 5

        if result["entropy"] >= 5 and result["edge_mean"] >= 8:
            score += 8

        result["quality_score"] = max(0, min(100, score))

        if result["reject_reasons"]:
            result["ok"] = False

    except Exception as exc:
        result["ok"] = False
        result["quality_score"] = 0
        result["error"] = str(exc)
        result["reject_reasons"].append("download_or_analysis_failed")

    return result


def _hf_collect_issue_candidate_urls(issue, max_candidates=12):
    urls = []

    # Include current suggested image first.
    if issue.get("image_url"):
        urls.append(issue.get("image_url"))

    # Then include candidate pool.
    for url in issue.get("candidate_image_urls") or []:
        if url and url not in urls:
            urls.append(url)

    # Include already verified image for transparency if present.
    if issue.get("verified_image_url") and issue.get("verified_image_url") not in urls:
        urls.insert(0, issue.get("verified_image_url"))

    return urls[:max_candidates]


def _hf_score_issue_image_candidates(issue, max_candidates=12, top_k=5):
    raw_urls = _hf_collect_issue_candidate_urls(issue, max_candidates=max_candidates)
    category = _hf_classify_issue_for_image_intelligence(issue)

    analyzed = []
    seen_sha1 = {}
    seen_ahash = {}

    for index, url in enumerate(raw_urls):
        candidate = _hf_analyze_image_candidate(url)
        candidate["candidate_index"] = index
        candidate["issue_category"] = category
        candidate["duplicate_of"] = None

        sha1 = candidate.get("sha1")
        ahash = candidate.get("ahash")

        if sha1:
            if sha1 in seen_sha1:
                candidate["duplicate_of"] = seen_sha1[sha1]
            else:
                seen_sha1[sha1] = url

        if ahash:
            for existing_hash, existing_url in seen_ahash.items():
                if _hf_hamming_hex(ahash, existing_hash) <= 4:
                    candidate["duplicate_of"] = candidate["duplicate_of"] or existing_url
                    break

            seen_ahash.setdefault(ahash, url)

        if candidate["duplicate_of"]:
            candidate["ok"] = False
            if "duplicate_or_near_duplicate" not in candidate["reject_reasons"]:
                candidate["reject_reasons"].append("duplicate_or_near_duplicate")
            candidate["quality_score"] = max(0, candidate.get("quality_score", 0) - 45)

        analyzed.append(candidate)

    ranked = sorted(
        analyzed,
        key=lambda c: (
            1 if c.get("ok") else 0,
            c.get("quality_score", 0),
            -c.get("candidate_index", 999),
        ),
        reverse=True,
    )

    clean = [c for c in ranked if c.get("ok")]
    rejected = [c for c in ranked if not c.get("ok")]

    clean_top = clean[:top_k]
    best = clean_top[0] if clean_top else None

    return {
        "issue_id": issue.get("id"),
        "record_id": issue.get("record_id"),
        "title": issue.get("title"),
        "section": issue.get("section"),
        "severity": issue.get("severity"),
        "issue_category": category,
        "current_image_url": issue.get("image_url"),
        "verified_image_url": issue.get("verified_image_url"),
        "best_image_url": best.get("url") if best else "",
        "best_image_score": best.get("quality_score") if best else 0,
        "clean_candidate_image_urls": [c.get("url") for c in clean_top],
        "clean_candidate_count": len(clean),
        "rejected_candidate_count": len(rejected),
        "raw_candidate_count": len(raw_urls),
        "candidates": ranked,
        "status": "scored",
        "note": "Deterministic cleanup only. Semantic AI vision ranking is not applied in this pass.",
    }


def _hf_get_verified_issues_for_record_internal(record_id):
    """
    Internal helper for image-intelligence endpoint.

    It first tries the existing verified issues route handler if available.
    If that fails because the route returns a Response object or has a different
    name, use the HTTP endpoint as a safe fallback.
    """
    # Preferred: call local HTTP endpoint to preserve exact production payload shape.
    # This is read-only and avoids making assumptions about database schema.
    if _hf_requests is None:
        raise RuntimeError("requests is not installed")

    url = f"{_hf_public_base_url()}/verified-issues/{record_id}"
    res = _hf_requests.get(url, timeout=60)

    try:
        data = res.json()
    except Exception as exc:
        raise RuntimeError(f"verified_issues_non_json status={res.status_code} preview={res.text[:250]!r}") from exc

    if not res.ok:
        raise RuntimeError(f"verified_issues_fetch_failed status={res.status_code} data={data}")

    return data.get("issues") or []


@app.get("/records/{record_id}/image-intelligence-preview")
def image_intelligence_preview(
    record_id: str,
    max_candidates: int = 12,
    top_k: int = 5,
):
    """
    Read-only image intelligence preview.

    This endpoint does NOT update verified issues.
    It scores current image_url + candidate_image_urls and returns a cleaned set.

    Use it before wiring dashboard or database mutations.
    """
    try:
        max_candidates = max(1, min(int(max_candidates), 20))
        top_k = max(1, min(int(top_k), 10))

        issues = _hf_get_verified_issues_for_record_internal(record_id)

        scored_issues = [
            _hf_score_issue_image_candidates(
                issue=issue,
                max_candidates=max_candidates,
                top_k=top_k,
            )
            for issue in issues
        ]

        total_raw = sum(i.get("raw_candidate_count", 0) for i in scored_issues)
        total_clean = sum(i.get("clean_candidate_count", 0) for i in scored_issues)
        total_rejected = sum(i.get("rejected_candidate_count", 0) for i in scored_issues)
        issues_without_clean = sum(1 for i in scored_issues if not i.get("clean_candidate_image_urls"))

        return {
            "success": True,
            "record_id": record_id,
            "mode": "preview_read_only",
            "base_url": _hf_public_base_url(),
            "issues_count": len(scored_issues),
            "summary": {
                "total_candidates_analyzed": total_raw,
                "total_clean_candidates": total_clean,
                "total_rejected_candidates": total_rejected,
                "issues_without_clean_candidates": issues_without_clean,
            },
            "issues": scored_issues,
            "message": "Image intelligence preview complete. No database records were changed.",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "record_id": record_id,
                "error": str(exc),
                "message": "Image intelligence preview failed.",
            },
        )


@app.get("/records/{record_id}/image-intelligence-summary")
def image_intelligence_summary(
    record_id: str,
    max_candidates: int = 12,
    top_k: int = 5,
):
    """
    Smaller read-only summary for quick terminal checks.
    """
    preview = image_intelligence_preview(
        record_id=record_id,
        max_candidates=max_candidates,
        top_k=top_k,
    )

    compact_issues = []

    for issue in preview.get("issues", []):
      compact_issues.append({
          "issue_id": issue.get("issue_id"),
          "title": issue.get("title"),
          "section": issue.get("section"),
          "issue_category": issue.get("issue_category"),
          "best_image_url": issue.get("best_image_url"),
          "best_image_score": issue.get("best_image_score"),
          "raw_candidate_count": issue.get("raw_candidate_count"),
          "clean_candidate_count": issue.get("clean_candidate_count"),
          "rejected_candidate_count": issue.get("rejected_candidate_count"),
          "clean_candidate_image_urls": issue.get("clean_candidate_image_urls"),
      })

    return {
        "success": True,
        "record_id": record_id,
        "mode": "summary_read_only",
        "issues_count": preview.get("issues_count", 0),
        "summary": preview.get("summary", {}),
        "issues": compact_issues,
    }



# ============================================================
# HomeFax Image Intelligence Pass 1B
# Read-only image candidate cleanup / ranking preview endpoint
# ============================================================

import math as _hf_img_math
import hashlib as _hf_img_hashlib
from io import BytesIO as _hf_img_BytesIO
from typing import Any as _hf_Any, Dict as _hf_Dict, List as _hf_List, Optional as _hf_Optional

try:
    import requests as _hf_requests
except Exception:
    _hf_requests = None

try:
    from PIL import Image as _hf_Image
    from PIL import ImageFilter as _hf_ImageFilter
    from PIL import ImageStat as _hf_ImageStat
except Exception:
    _hf_Image = None
    _hf_ImageFilter = None
    _hf_ImageStat = None


def _hf_safe_text(value):
    if value is None:
        return ""
    return str(value)


def _hf_public_base_url():
    """
    Used only when the endpoint needs to fetch images from this same API.
    Prefer explicit env var in Render if available.
    """
    return (
        os.getenv("HOMEFAX_PUBLIC_API_BASE_URL")
        or os.getenv("PUBLIC_API_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or "https://lateef-fastapi-docker.onrender.com"
    ).rstrip("/")


def _hf_join_public_url(path_or_url):
    if not path_or_url:
        return ""

    value = str(path_or_url)

    if value.startswith("http://") or value.startswith("https://"):
        return value

    if not value.startswith("/"):
        value = "/" + value

    return _hf_public_base_url() + value


def _hf_issue_text(issue):
    parts = [
        issue.get("title"),
        issue.get("issueTitle"),
        issue.get("section"),
        issue.get("system"),
        issue.get("component"),
        issue.get("location"),
        issue.get("summary"),
        issue.get("type"),
    ]
    return " ".join(_hf_safe_text(p) for p in parts).lower()


def _hf_classify_issue_for_image_intelligence(issue):
    text = _hf_issue_text(issue)

    if any(
        k in text
        for k in [
            "gfci",
            "gfcis",
            "afci",
            "electrical",
            "breaker",
            "panel",
            "panelboard",
            "wiring",
            "meter",
            "disconnect",
            "receptacle",
            "outlet",
            "electric",
        ]
    ):
        return "electrical"

    if any(
        k in text
        for k in [
            "plumbing",
            "leak",
            "water",
            "valve",
            "supply",
            "shut",
            "pipe",
            "drain",
            "sink",
            "hot water",
            "water heater",
        ]
    ):
        return "plumbing"

    if any(k in text for k in ["roof", "shingle", "flashing", "gutter", "downspout"]):
        return "roof"

    if any(k in text for k in ["siding", "wall-covering", "exterior", "fascia", "soffit", "eaves"]):
        return "exterior"

    if any(k in text for k in ["hvac", "furnace", "heating", "cooling", "thermostat", "air conditioning"]):
        return "hvac"

    if any(k in text for k in ["deck", "porch", "railing", "handrail", "guardrail", "ledger"]):
        return "structure"

    return "general"


def _hf_entropy_from_histogram(hist, total):
    entropy = 0.0

    if total <= 0:
        return 0.0

    for count in hist:
        if count:
            p = count / total
            entropy -= p * _hf_img_math.log2(p)

    return entropy


def _hf_image_ahash(img, size=8):
    gray = img.convert("L").resize((size, size))
    pixels = list(gray.getdata())
    avg = sum(pixels) / max(1, len(pixels))
    bits = "".join("1" if p >= avg else "0" for p in pixels)
    return f"{int(bits, 2):016x}"


def _hf_hamming_hex(a, b):
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 64


def _hf_fetch_image_bytes(path_or_url, timeout=20):
    """
    Fetch image bytes from either:
    - /inspection-images-s3/... through local FastAPI proxy
    - absolute URL
    """
    if _hf_requests is None:
        raise RuntimeError("requests is not installed")

    url = _hf_join_public_url(path_or_url)

    res = _hf_requests.get(url, timeout=timeout)

    if not res.ok:
        raise RuntimeError(f"image_fetch_failed status={res.status_code}")

    content_type = res.headers.get("content-type", "").lower()

    if "image" not in content_type:
        raise RuntimeError(f"not_image content_type={content_type}")

    return res.content or b""


def _hf_analyze_image_candidate(path_or_url):
    """
    Deterministic image quality analysis.
    This does NOT decide semantic match yet.
    It only removes junk/blank/tiny/duplicate/low-information candidates.
    """
    result = {
        "ok": True,
        "url": path_or_url,
        "full_url": _hf_join_public_url(path_or_url),
        "bytes_len": 0,
        "width": 0,
        "height": 0,
        "aspect_ratio": 0,
        "mean_brightness": 0,
        "stddev_brightness": 0,
        "entropy": 0,
        "edge_mean": 0,
        "black_white_ratio": 0,
        "midtone_ratio": 0,
        "ahash": "",
        "sha1": "",
        "quality_score": 100,
        "reject_reasons": [],
        "warning_reasons": [],
        "error": "",
    }

    if _hf_Image is None or _hf_ImageFilter is None or _hf_ImageStat is None:
        result["ok"] = False
        result["quality_score"] = 0
        result["reject_reasons"].append("pillow_not_installed")
        return result

    try:
        data = _hf_fetch_image_bytes(path_or_url)
        result["bytes_len"] = len(data)
        result["sha1"] = _hf_img_hashlib.sha1(data).hexdigest()

        img = _hf_Image.open(_hf_img_BytesIO(data))
        img = img.convert("RGB")

        width, height = img.size
        result["width"] = width
        result["height"] = height
        result["aspect_ratio"] = round(width / height, 3) if height else 0

        gray = img.convert("L")
        stat = _hf_ImageStat.Stat(gray)
        result["mean_brightness"] = round(stat.mean[0], 2)
        result["stddev_brightness"] = round(stat.stddev[0], 2)

        hist = gray.histogram()
        total = width * height
        result["entropy"] = round(_hf_entropy_from_histogram(hist, total), 3)

        edges = gray.filter(_hf_ImageFilter.FIND_EDGES)
        edge_stat = _hf_ImageStat.Stat(edges)
        result["edge_mean"] = round(edge_stat.mean[0], 2)

        small_gray = gray.resize((128, 128))
        pixels = list(small_gray.getdata())
        total_small = max(1, len(pixels))

        black_white = sum(1 for p in pixels if p <= 25 or p >= 230)
        midtone = sum(1 for p in pixels if 40 < p < 215)

        result["black_white_ratio"] = round(black_white / total_small, 3)
        result["midtone_ratio"] = round(midtone / total_small, 3)
        result["ahash"] = _hf_image_ahash(img)

        # Hard rejects
        if result["bytes_len"] < 2500:
            result["reject_reasons"].append("very_small_file")

        if width < 140 or height < 100:
            result["reject_reasons"].append("very_small_dimensions")

        if result["stddev_brightness"] < 6:
            result["reject_reasons"].append("near_blank_low_detail")

        if result["entropy"] < 2.0:
            result["reject_reasons"].append("low_entropy_low_information")

        if result["black_white_ratio"] > 0.82 and result["midtone_ratio"] < 0.18:
            result["reject_reasons"].append("black_white_placeholder_like")

        if result["aspect_ratio"] > 5 or result["aspect_ratio"] < 0.2:
            result["reject_reasons"].append("extreme_aspect_ratio")

        # Soft warnings
        if result["edge_mean"] < 4:
            result["warning_reasons"].append("weak_edges_low_visual_detail")

        if result["stddev_brightness"] < 15:
            result["warning_reasons"].append("low_contrast")

        if result["entropy"] < 4:
            result["warning_reasons"].append("low_visual_information")

        score = 100
        score -= 35 * len(result["reject_reasons"])
        score -= 10 * len(result["warning_reasons"])

        if width >= 300 and height >= 200:
            score += 5

        if result["entropy"] >= 5 and result["edge_mean"] >= 8:
            score += 8

        result["quality_score"] = max(0, min(100, score))

        if result["reject_reasons"]:
            result["ok"] = False

    except Exception as exc:
        result["ok"] = False
        result["quality_score"] = 0
        result["error"] = str(exc)
        result["reject_reasons"].append("download_or_analysis_failed")

    return result


def _hf_collect_issue_candidate_urls(issue, max_candidates=12):
    urls = []

    # Include current suggested image first.
    if issue.get("image_url"):
        urls.append(issue.get("image_url"))

    # Then include candidate pool.
    for url in issue.get("candidate_image_urls") or []:
        if url and url not in urls:
            urls.append(url)

    # Include already verified image for transparency if present.
    if issue.get("verified_image_url") and issue.get("verified_image_url") not in urls:
        urls.insert(0, issue.get("verified_image_url"))

    return urls[:max_candidates]


def _hf_score_issue_image_candidates(issue, max_candidates=12, top_k=5):
    raw_urls = _hf_collect_issue_candidate_urls(issue, max_candidates=max_candidates)
    category = _hf_classify_issue_for_image_intelligence(issue)

    analyzed = []
    seen_sha1 = {}
    seen_ahash = {}

    for index, url in enumerate(raw_urls):
        candidate = _hf_analyze_image_candidate(url)
        candidate["candidate_index"] = index
        candidate["issue_category"] = category
        candidate["duplicate_of"] = None

        sha1 = candidate.get("sha1")
        ahash = candidate.get("ahash")

        if sha1:
            if sha1 in seen_sha1:
                candidate["duplicate_of"] = seen_sha1[sha1]
            else:
                seen_sha1[sha1] = url

        if ahash:
            for existing_hash, existing_url in seen_ahash.items():
                if _hf_hamming_hex(ahash, existing_hash) <= 4:
                    candidate["duplicate_of"] = candidate["duplicate_of"] or existing_url
                    break

            seen_ahash.setdefault(ahash, url)

        if candidate["duplicate_of"]:
            candidate["ok"] = False
            if "duplicate_or_near_duplicate" not in candidate["reject_reasons"]:
                candidate["reject_reasons"].append("duplicate_or_near_duplicate")
            candidate["quality_score"] = max(0, candidate.get("quality_score", 0) - 45)

        analyzed.append(candidate)

    ranked = sorted(
        analyzed,
        key=lambda c: (
            1 if c.get("ok") else 0,
            c.get("quality_score", 0),
            -c.get("candidate_index", 999),
        ),
        reverse=True,
    )

    clean = [c for c in ranked if c.get("ok")]
    rejected = [c for c in ranked if not c.get("ok")]

    clean_top = clean[:top_k]
    best = clean_top[0] if clean_top else None

    return {
        "issue_id": issue.get("id"),
        "record_id": issue.get("record_id"),
        "title": issue.get("title"),
        "section": issue.get("section"),
        "severity": issue.get("severity"),
        "issue_category": category,
        "current_image_url": issue.get("image_url"),
        "verified_image_url": issue.get("verified_image_url"),
        "best_image_url": best.get("url") if best else "",
        "best_image_score": best.get("quality_score") if best else 0,
        "clean_candidate_image_urls": [c.get("url") for c in clean_top],
        "clean_candidate_count": len(clean),
        "rejected_candidate_count": len(rejected),
        "raw_candidate_count": len(raw_urls),
        "candidates": ranked,
        "status": "scored",
        "note": "Deterministic cleanup only. Semantic AI vision ranking is not applied in this pass.",
    }


def _hf_get_verified_issues_for_record_internal(record_id):
    """
    Internal helper for image-intelligence endpoint.

    It first tries the existing verified issues route handler if available.
    If that fails because the route returns a Response object or has a different
    name, use the HTTP endpoint as a safe fallback.
    """
    # Preferred: call local HTTP endpoint to preserve exact production payload shape.
    # This is read-only and avoids making assumptions about database schema.
    if _hf_requests is None:
        raise RuntimeError("requests is not installed")

    url = f"{_hf_public_base_url()}/verified-issues/{record_id}"
    res = _hf_requests.get(url, timeout=60)

    try:
        data = res.json()
    except Exception as exc:
        raise RuntimeError(f"verified_issues_non_json status={res.status_code} preview={res.text[:250]!r}") from exc

    if not res.ok:
        raise RuntimeError(f"verified_issues_fetch_failed status={res.status_code} data={data}")

    return data.get("issues") or []


@app.get("/records/{record_id}/image-intelligence-preview")
def image_intelligence_preview(
    record_id: str,
    max_candidates: int = 12,
    top_k: int = 5,
):
    """
    Read-only image intelligence preview.

    This endpoint does NOT update verified issues.
    It scores current image_url + candidate_image_urls and returns a cleaned set.

    Use it before wiring dashboard or database mutations.
    """
    try:
        max_candidates = max(1, min(int(max_candidates), 20))
        top_k = max(1, min(int(top_k), 10))

        issues = _hf_get_verified_issues_for_record_internal(record_id)

        scored_issues = [
            _hf_score_issue_image_candidates(
                issue=issue,
                max_candidates=max_candidates,
                top_k=top_k,
            )
            for issue in issues
        ]

        total_raw = sum(i.get("raw_candidate_count", 0) for i in scored_issues)
        total_clean = sum(i.get("clean_candidate_count", 0) for i in scored_issues)
        total_rejected = sum(i.get("rejected_candidate_count", 0) for i in scored_issues)
        issues_without_clean = sum(1 for i in scored_issues if not i.get("clean_candidate_image_urls"))

        return {
            "success": True,
            "record_id": record_id,
            "mode": "preview_read_only",
            "base_url": _hf_public_base_url(),
            "issues_count": len(scored_issues),
            "summary": {
                "total_candidates_analyzed": total_raw,
                "total_clean_candidates": total_clean,
                "total_rejected_candidates": total_rejected,
                "issues_without_clean_candidates": issues_without_clean,
            },
            "issues": scored_issues,
            "message": "Image intelligence preview complete. No database records were changed.",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "record_id": record_id,
                "error": str(exc),
                "message": "Image intelligence preview failed.",
            },
        )


@app.get("/records/{record_id}/image-intelligence-summary")
def image_intelligence_summary(
    record_id: str,
    max_candidates: int = 12,
    top_k: int = 5,
):
    """
    Smaller read-only summary for quick terminal checks.
    """
    preview = image_intelligence_preview(
        record_id=record_id,
        max_candidates=max_candidates,
        top_k=top_k,
    )

    compact_issues = []

    for issue in preview.get("issues", []):
      compact_issues.append({
          "issue_id": issue.get("issue_id"),
          "title": issue.get("title"),
          "section": issue.get("section"),
          "issue_category": issue.get("issue_category"),
          "best_image_url": issue.get("best_image_url"),
          "best_image_score": issue.get("best_image_score"),
          "raw_candidate_count": issue.get("raw_candidate_count"),
          "clean_candidate_count": issue.get("clean_candidate_count"),
          "rejected_candidate_count": issue.get("rejected_candidate_count"),
          "clean_candidate_image_urls": issue.get("clean_candidate_image_urls"),
      })

    return {
        "success": True,
        "record_id": record_id,
        "mode": "summary_read_only",
        "issues_count": preview.get("issues_count", 0),
        "summary": preview.get("summary", {}),
        "issues": compact_issues,
    }



# ============================================================
# HomeFax Image Intelligence Pass 2
# AI Vision Semantic Reranking Preview Endpoint
# ============================================================
#
# Purpose:
#   Read-only AI reranking of image candidates.
#   This pass answers:
#     "Does this image actually match this finding?"
#
# This endpoint does NOT update the database.
# It is intentionally capped by max_issues/max_candidates for cost control.

import json as _hf_ai_json
import re as _hf_ai_re

try:
    from openai import OpenAI as _hf_OpenAI
except Exception:
    _hf_OpenAI = None


def _hf_ai_model_name():
    return (
        os.getenv("HOMEFAX_VISION_MODEL")
        or os.getenv("OPENAI_VISION_MODEL")
        or "gpt-4.1-mini"
    )


def _hf_ai_available():
    return bool(_hf_OpenAI and os.getenv("OPENAI_API_KEY"))


def _hf_compact_finding_for_ai(issue):
    return {
        "issue_id": issue.get("issue_id") or issue.get("id"),
        "title": issue.get("title") or "",
        "section": issue.get("section") or "",
        "severity": issue.get("severity") or "",
        "issue_category": issue.get("issue_category") or "",
        "current_image_url": issue.get("current_image_url") or issue.get("image_url") or "",
        "verified_image_url": issue.get("verified_image_url") or "",
    }


def _hf_extract_json_object(text):
    """
    Extract a JSON object from the model response.
    Keeps endpoint resilient if model wraps response in prose/code fence.
    """
    if not text:
        return {}

    text = text.strip()

    # Remove simple fenced code wrappers.
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        return _hf_ai_json.loads(text)
    except Exception:
        pass

    match = _hf_ai_re.search(r"\{.*\}", text, flags=_hf_ai_re.DOTALL)

    if not match:
        return {}

    try:
        return _hf_ai_json.loads(match.group(0))
    except Exception:
        return {}


def _hf_ai_prompt_for_candidate(issue, candidate):
    finding = _hf_compact_finding_for_ai(issue)

    return f"""
You are HomeFax AI image verification support.

Your job:
Decide whether the provided inspection photo is a good visual match for the finding.

You must judge only what is visible in the image.
If the image is clean but does not clearly show the finding, score it low.
Do not assume it matches only because it came from the same report page.

Finding:
{_hf_ai_json.dumps(finding, indent=2)}

Candidate image metadata:
{_hf_ai_json.dumps({
    "url": candidate.get("url"),
    "quality_score": candidate.get("quality_score"),
    "issue_category": candidate.get("issue_category"),
    "candidate_index": candidate.get("candidate_index"),
}, indent=2)}

Scoring guide:
- 90-100: clearly shows the exact defect/component
- 70-89: likely relevant, component visible, defect may be partly visible
- 40-69: same general system/room, but defect not clear
- 10-39: weak or questionable relevance
- 0-9: wrong image, unrelated, placeholder, no useful evidence

Return ONLY valid JSON with this exact shape:
{{
  "match_score": 0,
  "is_relevant": false,
  "defect_visible": false,
  "component_visible": false,
  "image_category": "unknown",
  "reason": "short explanation",
  "reject_reason": "short reason if score is under 40, otherwise empty"
}}
""".strip()


def _hf_ai_score_single_candidate(issue, candidate):
    if not _hf_ai_available():
        return {
            "match_score": 0,
            "is_relevant": False,
            "defect_visible": False,
            "component_visible": False,
            "image_category": "unknown",
            "reason": "AI vision is not available. Check OPENAI_API_KEY and openai package.",
            "reject_reason": "ai_unavailable",
            "ai_error": "ai_unavailable",
        }

    client = _hf_OpenAI()
    image_url = _hf_join_public_url(candidate.get("url") or "")

    try:
        response = client.responses.create(
            model=_hf_ai_model_name(),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _hf_ai_prompt_for_candidate(issue, candidate),
                        },
                        {
                            "type": "input_image",
                            "image_url": image_url,
                            "detail": "low",
                        },
                    ],
                }
            ],
        )

        raw_text = getattr(response, "output_text", "") or ""
        parsed = _hf_extract_json_object(raw_text)

        match_score = parsed.get("match_score", 0)

        try:
            match_score = int(match_score)
        except Exception:
            match_score = 0

        match_score = max(0, min(100, match_score))

        return {
            "match_score": match_score,
            "is_relevant": bool(parsed.get("is_relevant")),
            "defect_visible": bool(parsed.get("defect_visible")),
            "component_visible": bool(parsed.get("component_visible")),
            "image_category": str(parsed.get("image_category") or "unknown"),
            "reason": str(parsed.get("reason") or raw_text[:300]),
            "reject_reason": str(parsed.get("reject_reason") or ""),
            "ai_model": _hf_ai_model_name(),
            "ai_error": "",
        }

    except Exception as exc:
        return {
            "match_score": 0,
            "is_relevant": False,
            "defect_visible": False,
            "component_visible": False,
            "image_category": "unknown",
            "reason": "AI vision call failed.",
            "reject_reason": "ai_call_failed",
            "ai_model": _hf_ai_model_name(),
            "ai_error": str(exc),
        }


def _hf_ai_rerank_issue(issue, max_candidates=5, top_k=3):
    clean_candidates = issue.get("candidates") or []
    clean_candidates = [c for c in clean_candidates if c.get("ok")]
    clean_candidates = clean_candidates[:max_candidates]

    scored = []

    for candidate in clean_candidates:
        ai_score = _hf_ai_score_single_candidate(issue, candidate)

        combined_score = round(
            (0.7 * ai_score.get("match_score", 0))
            + (0.3 * candidate.get("quality_score", 0)),
            2,
        )

        merged = {
            **candidate,
            "ai_match_score": ai_score.get("match_score", 0),
            "ai_is_relevant": ai_score.get("is_relevant", False),
            "ai_defect_visible": ai_score.get("defect_visible", False),
            "ai_component_visible": ai_score.get("component_visible", False),
            "ai_image_category": ai_score.get("image_category", "unknown"),
            "ai_reason": ai_score.get("reason", ""),
            "ai_reject_reason": ai_score.get("reject_reason", ""),
            "ai_model": ai_score.get("ai_model", _hf_ai_model_name()),
            "ai_error": ai_score.get("ai_error", ""),
            "combined_score": combined_score,
        }

        scored.append(merged)

    ranked = sorted(
        scored,
        key=lambda c: (
            c.get("ai_match_score", 0),
            c.get("combined_score", 0),
            c.get("quality_score", 0),
            -c.get("candidate_index", 999),
        ),
        reverse=True,
    )

    top = ranked[:top_k]
    best = top[0] if top else None

    return {
        "issue_id": issue.get("issue_id"),
        "record_id": issue.get("record_id"),
        "title": issue.get("title"),
        "section": issue.get("section"),
        "severity": issue.get("severity"),
        "issue_category": issue.get("issue_category"),
        "best_image_url": best.get("url") if best else "",
        "best_ai_match_score": best.get("ai_match_score") if best else 0,
        "best_combined_score": best.get("combined_score") if best else 0,
        "best_ai_reason": best.get("ai_reason") if best else "",
        "ai_ranked_candidate_image_urls": [c.get("url") for c in top],
        "ai_ranked_candidates": ranked,
        "semantic_status": "ai_scored",
        "note": "Read-only AI vision reranking. No database records were changed.",
    }


@app.get("/records/{record_id}/image-intelligence-ai-preview")
def image_intelligence_ai_preview(
    record_id: str,
    max_candidates: int = 5,
    top_k: int = 3,
    max_issues: int = 3,
    start_index: int = 0,
):
    """
    Read-only AI Vision semantic reranking preview.

    Cost control:
    - max_issues defaults to 3.
    - max_candidates defaults to 5.
    - This endpoint should be tested on small batches first.
    """
    try:
        max_candidates = max(1, min(int(max_candidates), 8))
        top_k = max(1, min(int(top_k), 5))
        max_issues = max(1, min(int(max_issues), 10))
        start_index = max(0, int(start_index))

        deterministic_preview = image_intelligence_preview(
            record_id=record_id,
            max_candidates=max_candidates,
            top_k=max_candidates,
        )

        all_preview_issues = deterministic_preview.get("issues", [])
        total_preview_issues = len(all_preview_issues)
        end_index = min(start_index + max_issues, total_preview_issues)
        base_issues = all_preview_issues[start_index:end_index]

        ai_issues = [
            _hf_ai_rerank_issue(
                issue=issue,
                max_candidates=max_candidates,
                top_k=top_k,
            )
            for issue in base_issues
        ]

        total_ai_candidates_scored = sum(
            len(issue.get("ai_ranked_candidates") or [])
            for issue in ai_issues
        )

        return {
            "success": True,
            "record_id": record_id,
            "mode": "ai_preview_read_only",
            "ai_available": _hf_ai_available(),
            "ai_model": _hf_ai_model_name(),
            "issues_requested": max_issues,
            "issues_scored": len(ai_issues),
            "start_index": start_index,
            "end_index": end_index,
            "next_start_index": end_index if end_index < total_preview_issues else None,
            "has_more": end_index < total_preview_issues,
            "total_preview_issues": total_preview_issues,
            "summary": {
                "deterministic_summary": deterministic_preview.get("summary", {}),
                "total_ai_candidates_scored": total_ai_candidates_scored,
            },
            "issues": ai_issues,
            "message": "AI image intelligence preview complete. No database records were changed.",
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "record_id": record_id,
                "error": str(exc),
                "message": "AI image intelligence preview failed.",
            },
        )



# ============================================================
# HomeFax Original Report Storage + Source Page Endpoint Pass 1
# Provides original PDF source access for report-backed findings.
# ============================================================

import os as _hf_report_os
import re as _hf_report_re
import json as _hf_report_json
import shutil as _hf_report_shutil
from pathlib import Path as _hf_report_Path
from typing import Optional as _hf_report_Optional, Any as _hf_report_Any, Dict as _hf_report_Dict

try:
    from fastapi import HTTPException as _hf_report_HTTPException
    from fastapi.responses import FileResponse as _hf_report_FileResponse, RedirectResponse as _hf_report_RedirectResponse
except Exception:
    _hf_report_HTTPException = None
    _hf_report_FileResponse = None
    _hf_report_RedirectResponse = None


_HF_REPORT_STORAGE_DIR = _hf_report_Path(
    _hf_report_os.getenv("HOMEFAX_ORIGINAL_REPORT_DIR", "original_reports")
).resolve()

_HF_REPORT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _hf_report_safe_record_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown-record"

    # Keep URLs/filesystem safe while preserving readable record IDs.
    safe = _hf_report_re.sub(r"[^a-zA-Z0-9._-]+", "-", raw)
    safe = safe.strip(".-_")
    return safe or "unknown-record"


def _hf_report_public_base_url() -> str:
    # Optional. If set, returned URLs can be absolute.
    return _hf_report_os.getenv("PUBLIC_API_BASE_URL", "").rstrip("/")


def _hf_report_relative_pdf_url(record_id: str) -> str:
    safe_id = _hf_report_safe_record_id(record_id)
    return f"/inspection-report/{safe_id}"


def _hf_report_page_url(record_id: str, source_page: _hf_report_Optional[int] = None) -> str:
    base = _hf_report_relative_pdf_url(record_id)
    if source_page:
        return f"{base}#page={int(source_page)}"
    return base


def _hf_report_absolute_or_relative(url: str) -> str:
    base = _hf_report_public_base_url()
    if base and url.startswith("/"):
        return f"{base}{url}"
    return url


def _hf_report_pdf_path(record_id: str) -> _hf_report_Path:
    safe_id = _hf_report_safe_record_id(record_id)
    return _HF_REPORT_STORAGE_DIR / f"{safe_id}.pdf"


def _hf_report_find_existing_pdf(record_id: str) -> _hf_report_Optional[_hf_report_Path]:
    safe_id = _hf_report_safe_record_id(record_id)
    expected = _hf_report_pdf_path(safe_id)

    if expected.exists() and expected.is_file():
        return expected

    # Helpful fallback search for manually copied files.
    candidates = [
        _HF_REPORT_STORAGE_DIR / f"{safe_id}.PDF",
        _HF_REPORT_STORAGE_DIR / f"{safe_id}_original.pdf",
        _HF_REPORT_STORAGE_DIR / f"{safe_id}-original.pdf",
        _hf_report_Path("uploads") / f"{safe_id}.pdf",
        _hf_report_Path("uploaded_reports") / f"{safe_id}.pdf",
    ]

    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.exists() and candidate.is_file():
            return candidate

    return None


def _hf_report_save_original_pdf(record_id: str, source_path: str) -> _hf_report_Dict[str, _hf_report_Any]:
    if not source_path:
        raise ValueError("source_path is required")

    src = _hf_report_Path(source_path).expanduser().resolve()

    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"Original PDF not found: {src}")

    if src.suffix.lower() != ".pdf":
        raise ValueError(f"Original report must be a PDF: {src}")

    dest = _hf_report_pdf_path(record_id)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if src != dest:
        _hf_report_shutil.copyfile(src, dest)

    size_bytes = dest.stat().st_size

    return {
        "record_id": _hf_report_safe_record_id(record_id),
        "stored": True,
        "path": str(dest),
        "size_bytes": size_bytes,
        "report_pdf_url": _hf_report_absolute_or_relative(_hf_report_relative_pdf_url(record_id)),
    }


def _hf_report_db_connection():
    # Reuse whichever DB helper exists in main.py.
    for name in ("get_db_connection", "get_connection", "db_connection"):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No DB connection helper found. Expected get_db_connection(), get_connection(), or db_connection().")


def _hf_report_add_column_if_missing(cursor, table_name: str, column_name: str, column_sql: str):
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )

    exists = cursor.fetchone()
    if isinstance(exists, dict):
        count = int(exists.get("COUNT(*)", 0) or list(exists.values())[0] or 0)
    else:
        count = int(exists[0] or 0)

    if count == 0:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
        return True

    return False


def _hf_report_ensure_schema() -> _hf_report_Dict[str, _hf_report_Any]:
    added = []

    conn = _hf_report_db_connection()
    try:
        with conn.cursor() as cursor:
            schema_items = [
                ("verified_issues", "report_pdf_url", "TEXT NULL"),
                ("verified_issues", "report_page_url", "TEXT NULL"),
                ("verified_issues", "source_page", "INT NULL"),
                ("verified_issues", "original_report_path", "TEXT NULL"),
                ("verified_issues", "homeowner_selected_image_url", "TEXT NULL"),
            ]

            for table_name, column_name, column_sql in schema_items:
                try:
                    did_add = _hf_report_add_column_if_missing(cursor, table_name, column_name, column_sql)
                    if did_add:
                        added.append(f"{table_name}.{column_name}")
                except Exception as column_error:
                    print("[HomeFax Report Source] schema column warning:", table_name, column_name, column_error)

        conn.commit()

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "success": True,
        "storage_dir": str(_HF_REPORT_STORAGE_DIR),
        "added_columns": added,
    }


def _hf_report_fetch_issue(issue_id: int) -> _hf_report_Optional[dict]:
    conn = _hf_report_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM verified_issues WHERE id = %s LIMIT 1", (issue_id,))
            row = cursor.fetchone()

            if not row:
                return None

            if isinstance(row, dict):
                return row

            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_report_fetch_record_first_issue(record_id: str) -> _hf_report_Optional[dict]:
    conn = _hf_report_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM verified_issues
                WHERE record_id = %s
                ORDER BY id ASC
                LIMIT 1
                """,
                (record_id,),
            )
            row = cursor.fetchone()

            if not row:
                return None

            if isinstance(row, dict):
                return row

            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_report_issue_source_payload(issue: dict) -> dict:
    issue = issue or {}
    record_id = issue.get("record_id") or ""

    source_page = (
        issue.get("source_page")
        or issue.get("detail_page")
        or issue.get("page")
        or issue.get("summary_page")
    )

    try:
        source_page = int(source_page) if source_page not in (None, "", "not linked yet") else None
    except Exception:
        source_page = None

    source_number = (
        issue.get("source_number")
        or issue.get("sourceNumber")
        or issue.get("report_item")
        or issue.get("reportItem")
        or ""
    )

    report_pdf_url = issue.get("report_pdf_url") or _hf_report_relative_pdf_url(record_id)
    report_page_url = issue.get("report_page_url") or _hf_report_page_url(record_id, source_page)

    exists = _hf_report_find_existing_pdf(record_id) is not None

    return {
        "success": True,
        "issue_id": issue.get("id"),
        "record_id": record_id,
        "source_number": source_number,
        "source_page": source_page,
        "report_pdf_url": _hf_report_absolute_or_relative(report_pdf_url),
        "report_page_url": _hf_report_absolute_or_relative(report_page_url),
        "original_report_available": exists,
        "source_status": "linked" if exists else "report_not_uploaded_to_source_storage",
        "message": (
            "Original report is available."
            if exists
            else "Original report has not been registered in original report storage yet."
        ),
    }


def _hf_report_update_record_source_urls(record_id: str) -> dict:
    pdf_url = _hf_report_relative_pdf_url(record_id)

    conn = _hf_report_db_connection()
    updated = 0

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, source_page, detail_page, page, summary_page
                FROM verified_issues
                WHERE record_id = %s
                """,
                (record_id,),
            )

            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in rows]

            for row in rows:
                issue_id = row.get("id")
                source_page = (
                    row.get("source_page")
                    or row.get("detail_page")
                    or row.get("page")
                    or row.get("summary_page")
                )

                try:
                    source_page_int = int(source_page) if source_page else None
                except Exception:
                    source_page_int = None

                page_url = _hf_report_page_url(record_id, source_page_int)

                cursor.execute(
                    """
                    UPDATE verified_issues
                    SET report_pdf_url = %s,
                        report_page_url = %s,
                        source_page = COALESCE(source_page, %s),
                        original_report_path = %s
                    WHERE id = %s
                    """,
                    (
                        pdf_url,
                        page_url,
                        source_page_int,
                        str(_hf_report_pdf_path(record_id)),
                        issue_id,
                    ),
                )
                updated += 1

        conn.commit()

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "success": True,
        "record_id": record_id,
        "updated_issues": updated,
        "report_pdf_url": _hf_report_absolute_or_relative(pdf_url),
    }


@app.get("/original-report-source-health")
def original_report_source_health():
    schema = _hf_report_ensure_schema()

    return {
        "success": True,
        "storage_dir": str(_HF_REPORT_STORAGE_DIR),
        "storage_exists": _HF_REPORT_STORAGE_DIR.exists(),
        "schema": schema,
        "endpoints": [
            "GET /inspection-report/{record_id}",
            "GET /records/{record_id}/report-source",
            "GET /verified-issue/{issue_id}/report-source",
            "POST /records/{record_id}/register-original-report",
            "POST /records/{record_id}/refresh-report-source-urls",
        ],
    }


@app.get("/inspection-report/{record_id}")
def get_original_inspection_report(record_id: str):
    pdf_path = _hf_report_find_existing_pdf(record_id)

    if not pdf_path:
        raise _hf_report_HTTPException(
            status_code=404,
            detail={
                "success": False,
                "record_id": record_id,
                "message": "Original inspection PDF has not been registered yet.",
                "expected_path": str(_hf_report_pdf_path(record_id)),
            },
        )

    return _hf_report_FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"{_hf_report_safe_record_id(record_id)}.pdf",
    )


@app.get("/records/{record_id}/report-source")
def get_record_report_source(record_id: str):
    _hf_report_ensure_schema()

    first_issue = _hf_report_fetch_record_first_issue(record_id) or {"record_id": record_id}
    source = _hf_report_issue_source_payload(first_issue)

    source.update(
        {
            "record_id": record_id,
            "report_pdf_url": _hf_report_absolute_or_relative(_hf_report_relative_pdf_url(record_id)),
            "report_page_url": _hf_report_absolute_or_relative(_hf_report_page_url(record_id, source.get("source_page"))),
        }
    )

    return source


@app.get("/verified-issue/{issue_id}/report-source")
def get_verified_issue_report_source(issue_id: int):
    _hf_report_ensure_schema()

    issue = _hf_report_fetch_issue(issue_id)

    if not issue:
        raise _hf_report_HTTPException(
            status_code=404,
            detail={
                "success": False,
                "issue_id": issue_id,
                "message": "Verified issue not found.",
            },
        )

    return _hf_report_issue_source_payload(issue)


@app.post("/records/{record_id}/register-original-report")
def register_original_report(record_id: str, payload: dict):
    """
    Register an existing local PDF path for a record.

    Body:
    {
      "source_path": "/home/maestrodagod/Downloads/report.pdf"
    }

    This is for Pass 1/local testing. Later n8n/upload flow should save this automatically.
    """
    _hf_report_ensure_schema()

    source_path = (payload or {}).get("source_path", "")

    try:
        stored = _hf_report_save_original_pdf(record_id, source_path)
        updated = _hf_report_update_record_source_urls(record_id)
    except Exception as error:
        raise _hf_report_HTTPException(
            status_code=400,
            detail={
                "success": False,
                "record_id": record_id,
                "error": str(error),
            },
        )

    return {
        "success": True,
        "record_id": record_id,
        "stored": stored,
        "updated": updated,
    }


@app.post("/records/{record_id}/refresh-report-source-urls")
def refresh_report_source_urls(record_id: str):
    _hf_report_ensure_schema()
    updated = _hf_report_update_record_source_urls(record_id)

    return {
        "success": True,
        "record_id": record_id,
        "updated": updated,
    }



# ============================================================
# HomeFax Original Report Source Compatibility Patch 1
# Fixes source URL refresh on schemas without detail_page/page/summary_page.
# Also improves source_number detection from available columns.
# ============================================================

def _hf_report_table_columns(table_name: str = "verified_issues") -> set:
    conn = _hf_report_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = %s
                """,
                (table_name,),
            )
            rows = cursor.fetchall() or []

            columns = set()
            for row in rows:
                if isinstance(row, dict):
                    columns.add(str(row.get("column_name") or row.get("COLUMN_NAME") or "").strip())
                else:
                    columns.add(str(row[0]).strip())

            return {c for c in columns if c}

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_report_first_existing_column(columns: set, names: list) -> str:
    for name in names:
        if name in columns:
            return name
    return ""


def _hf_report_get_first_value(row: dict, keys: list):
    for key in keys:
        if key in row and row.get(key) not in (None, "", "not linked yet"):
            return row.get(key)
    return None


def _hf_report_extract_source_number(issue: dict) -> str:
    issue = issue or {}

    candidates = [
        "source_number",
        "sourceNumber",
        "report_item",
        "reportItem",
        "item_number",
        "itemNumber",
        "inspection_item",
        "inspectionItem",
        "finding_number",
        "findingNumber",
        "issue_code",
        "issueCode",
        "code",
    ]

    value = _hf_report_get_first_value(issue, candidates)

    if value:
        return str(value).strip()

    # Fallback: extract a report item pattern like 9.8.1 from title/summary/section.
    text = " ".join(
        str(issue.get(k) or "")
        for k in [
            "title",
            "issueTitle",
            "original_title",
            "section",
            "summary",
            "description",
            "original_report_wording",
        ]
    )

    m = _hf_report_re.search(r"\b\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\b", text)
    return m.group(0) if m else ""


def _hf_report_extract_source_page(issue: dict):
    issue = issue or {}

    value = _hf_report_get_first_value(
        issue,
        [
            "source_page",
            "detail_page",
            "page",
            "summary_page",
            "page_number",
            "pageNumber",
            "report_page",
            "reportPage",
        ],
    )

    try:
        return int(value) if value not in (None, "", "not linked yet") else None
    except Exception:
        return None


# Override previous implementation safely.
def _hf_report_issue_source_payload(issue: dict) -> dict:
    issue = issue or {}
    record_id = issue.get("record_id") or ""

    source_page = _hf_report_extract_source_page(issue)
    source_number = _hf_report_extract_source_number(issue)

    report_pdf_url = issue.get("report_pdf_url") or _hf_report_relative_pdf_url(record_id)
    report_page_url = issue.get("report_page_url") or _hf_report_page_url(record_id, source_page)

    exists = _hf_report_find_existing_pdf(record_id) is not None

    return {
        "success": True,
        "issue_id": issue.get("id"),
        "record_id": record_id,
        "source_number": source_number,
        "source_page": source_page,
        "report_pdf_url": _hf_report_absolute_or_relative(report_pdf_url),
        "report_page_url": _hf_report_absolute_or_relative(report_page_url),
        "original_report_available": exists,
        "source_status": "linked" if exists else "report_not_uploaded_to_source_storage",
        "message": (
            "Original report is available."
            if exists
            else "Original report has not been registered in original report storage yet."
        ),
    }


# Override previous implementation safely.
def _hf_report_update_record_source_urls(record_id: str) -> dict:
    columns = _hf_report_table_columns("verified_issues")

    pdf_url = _hf_report_relative_pdf_url(record_id)

    page_columns = [
        c
        for c in [
            "source_page",
            "detail_page",
            "page",
            "summary_page",
            "page_number",
            "report_page",
        ]
        if c in columns
    ]

    select_cols = ["id"]
    for col in page_columns:
        if col not in select_cols:
            select_cols.append(col)

    # Keep this select schema-safe. Do not select columns that do not exist.
    select_sql = f"""
        SELECT {", ".join(select_cols)}
        FROM verified_issues
        WHERE record_id = %s
    """

    conn = _hf_report_db_connection()
    updated = 0

    try:
        with conn.cursor() as cursor:
            cursor.execute(select_sql, (record_id,))
            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                result_columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(result_columns, row)) for row in rows]

            for row in rows:
                issue_id = row.get("id")

                source_page_int = None
                for col in page_columns:
                    val = row.get(col)
                    if val not in (None, "", "not linked yet"):
                        try:
                            source_page_int = int(val)
                            break
                        except Exception:
                            pass

                page_url = _hf_report_page_url(record_id, source_page_int)

                update_fields = []
                update_values = []

                if "report_pdf_url" in columns:
                    update_fields.append("report_pdf_url = %s")
                    update_values.append(pdf_url)

                if "report_page_url" in columns:
                    update_fields.append("report_page_url = %s")
                    update_values.append(page_url)

                if "source_page" in columns and source_page_int:
                    update_fields.append("source_page = COALESCE(source_page, %s)")
                    update_values.append(source_page_int)

                if "original_report_path" in columns:
                    update_fields.append("original_report_path = %s")
                    update_values.append(str(_hf_report_pdf_path(record_id)))

                if not update_fields:
                    continue

                update_values.append(issue_id)

                cursor.execute(
                    f"""
                    UPDATE verified_issues
                    SET {", ".join(update_fields)}
                    WHERE id = %s
                    """,
                    tuple(update_values),
                )

                updated += 1

        conn.commit()

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "success": True,
        "record_id": record_id,
        "updated_issues": updated,
        "available_page_columns": page_columns,
        "report_pdf_url": _hf_report_absolute_or_relative(pdf_url),
    }



# ============================================================
# HomeFax Original Report Source HEAD Support Patch
# Allows curl -I /inspection-report/{record_id}.
# ============================================================

@app.head("/inspection-report/{record_id}")
def head_original_inspection_report(record_id: str):
    pdf_path = _hf_report_find_existing_pdf(record_id)

    if not pdf_path:
        raise _hf_report_HTTPException(
            status_code=404,
            detail={
                "success": False,
                "record_id": record_id,
                "message": "Original inspection PDF has not been registered yet.",
                "expected_path": str(_hf_report_pdf_path(record_id)),
            },
        )

    return _hf_report_FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"{_hf_report_safe_record_id(record_id)}.pdf",
    )



# ============================================================
# HomeFax Standard Finding Schema Pass 1
# Adds normalized HomeFax finding fields for monitoring.
#
# Purpose:
# - Preserve original inspector/report evidence.
# - Normalize diverse inspection reports into a standard HomeFax model.
# - Give dashboard/monitoring a consistent structure.
# ============================================================

import json as _hf_std_json
import re as _hf_std_re
from datetime import datetime as _hf_std_datetime


_HF_STANDARD_SCHEMA_VERSION = "homefax_standard_finding_v1"


def _hf_std_safe_text(value) -> str:
    return _hf_std_re.sub(r"\s+", " ", str(value or "")).strip()


def _hf_std_lower_blob(issue: dict) -> str:
    issue = issue or {}
    parts = [
        issue.get("title"),
        issue.get("summary"),
        issue.get("section"),
        issue.get("severity"),
        issue.get("risk_level"),
        issue.get("current_status"),
        issue.get("status"),
    ]
    return _hf_std_safe_text(" ".join(str(p or "") for p in parts)).lower()


def _hf_std_extract_report_item(issue: dict) -> str:
    issue = issue or {}

    for key in [
        "source_number",
        "report_item",
        "item_number",
        "issue_code",
        "source_item_number",
        "standard_source_item_number",
    ]:
        value = _hf_std_safe_text(issue.get(key))
        if value:
            return value

    blob = _hf_std_safe_text(
        " ".join(
            str(issue.get(k) or "")
            for k in ["summary", "title", "section"]
        )
    )

    match = _hf_std_re.search(r"\b(?:report\s+item\s+)?(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\b", blob, flags=_hf_std_re.I)
    return match.group(1) if match else ""


def _hf_std_title_case(value: str) -> str:
    text = _hf_std_safe_text(value)
    if not text:
        return ""

    replacements = {
        "Gfci": "GFCI",
        "Afci": "AFCI",
        "Hvac": "HVAC",
        "Pvc": "PVC",
        "A/c": "A/C",
        "Shut-Oì": "Shut-Off",
        "Shut-oì": "Shut-off",
        "Soïts": "Soffits",
    }

    titled = text.title()

    for old, new in replacements.items():
        titled = titled.replace(old, new)

    return titled


def _hf_std_normalize_title(issue: dict) -> str:
    title = _hf_std_safe_text(issue.get("title"))
    if not title:
        title = _hf_std_safe_text(issue.get("summary"))

    # Remove parser/report prefix noise when possible.
    title = _hf_std_re.sub(r"^Report item\s+\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\s+[—-]\s*", "", title, flags=_hf_std_re.I)
    title = title.split("—")[0].strip() if "—" in title else title

    return _hf_std_title_case(title)


def _hf_std_normalize_section(issue: dict) -> str:
    section = _hf_std_safe_text(issue.get("section"))
    section = section.replace("Shut-Oì", "Shut-Off").replace("Soïts", "Soffits")
    return section


def _hf_std_category(issue: dict) -> str:
    blob = _hf_std_lower_blob(issue)

    if any(x in blob for x in ["gfci", "afci", "electric", "electrical", "wiring", "breaker", "panel", "meter", "disconnect", "receptacle", "outlet"]):
        return "Electrical"

    if any(x in blob for x in ["plumbing", "water", "leak", "pipe", "drain", "valve", "shut-off", "shut off", "heater", "hot water"]):
        return "Plumbing"

    if any(x in blob for x in ["roof", "flashing", "gutter", "downspout", "shingle", "penetration"]):
        return "Roofing"

    if any(x in blob for x in ["exterior", "siding", "wall-covering", "wall covering", "eaves", "soffit", "fascia", "window", "door", "deck", "porch", "patio", "handrail", "railing", "grading", "vegetation"]):
        return "Exterior"

    if any(x in blob for x in ["heating", "cooling", "hvac", "thermostat", "furnace", "air conditioner"]):
        return "HVAC"

    if any(x in blob for x in ["kitchen", "bathroom", "interior", "floor", "ceiling", "wall", "range", "appliance"]):
        return "Interior"

    return "General"


def _hf_std_system(issue: dict, category: str) -> str:
    blob = _hf_std_lower_blob(issue)
    section = _hf_std_normalize_section(issue)

    if "gfci" in blob:
        return "GFCI Protection"
    if "afci" in blob:
        return "AFCI Protection"
    if "meter" in blob:
        return "Electrical Service"
    if "disconnect" in blob:
        return "Main Service Disconnect"
    if "panel" in blob or "breaker" in blob or "knockout" in blob:
        return "Panelboards & Breakers"
    if "wiring" in blob:
        return "Electrical Wiring"

    if "water heater" in blob or "hot water" in blob:
        return "Water Heater / Hot Water Source"
    if "shut-off" in blob or "shut off" in blob or "valve" in blob:
        return "Water Shut-Off Valve"
    if "water supply" in blob:
        return "Water Supply"
    if "drain" in blob or "pipe" in blob:
        return "Drain / Waste / Vent"

    if "gutter" in blob or "downspout" in blob:
        return "Gutters & Downspouts"
    if "flashing" in blob:
        return "Roof Flashing"
    if "roof" in blob:
        return "Roof Covering"

    if "window" in blob:
        return "Windows"
    if "door" in blob:
        return "Doors"
    if "deck" in blob or "porch" in blob or "patio" in blob:
        return "Deck / Porch / Patio"
    if "handrail" in blob or "railing" in blob:
        return "Railings / Guards / Handrails"
    if "soffit" in blob or "fascia" in blob or "eaves" in blob:
        return "Eaves / Soffits / Fascia"

    if "thermostat" in blob:
        return "Thermostat"
    if "heating" in blob:
        return "Heating System"
    if "cooling" in blob:
        return "Cooling System"

    return section or category or "General"


def _hf_std_component(issue: dict, system: str) -> str:
    blob = _hf_std_lower_blob(issue)

    if "gfci" in blob:
        return "GFCI outlet or circuit"
    if "afci" in blob:
        return "AFCI circuit protection"
    if "meter base" in blob:
        return "Electric meter / meter base"
    if "meter" in blob:
        return "Electric meter"
    if "disconnect" in blob:
        return "Main service disconnect"
    if "breaker" in blob:
        return "Breaker / electrical panel"
    if "panel" in blob:
        return "Electrical panel"
    if "wiring" in blob:
        return "Electrical wiring"

    if "valve" in blob or "shut-off" in blob or "shut off" in blob:
        return "Water shut-off valve"
    if "water heater" in blob:
        return "Water heater"
    if "pipe" in blob:
        return "Pipe / support"
    if "drain" in blob:
        return "Drain line"

    if "gutter" in blob:
        return "Gutter"
    if "downspout" in blob:
        return "Downspout"
    if "flashing" in blob:
        return "Flashing"
    if "roof" in blob:
        return "Roof covering or roof component"

    if "window" in blob:
        return "Window"
    if "door" in blob:
        return "Door"
    if "deck" in blob:
        return "Deck component"
    if "handrail" in blob:
        return "Handrail"
    if "railing" in blob:
        return "Railing"

    return system or "Component to confirm"


def _hf_std_defect_type(issue: dict) -> str:
    blob = _hf_std_lower_blob(issue)

    checks = [
        ("missing", "Missing"),
        ("not gfci", "Missing protection"),
        ("gfci", "Electrical protection defect"),
        ("afci", "Electrical protection defect"),
        ("improper", "Improper installation"),
        ("faulty", "Not functioning properly"),
        ("wouldn't reset", "Not functioning properly"),
        ("wont reset", "Not functioning properly"),
        ("leak", "Leak"),
        ("corrosion", "Corrosion"),
        ("rust", "Corrosion"),
        ("damaged", "Damaged"),
        ("damage", "Damaged"),
        ("loose", "Loose"),
        ("cracked", "Cracked"),
        ("rot", "Deterioration / rot"),
        ("older", "Aging / older system noted"),
        ("inadequate", "Inadequate support or installation"),
        ("major defect", "Major defect"),
        ("material defect", "Material defect"),
        ("defect", "Defect"),
    ]

    for token, label in checks:
        if token in blob:
            return label

    return "Needs review"


def _hf_std_severity(issue: dict) -> str:
    value = _hf_std_safe_text(issue.get("severity") or issue.get("priority") or issue.get("risk_level"))

    if value:
        lower = value.lower()
        if lower in ["critical", "urgent"]:
            return "Critical"
        if lower in ["high", "major"]:
            return "High"
        if lower in ["medium", "moderate"]:
            return "Medium"
        if lower in ["low", "minor"]:
            return "Low"

    blob = _hf_std_lower_blob(issue)

    if any(x in blob for x in ["major defect", "active water leak", "improper wiring", "missing gfci", "gfci", "afci", "panel", "breaker", "meter", "disconnect"]):
        return "High"

    if any(x in blob for x in ["damaged", "leak", "corrosion", "roof", "flashing", "gutter"]):
        return "Medium"

    return "Medium"


def _hf_std_risk_reasons(issue: dict, category: str, defect_type: str) -> list:
    blob = _hf_std_lower_blob(issue)

    if category == "Electrical":
        if "gfci" in blob:
            return ["shock risk", "wet-area electrical safety", "code/safety review"]
        if "afci" in blob:
            return ["electrical fire risk", "arc-fault protection", "code/safety review"]
        return ["shock risk", "fire risk", "electrical service reliability"]

    if category == "Plumbing":
        if "leak" in blob:
            return ["water damage", "mold risk", "hidden deterioration"]
        return ["water service reliability", "water damage risk", "maintenance concern"]

    if category == "Roofing":
        return ["water intrusion", "moisture damage", "roof system deterioration"]

    if category == "Exterior":
        return ["moisture intrusion", "deterioration", "safety or maintenance concern"]

    if category == "HVAC":
        return ["comfort issue", "system reliability", "efficiency or safety concern"]

    return ["maintenance tracking", "repair planning", "future monitoring"]


def _hf_std_trade(category: str, issue: dict) -> str:
    blob = _hf_std_lower_blob(issue)

    if category == "Electrical":
        return "Licensed electrician"
    if category == "Plumbing":
        return "Licensed plumber"
    if category == "Roofing":
        return "Qualified roofing contractor"
    if category == "HVAC":
        return "Qualified HVAC technician"

    if "window" in blob or "door" in blob or "deck" in blob or "handrail" in blob:
        return "Qualified contractor"

    return "Qualified contractor"


def _hf_std_plain_summary(issue: dict, category: str, system: str, component: str, defect_type: str) -> str:
    title = _hf_std_normalize_title(issue)
    location = _hf_std_normalize_section(issue)

    if category == "Electrical" and "GFCI" in system:
        return f"The inspection report notes possible missing or defective GFCI protection at {location or component}."

    if category == "Electrical" and "AFCI" in system:
        return f"The inspection report notes possible missing or defective AFCI protection at {location or component}."

    if category == "Electrical":
        return f"The inspection report notes an electrical concern involving {component or system}."

    if category == "Plumbing" and "Leak" in defect_type:
        return f"The inspection report notes an active or suspected water leak at {location or component}."

    if category == "Plumbing":
        return f"The inspection report notes a plumbing concern involving {component or system}."

    if category == "Roofing":
        return f"The inspection report notes a roof or drainage concern involving {component or system}."

    if category == "Exterior":
        return f"The inspection report notes an exterior concern involving {component or system}."

    if category == "HVAC":
        return f"The inspection report notes an HVAC concern involving {component or system}."

    if title:
        return f"The inspection report notes: {title}."

    return "The inspection report notes a finding that should be reviewed."


def _hf_std_recommended_action(issue: dict, trade: str, category: str) -> str:
    if category == "Electrical":
        return "Have a licensed electrician inspect this item and make the recommended corrections."
    if category == "Plumbing":
        return "Have a licensed plumber inspect this item and repair or monitor it as recommended."
    if category == "Roofing":
        return "Have a qualified roofing contractor review this item and repair or monitor it as recommended."
    if category == "HVAC":
        return "Have a qualified HVAC technician inspect this item and make the recommended corrections."

    return f"Have a {trade.lower()} review this item and make the recommended corrections."


def _hf_std_monitoring_plan(issue: dict, category: str, severity: str) -> str:
    if severity in ["Critical", "High"]:
        return "Track as an active priority item until repaired, professionally cleared, or admin-verified."
    if category in ["Roofing", "Plumbing", "Exterior"]:
        return "Monitor for worsening conditions, moisture issues, or completed repairs."
    return "Track until homeowner/admin decision confirms repair, monitoring, dismissal, or baseline lock."


def _hf_std_build_standard_json(issue: dict) -> dict:
    report_item = _hf_std_extract_report_item(issue)
    inspector_title = _hf_std_normalize_title(issue)
    source_section = _hf_std_normalize_section(issue)

    category = _hf_std_category(issue)
    system = _hf_std_system(issue, category)
    component = _hf_std_component(issue, system)
    defect_type = _hf_std_defect_type(issue)
    severity = _hf_std_severity(issue)
    risk_reasons = _hf_std_risk_reasons(issue, category, defect_type)
    trade = _hf_std_trade(category, issue)
    plain_summary = _hf_std_plain_summary(issue, category, system, component, defect_type)
    recommended_action = _hf_std_recommended_action(issue, trade, category)
    monitoring_plan = _hf_std_monitoring_plan(issue, category, severity)

    source = {
        "report_item": report_item,
        "report_page": issue.get("source_page") or issue.get("page") or None,
        "report_section": source_section,
        "inspector_title": inspector_title,
        "inspector_finding_text": _hf_std_safe_text(issue.get("inspector_finding_text") or issue.get("source_finding_text") or ""),
        "inspector_recommendation": _hf_std_safe_text(issue.get("inspector_recommendation") or issue.get("source_recommendation") or ""),
        "report_pdf_url": issue.get("report_pdf_url") or "",
        "report_page_url": issue.get("report_page_url") or "",
    }

    homefax = {
        "category": category,
        "system": system,
        "component": component,
        "location_area": source_section,
        "defect_type": defect_type,
        "severity": severity,
        "risk_level": severity,
        "risk_reasons": risk_reasons,
        "plain_summary": plain_summary,
        "recommended_trade": trade,
        "recommended_action": recommended_action,
        "monitoring_plan": monitoring_plan,
    }

    evidence = {
        "primary_image_url": issue.get("image_url") or "",
        "candidate_image_urls": issue.get("candidate_image_urls") or [],
        "homeowner_selected_image_url": issue.get("homeowner_selected_image_url") or "",
        "verified_image_url": issue.get("verified_image_url") or "",
        "image_status": issue.get("image_match_status") or "needs_review",
    }

    workflow = {
        "homeowner_decision": issue.get("homeowner_decision") or "unreviewed",
        "homeowner_image_decision": issue.get("homeowner_image_decision") or "unreviewed",
        "admin_review_status": issue.get("admin_review_status") or "pending",
        "baseline_locked": bool(issue.get("baseline_locked")),
        "current_status": issue.get("current_status") or issue.get("status") or "active",
    }

    return {
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "source": source,
        "homefax": homefax,
        "evidence": evidence,
        "workflow": workflow,
        "generated_at": _hf_std_datetime.utcnow().isoformat() + "Z",
    }


def _hf_std_column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    row = cursor.fetchone()
    if isinstance(row, dict):
        return int(list(row.values())[0] or 0) > 0
    return int(row[0] or 0) > 0


def _hf_std_add_column(cursor, table_name: str, column_name: str, column_sql: str) -> bool:
    if _hf_std_column_exists(cursor, table_name, column_name):
        return False

    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
    return True


def _hf_std_ensure_schema() -> dict:
    conn = _hf_report_db_connection() if "_hf_report_db_connection" in globals() else None
    if conn is None:
        # Fall back to common DB helper names.
        for name in ("get_db_connection", "get_connection", "db_connection"):
            fn = globals().get(name)
            if callable(fn):
                conn = fn()
                break

    if conn is None:
        raise RuntimeError("No database connection helper found.")

    added = []

    try:
        with conn.cursor() as cursor:
            columns = [
                ("homefax_standard_schema_version", "VARCHAR(80) NULL"),
                ("homefax_standard_json", "JSON NULL"),

                ("source_item_number", "VARCHAR(80) NULL"),
                ("source_report_section", "TEXT NULL"),
                ("source_finding_title", "TEXT NULL"),
                ("source_finding_text", "TEXT NULL"),
                ("source_recommendation", "TEXT NULL"),

                ("standard_category", "VARCHAR(120) NULL"),
                ("standard_system", "VARCHAR(180) NULL"),
                ("standard_component", "VARCHAR(180) NULL"),
                ("standard_defect_type", "VARCHAR(180) NULL"),
                ("standard_location_area", "TEXT NULL"),
                ("standard_severity", "VARCHAR(80) NULL"),
                ("standard_risk_reasons", "JSON NULL"),
                ("standard_plain_summary", "TEXT NULL"),
                ("standard_recommended_trade", "VARCHAR(180) NULL"),
                ("standard_recommended_action", "TEXT NULL"),
                ("standard_monitoring_plan", "TEXT NULL"),
            ]

            for column_name, column_sql in columns:
                try:
                    if _hf_std_add_column(cursor, "verified_issues", column_name, column_sql):
                        added.append(column_name)
                except Exception as error:
                    print("[HomeFax Standard Schema] column warning:", column_name, error)

        conn.commit()

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "success": True,
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "added_columns": added,
    }


def _hf_std_fetch_issues(record_id: str) -> list:
    conn = _hf_report_db_connection() if "_hf_report_db_connection" in globals() else None
    if conn is None:
        for name in ("get_db_connection", "get_connection", "db_connection"):
            fn = globals().get(name)
            if callable(fn):
                conn = fn()
                break

    if conn is None:
        raise RuntimeError("No database connection helper found.")

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM verified_issues WHERE record_id = %s ORDER BY id ASC", (record_id,))
            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                cols = [desc[0] for desc in cursor.description]
                rows = [dict(zip(cols, row)) for row in rows]

            return rows

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_std_backfill_record(record_id: str) -> dict:
    _hf_std_ensure_schema()

    issues = _hf_std_fetch_issues(record_id)
    updated = 0
    samples = []

    conn = _hf_report_db_connection() if "_hf_report_db_connection" in globals() else None
    if conn is None:
        for name in ("get_db_connection", "get_connection", "db_connection"):
            fn = globals().get(name)
            if callable(fn):
                conn = fn()
                break

    if conn is None:
        raise RuntimeError("No database connection helper found.")

    try:
        with conn.cursor() as cursor:
            for issue in issues:
                standard = _hf_std_build_standard_json(issue)
                source = standard["source"]
                homefax = standard["homefax"]

                cursor.execute(
                    """
                    UPDATE verified_issues
                    SET
                        homefax_standard_schema_version = %s,
                        homefax_standard_json = %s,

                        source_item_number = %s,
                        source_report_section = %s,
                        source_finding_title = %s,
                        source_finding_text = %s,
                        source_recommendation = %s,

                        standard_category = %s,
                        standard_system = %s,
                        standard_component = %s,
                        standard_defect_type = %s,
                        standard_location_area = %s,
                        standard_severity = %s,
                        standard_risk_reasons = %s,
                        standard_plain_summary = %s,
                        standard_recommended_trade = %s,
                        standard_recommended_action = %s,
                        standard_monitoring_plan = %s
                    WHERE id = %s
                    """,
                    (
                        _HF_STANDARD_SCHEMA_VERSION,
                        _hf_std_json.dumps(standard, default=str),

                        source.get("report_item"),
                        source.get("report_section"),
                        source.get("inspector_title"),
                        source.get("inspector_finding_text"),
                        source.get("inspector_recommendation"),

                        homefax.get("category"),
                        homefax.get("system"),
                        homefax.get("component"),
                        homefax.get("defect_type"),
                        homefax.get("location_area"),
                        homefax.get("severity"),
                        _hf_std_json.dumps(homefax.get("risk_reasons") or []),
                        homefax.get("plain_summary"),
                        homefax.get("recommended_trade"),
                        homefax.get("recommended_action"),
                        homefax.get("monitoring_plan"),

                        issue.get("id"),
                    ),
                )

                updated += 1

                if len(samples) < 5:
                    samples.append(
                        {
                            "id": issue.get("id"),
                            "source_item_number": source.get("report_item"),
                            "source_finding_title": source.get("inspector_title"),
                            "standard_category": homefax.get("category"),
                            "standard_system": homefax.get("system"),
                            "standard_component": homefax.get("component"),
                            "standard_defect_type": homefax.get("defect_type"),
                            "standard_plain_summary": homefax.get("plain_summary"),
                            "standard_recommended_trade": homefax.get("recommended_trade"),
                            "standard_monitoring_plan": homefax.get("monitoring_plan"),
                        }
                    )

        conn.commit()

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "success": True,
        "record_id": record_id,
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "issues_found": len(issues),
        "issues_updated": updated,
        "samples": samples,
    }


@app.get("/homefax-standard-schema-health")
def homefax_standard_schema_health():
    schema = _hf_std_ensure_schema()

    return {
        "success": True,
        "schema": schema,
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "endpoints": [
            "GET /homefax-standard-schema-health",
            "POST /records/{record_id}/homefax-standard-schema/backfill",
            "GET /records/{record_id}/homefax-standard-report-preview",
        ],
    }


@app.post("/records/{record_id}/homefax-standard-schema/backfill")
def homefax_standard_schema_backfill(record_id: str):
    return _hf_std_backfill_record(record_id)


@app.get("/records/{record_id}/homefax-standard-report-preview")
def homefax_standard_report_preview(record_id: str, limit: int = 10):
    _hf_std_ensure_schema()
    issues = _hf_std_fetch_issues(record_id)

    preview = []

    for issue in issues[: max(1, min(int(limit or 10), 50))]:
        standard = _hf_std_build_standard_json(issue)

        preview.append(
            {
                "id": issue.get("id"),
                "title": issue.get("title"),
                "source_item_number": standard["source"].get("report_item"),
                "source_finding_title": standard["source"].get("inspector_title"),
                "source_finding_text": standard["source"].get("inspector_finding_text"),
                "source_recommendation": standard["source"].get("inspector_recommendation"),
                "category": standard["homefax"].get("category"),
                "system": standard["homefax"].get("system"),
                "component": standard["homefax"].get("component"),
                "defect_type": standard["homefax"].get("defect_type"),
                "plain_summary": standard["homefax"].get("plain_summary"),
                "recommended_trade": standard["homefax"].get("recommended_trade"),
                "recommended_action": standard["homefax"].get("recommended_action"),
                "monitoring_plan": standard["homefax"].get("monitoring_plan"),
                "primary_image_url": standard["evidence"].get("primary_image_url"),
                "candidate_image_count": len(standard["evidence"].get("candidate_image_urls") or []),
            }
        )

    return {
        "success": True,
        "record_id": record_id,
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "issues_previewed": len(preview),
        "issues_total": len(issues),
        "issues": preview,
    }



# ============================================================
# HomeFax Standard Schema Hardening Patch 1
# Fixes:
# - candidate_image_urls string/list normalization
# - downspout drainage classification
# - better exterior drainage summaries
# - standard finding payload helpers
# ============================================================

import json as _hf_harden_json
import re as _hf_harden_re


def _hf_harden_safe_text(value) -> str:
    return _hf_harden_re.sub(r"\s+", " ", str(value or "")).strip()


def _hf_harden_parse_candidate_urls(value):
    """
    Normalize candidate_image_urls into a real list.

    Handles:
    - Python list
    - JSON string list
    - comma-separated string
    - single URL string
    - None
    """
    if not value:
        return []

    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v or "").strip()]

    if isinstance(value, tuple):
        return [str(v).strip() for v in value if str(v or "").strip()]

    if isinstance(value, str):
        raw = value.strip()

        if not raw:
            return []

        # JSON list string
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = _hf_harden_json.loads(raw)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v or "").strip()]
            except Exception:
                pass

        # Sometimes Python repr list sneaks in with single quotes.
        if raw.startswith("[") and raw.endswith("]"):
            try:
                normalized = raw.replace("'", '"')
                parsed = _hf_harden_json.loads(normalized)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v or "").strip()]
            except Exception:
                pass

        if "," in raw:
            return [part.strip() for part in raw.split(",") if part.strip()]

        return [raw]

    return []


def _hf_harden_unique_urls(urls):
    seen = set()
    clean = []

    for url in urls or []:
        item = str(url or "").strip()
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        clean.append(item)

    return clean


def _hf_harden_candidate_urls_from_issue(issue: dict) -> list:
    issue = issue or {}
    return _hf_harden_unique_urls(_hf_harden_parse_candidate_urls(issue.get("candidate_image_urls")))


def _hf_harden_candidate_count(issue: dict) -> int:
    return len(_hf_harden_candidate_urls_from_issue(issue))


def _hf_harden_blob(issue: dict) -> str:
    issue = issue or {}
    parts = [
        issue.get("title"),
        issue.get("summary"),
        issue.get("section"),
        issue.get("source_report_section"),
        issue.get("source_finding_title"),
        issue.get("standard_category"),
        issue.get("standard_system"),
        issue.get("standard_component"),
        issue.get("standard_defect_type"),
    ]
    return _hf_harden_safe_text(" ".join(str(p or "") for p in parts)).lower()


def _hf_harden_is_downspout_drainage(issue: dict) -> bool:
    blob = _hf_harden_blob(issue)

    return (
        "downspout" in blob
        or "gutter" in blob
        or "gutters" in blob
    ) and (
        "drain near" in blob
        or "drainage" in blob
        or "near house" in blob
        or "near foundation" in blob
        or "discharge" in blob
        or "extension" in blob
        or "splash block" in blob
    )


# Override prior standard category logic with hardened exterior drainage handling.
def _hf_std_category(issue: dict) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_harden_blob(issue)

    if _hf_harden_is_downspout_drainage(issue):
        return "Roofing"

    if any(x in blob for x in ["gfci", "afci", "electric", "electrical", "wiring", "breaker", "panel", "meter", "disconnect", "receptacle", "outlet"]):
        return "Electrical"

    # Downspout/gutter must be evaluated before general plumbing drain terms.
    if any(x in blob for x in ["roof", "flashing", "gutter", "gutters", "downspout", "shingle", "penetration"]):
        return "Roofing"

    if any(x in blob for x in ["plumbing", "water", "leak", "pipe", "drain", "valve", "shut-off", "shut off", "heater", "hot water"]):
        return "Plumbing"

    if any(x in blob for x in ["exterior", "siding", "wall-covering", "wall covering", "eaves", "soffit", "fascia", "window", "door", "deck", "porch", "patio", "handrail", "railing", "grading", "vegetation"]):
        return "Exterior"

    if any(x in blob for x in ["heating", "cooling", "hvac", "thermostat", "furnace", "air conditioner"]):
        return "HVAC"

    if any(x in blob for x in ["kitchen", "bathroom", "interior", "floor", "ceiling", "wall", "range", "appliance"]):
        return "Interior"

    return "General"


# Override prior standard system logic with hardened gutter/downspout handling.
def _hf_std_system(issue: dict, category: str) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_harden_blob(issue)
    section = _hf_std_normalize_section(issue) if "_hf_std_normalize_section" in globals() else _hf_harden_safe_text(issue.get("section"))

    if _hf_harden_is_downspout_drainage(issue):
        return "Gutters & Downspouts"

    if "gfci" in blob:
        return "GFCI Protection"
    if "afci" in blob:
        return "AFCI Protection"
    if "meter" in blob:
        return "Electrical Service"
    if "disconnect" in blob:
        return "Main Service Disconnect"
    if "panel" in blob or "breaker" in blob or "knockout" in blob:
        return "Panelboards & Breakers"
    if "wiring" in blob:
        return "Electrical Wiring"

    if "water heater" in blob or "hot water" in blob:
        return "Water Heater / Hot Water Source"
    if "shut-off" in blob or "shut off" in blob or "valve" in blob:
        return "Water Shut-Off Valve"
    if "water supply" in blob:
        return "Water Supply"
    if "drain" in blob or "pipe" in blob:
        return "Drain / Waste / Vent"

    if "gutter" in blob or "gutters" in blob or "downspout" in blob:
        return "Gutters & Downspouts"
    if "flashing" in blob:
        return "Roof Flashing"
    if "roof" in blob:
        return "Roof Covering"

    if "window" in blob:
        return "Windows"
    if "door" in blob:
        return "Doors"
    if "deck" in blob or "porch" in blob or "patio" in blob:
        return "Deck / Porch / Patio"
    if "handrail" in blob or "railing" in blob:
        return "Railings / Guards / Handrails"
    if "soffit" in blob or "fascia" in blob or "eaves" in blob:
        return "Eaves / Soffits / Fascia"

    if "thermostat" in blob:
        return "Thermostat"
    if "heating" in blob:
        return "Heating System"
    if "cooling" in blob:
        return "Cooling System"

    return section or category or "General"


# Override prior component logic with downspout handling.
def _hf_std_component(issue: dict, system: str) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_harden_blob(issue)

    if _hf_harden_is_downspout_drainage(issue):
        return "Downspout discharge / drainage termination"

    if "downspout" in blob:
        return "Downspout"
    if "gutter" in blob or "gutters" in blob:
        return "Gutter"

    if "gfci" in blob:
        return "GFCI outlet or circuit"
    if "afci" in blob:
        return "AFCI circuit protection"
    if "meter base" in blob:
        return "Electric meter / meter base"
    if "meter" in blob:
        return "Electric meter"
    if "disconnect" in blob:
        return "Main service disconnect"
    if "breaker" in blob:
        return "Breaker / electrical panel"
    if "panel" in blob:
        return "Electrical panel"
    if "wiring" in blob:
        return "Electrical wiring"

    if "valve" in blob or "shut-off" in blob or "shut off" in blob:
        return "Water shut-off valve"
    if "water heater" in blob:
        return "Water heater"
    if "pipe" in blob:
        return "Pipe / support"
    if "drain" in blob:
        return "Drain line"

    if "flashing" in blob:
        return "Flashing"
    if "roof" in blob:
        return "Roof covering or roof component"

    if "window" in blob:
        return "Window"
    if "door" in blob:
        return "Door"
    if "deck" in blob:
        return "Deck component"
    if "handrail" in blob:
        return "Handrail"
    if "railing" in blob:
        return "Railing"

    return system or "Component to confirm"


# Override defect type for drainage terms.
def _hf_std_defect_type(issue: dict) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_harden_blob(issue)

    if _hf_harden_is_downspout_drainage(issue):
        return "Drainage too close to house"
# Override plain summary to make gutter/downspout findings clearer.
def _hf_std_plain_summary(issue: dict, category: str, system: str, component: str, defect_type: str) -> str:
    title = _hf_std_normalize_title(issue) if "_hf_std_normalize_title" in globals() else _hf_harden_safe_text(issue.get("title"))
    location = _hf_std_normalize_section(issue) if "_hf_std_normalize_section" in globals() else _hf_harden_safe_text(issue.get("section"))

    if _hf_harden_is_downspout_drainage(issue):
        return "The inspection report notes that downspouts may be discharging too close to the house. This can move roof water toward the foundation instead of away from it."

    if category == "Roofing" and ("Gutter" in system or "Downspout" in system):
        return f"The inspection report notes a gutter or downspout concern involving {component or system}."

    if category == "Electrical" and "GFCI" in system:
        return f"The inspection report notes possible missing or defective GFCI protection at {location or component}."

    if category == "Electrical" and "AFCI" in system:
        return f"The inspection report notes possible missing or defective AFCI protection at {location or component}."

    if category == "Electrical":
        return f"The inspection report notes an electrical concern involving {component or system}."

    if category == "Plumbing" and "Leak" in defect_type:
        return f"The inspection report notes an active or suspected water leak at {location or component}."

    if category == "Plumbing":
        return f"The inspection report notes a plumbing concern involving {component or system}."

    if category == "Roofing":
        return f"The inspection report notes a roof or drainage concern involving {component or system}."

    if category == "Exterior":
        return f"The inspection report notes an exterior concern involving {component or system}."

    if category == "HVAC":
        return f"The inspection report notes an HVAC concern involving {component or system}."

    if title:
        return f"The inspection report notes: {title}."

    return "The inspection report notes a finding that should be reviewed."


# Override risk reasons to handle downspout drainage.
def _hf_std_risk_reasons(issue: dict, category: str, defect_type: str) -> list:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_harden_blob(issue)

    if _hf_harden_is_downspout_drainage(issue):
        return ["foundation moisture risk", "basement or crawlspace water risk", "poor roof drainage"]

    if category == "Electrical":
        if "gfci" in blob:
            return ["shock risk", "wet-area electrical safety", "code/safety review"]
        if "afci" in blob:
            return ["electrical fire risk", "arc-fault protection", "code/safety review"]
        return ["shock risk", "fire risk", "electrical service reliability"]

    if category == "Plumbing":
        if "leak" in blob:
            return ["water damage", "mold risk", "hidden deterioration"]
        return ["water service reliability", "water damage risk", "maintenance concern"]

    if category == "Roofing":
        return ["water intrusion", "moisture damage", "roof system deterioration"]

    if category == "Exterior":
        return ["moisture intrusion", "deterioration", "safety or maintenance concern"]

    if category == "HVAC":
        return ["comfort issue", "system reliability", "efficiency or safety concern"]

    return ["maintenance tracking", "repair planning", "future monitoring"]


# Override standard JSON builder only for evidence normalization.
def _hf_std_build_standard_json(issue: dict) -> dict:
    report_item = _hf_std_extract_report_item(issue)
    inspector_title = _hf_std_normalize_title(issue)
    source_section = _hf_std_normalize_section(issue)

    category = _hf_std_category(issue)
    system = _hf_std_system(issue, category)
    component = _hf_std_component(issue, system)
    defect_type = _hf_std_defect_type(issue)
    severity = _hf_std_severity(issue)
    risk_reasons = _hf_std_risk_reasons(issue, category, defect_type)
    trade = _hf_std_trade(category, issue)
    plain_summary = _hf_std_plain_summary(issue, category, system, component, defect_type)
    recommended_action = _hf_std_recommended_action(issue, trade, category)
    monitoring_plan = _hf_std_monitoring_plan(issue, category, severity)

    candidate_urls = _hf_harden_candidate_urls_from_issue(issue)

    source = {
        "report_item": report_item,
        "report_page": issue.get("source_page") or issue.get("page") or None,
        "report_section": source_section,
        "inspector_title": inspector_title,
        "inspector_finding_text": _hf_std_safe_text(issue.get("inspector_finding_text") or issue.get("source_finding_text") or ""),
        "inspector_recommendation": _hf_std_safe_text(issue.get("inspector_recommendation") or issue.get("source_recommendation") or ""),
        "report_pdf_url": issue.get("report_pdf_url") or "",
        "report_page_url": issue.get("report_page_url") or "",
    }

    homefax = {
        "category": category,
        "system": system,
        "component": component,
        "location_area": source_section,
        "defect_type": defect_type,
        "severity": severity,
        "risk_level": severity,
        "risk_reasons": risk_reasons,
        "plain_summary": plain_summary,
        "recommended_trade": trade,
        "recommended_action": recommended_action,
        "monitoring_plan": monitoring_plan,
    }

    evidence = {
        "primary_image_url": issue.get("image_url") or "",
        "candidate_image_urls": candidate_urls,
        "candidate_image_count": len(candidate_urls),
        "homeowner_selected_image_url": issue.get("homeowner_selected_image_url") or "",
        "verified_image_url": issue.get("verified_image_url") or "",
        "image_status": issue.get("image_match_status") or "needs_review",
    }

    workflow = {
        "homeowner_decision": issue.get("homeowner_decision") or "unreviewed",
        "homeowner_image_decision": issue.get("homeowner_image_decision") or "unreviewed",
        "admin_review_status": issue.get("admin_review_status") or "pending",
        "baseline_locked": bool(issue.get("baseline_locked")),
        "current_status": issue.get("current_status") or issue.get("status") or "active",
    }

    return {
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "source": source,
        "homefax": homefax,
        "evidence": evidence,
        "workflow": workflow,
        "generated_at": _hf_std_datetime.utcnow().isoformat() + "Z",
    }


@app.get("/records/{record_id}/homefax-standard-report-preview-v2")
def homefax_standard_report_preview_v2(record_id: str, limit: int = 10):
    _hf_std_ensure_schema()
    issues = _hf_std_fetch_issues(record_id)

    preview = []

    for issue in issues[: max(1, min(int(limit or 10), 50))]:
        standard = _hf_std_build_standard_json(issue)

        preview.append(
            {
                "id": issue.get("id"),
                "title": issue.get("title"),
                "source_item_number": standard["source"].get("report_item"),
                "source_finding_title": standard["source"].get("inspector_title"),
                "source_finding_text": standard["source"].get("inspector_finding_text"),
                "source_recommendation": standard["source"].get("inspector_recommendation"),
                "category": standard["homefax"].get("category"),
                "system": standard["homefax"].get("system"),
                "component": standard["homefax"].get("component"),
                "defect_type": standard["homefax"].get("defect_type"),
                "plain_summary": standard["homefax"].get("plain_summary"),
                "recommended_trade": standard["homefax"].get("recommended_trade"),
                "recommended_action": standard["homefax"].get("recommended_action"),
                "monitoring_plan": standard["homefax"].get("monitoring_plan"),
                "risk_reasons": standard["homefax"].get("risk_reasons"),
                "primary_image_url": standard["evidence"].get("primary_image_url"),
                "candidate_image_count": standard["evidence"].get("candidate_image_count", 0),
                "candidate_image_urls": standard["evidence"].get("candidate_image_urls", [])[:10],
            }
        )

    return {
        "success": True,
        "record_id": record_id,
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "issues_previewed": len(preview),
        "issues_total": len(issues),
        "issues": preview,
    }


@app.post("/records/{record_id}/homefax-standard-schema/backfill-v2")
def homefax_standard_schema_backfill_v2(record_id: str):
    return _hf_std_backfill_record(record_id)



# ============================================================
# HomeFax Standard Schema Hardening Patch 1B
# Fixes None defect_type crash in preview-v2/backfill-v2.
# Also makes defect_type fallback safer for standard report output.
# ============================================================

def _hf_std_defect_type(issue: dict) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_harden_blob(issue)

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return "Drainage too close to house"

    checks = [
        ("missing", "Missing"),
        ("not gfci", "Missing protection"),
        ("gfci", "Electrical protection defect"),
        ("afci", "Electrical protection defect"),
        ("improper", "Improper installation"),
        ("installation defect", "Installation defect"),
        ("fastening defect", "Fastening defect"),
        ("faulty", "Not functioning properly"),
        ("wouldn't reset", "Not functioning properly"),
        ("wont reset", "Not functioning properly"),
        ("leak", "Leak"),
        ("corrosion", "Corrosion"),
        ("rust", "Corrosion"),
        ("damaged", "Damaged"),
        ("damage", "Damaged"),
        ("loose", "Loose"),
        ("cracked", "Cracked"),
        ("rot", "Deterioration / rot"),
        ("older", "Aging / older system noted"),
        ("inadequate", "Inadequate support or installation"),
        ("major defect", "Major defect"),
        ("material defect", "Material defect"),
        ("defect", "Defect"),
    ]

    for token, label in checks:
        if token in blob:
            return label

    return "Needs review"


def _hf_std_plain_summary(issue: dict, category: str, system: str, component: str, defect_type: str) -> str:
    title = _hf_std_normalize_title(issue) if "_hf_std_normalize_title" in globals() else _hf_harden_safe_text(issue.get("title"))
    location = _hf_std_normalize_section(issue) if "_hf_std_normalize_section" in globals() else _hf_harden_safe_text(issue.get("section"))

    category = _hf_harden_safe_text(category) or "General"
    system = _hf_harden_safe_text(system) or category
    component = _hf_harden_safe_text(component) or system
    defect_type = _hf_harden_safe_text(defect_type) or "Needs review"

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return "The inspection report notes that downspouts may be discharging too close to the house. This can move roof water toward the foundation instead of away from it."

    if category == "Roofing" and ("Gutter" in system or "Downspout" in system):
        return f"The inspection report notes a gutter or downspout concern involving {component or system}."

    if category == "Electrical" and "GFCI" in system:
        return f"The inspection report notes possible missing or defective GFCI protection at {location or component}."

    if category == "Electrical" and "AFCI" in system:
        return f"The inspection report notes possible missing or defective AFCI protection at {location or component}."

    if category == "Electrical":
        return f"The inspection report notes an electrical concern involving {component or system}."

    if category == "Plumbing" and "Leak" in defect_type:
        return f"The inspection report notes an active or suspected water leak at {location or component}."

    if category == "Plumbing":
        return f"The inspection report notes a plumbing concern involving {component or system}."

    if category == "Roofing":
        return f"The inspection report notes a roof or drainage concern involving {component or system}."

    if category == "Exterior":
        return f"The inspection report notes an exterior concern involving {component or system}."

    if category == "HVAC":
        return f"The inspection report notes an HVAC concern involving {component or system}."

    if title:
        return f"The inspection report notes: {title}."

    return "The inspection report notes a finding that should be reviewed."


def _hf_std_build_standard_json(issue: dict) -> dict:
    report_item = _hf_std_extract_report_item(issue)
    inspector_title = _hf_std_normalize_title(issue)
    source_section = _hf_std_normalize_section(issue)

    category = _hf_std_category(issue) or "General"
    system = _hf_std_system(issue, category) or category
    component = _hf_std_component(issue, system) or system
    defect_type = _hf_std_defect_type(issue) or "Needs review"
    severity = _hf_std_severity(issue) or "Medium"
    risk_reasons = _hf_std_risk_reasons(issue, category, defect_type) or []
    trade = _hf_std_trade(category, issue) or "Qualified contractor"
    plain_summary = _hf_std_plain_summary(issue, category, system, component, defect_type)
    recommended_action = _hf_std_recommended_action(issue, trade, category)
    monitoring_plan = _hf_std_monitoring_plan(issue, category, severity)

    candidate_urls = _hf_harden_candidate_urls_from_issue(issue) if "_hf_harden_candidate_urls_from_issue" in globals() else []

    source = {
        "report_item": report_item,
        "report_page": issue.get("source_page") or issue.get("page") or None,
        "report_section": source_section,
        "inspector_title": inspector_title,
        "inspector_finding_text": _hf_std_safe_text(issue.get("inspector_finding_text") or issue.get("source_finding_text") or ""),
        "inspector_recommendation": _hf_std_safe_text(issue.get("inspector_recommendation") or issue.get("source_recommendation") or ""),
        "report_pdf_url": issue.get("report_pdf_url") or "",
        "report_page_url": issue.get("report_page_url") or "",
    }

    homefax = {
        "category": category,
        "system": system,
        "component": component,
        "location_area": source_section,
        "defect_type": defect_type,
        "severity": severity,
        "risk_level": severity,
        "risk_reasons": risk_reasons,
        "plain_summary": plain_summary,
        "recommended_trade": trade,
        "recommended_action": recommended_action,
        "monitoring_plan": monitoring_plan,
    }

    evidence = {
        "primary_image_url": issue.get("image_url") or "",
        "candidate_image_urls": candidate_urls,
        "candidate_image_count": len(candidate_urls),
        "homeowner_selected_image_url": issue.get("homeowner_selected_image_url") or "",
        "verified_image_url": issue.get("verified_image_url") or "",
        "image_status": issue.get("image_match_status") or "needs_review",
    }

    workflow = {
        "homeowner_decision": issue.get("homeowner_decision") or "unreviewed",
        "homeowner_image_decision": issue.get("homeowner_image_decision") or "unreviewed",
        "admin_review_status": issue.get("admin_review_status") or "pending",
        "baseline_locked": bool(issue.get("baseline_locked")),
        "current_status": issue.get("current_status") or issue.get("status") or "active",
    }

    return {
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "source": source,
        "homefax": homefax,
        "evidence": evidence,
        "workflow": workflow,
        "generated_at": _hf_std_datetime.utcnow().isoformat() + "Z",
    }



# ============================================================
# HomeFax Standard Schema Hardening Patch 1B
# Fixes None defect_type crash in preview-v2/backfill-v2.
# Also makes defect_type fallback safer for standard report output.
# ============================================================

def _hf_std_defect_type(issue: dict) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_harden_blob(issue)

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return "Drainage too close to house"

    checks = [
        ("missing", "Missing"),
        ("not gfci", "Missing protection"),
        ("gfci", "Electrical protection defect"),
        ("afci", "Electrical protection defect"),
        ("improper", "Improper installation"),
        ("installation defect", "Installation defect"),
        ("fastening defect", "Fastening defect"),
        ("faulty", "Not functioning properly"),
        ("wouldn't reset", "Not functioning properly"),
        ("wont reset", "Not functioning properly"),
        ("leak", "Leak"),
        ("corrosion", "Corrosion"),
        ("rust", "Corrosion"),
        ("damaged", "Damaged"),
        ("damage", "Damaged"),
        ("loose", "Loose"),
        ("cracked", "Cracked"),
        ("rot", "Deterioration / rot"),
        ("older", "Aging / older system noted"),
        ("inadequate", "Inadequate support or installation"),
        ("major defect", "Major defect"),
        ("material defect", "Material defect"),
        ("defect", "Defect"),
    ]

    for token, label in checks:
        if token in blob:
            return label

    return "Needs review"


def _hf_std_plain_summary(issue: dict, category: str, system: str, component: str, defect_type: str) -> str:
    title = _hf_std_normalize_title(issue) if "_hf_std_normalize_title" in globals() else _hf_harden_safe_text(issue.get("title"))
    location = _hf_std_normalize_section(issue) if "_hf_std_normalize_section" in globals() else _hf_harden_safe_text(issue.get("section"))

    category = _hf_harden_safe_text(category) or "General"
    system = _hf_harden_safe_text(system) or category
    component = _hf_harden_safe_text(component) or system
    defect_type = _hf_harden_safe_text(defect_type) or "Needs review"

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return "The inspection report notes that downspouts may be discharging too close to the house. This can move roof water toward the foundation instead of away from it."

    if category == "Roofing" and ("Gutter" in system or "Downspout" in system):
        return f"The inspection report notes a gutter or downspout concern involving {component or system}."

    if category == "Electrical" and "GFCI" in system:
        return f"The inspection report notes possible missing or defective GFCI protection at {location or component}."

    if category == "Electrical" and "AFCI" in system:
        return f"The inspection report notes possible missing or defective AFCI protection at {location or component}."

    if category == "Electrical":
        return f"The inspection report notes an electrical concern involving {component or system}."

    if category == "Plumbing" and "Leak" in defect_type:
        return f"The inspection report notes an active or suspected water leak at {location or component}."

    if category == "Plumbing":
        return f"The inspection report notes a plumbing concern involving {component or system}."

    if category == "Roofing":
        return f"The inspection report notes a roof or drainage concern involving {component or system}."

    if category == "Exterior":
        return f"The inspection report notes an exterior concern involving {component or system}."

    if category == "HVAC":
        return f"The inspection report notes an HVAC concern involving {component or system}."

    if title:
        return f"The inspection report notes: {title}."

    return "The inspection report notes a finding that should be reviewed."


def _hf_std_build_standard_json(issue: dict) -> dict:
    report_item = _hf_std_extract_report_item(issue)
    inspector_title = _hf_std_normalize_title(issue)
    source_section = _hf_std_normalize_section(issue)

    category = _hf_std_category(issue) or "General"
    system = _hf_std_system(issue, category) or category
    component = _hf_std_component(issue, system) or system
    defect_type = _hf_std_defect_type(issue) or "Needs review"
    severity = _hf_std_severity(issue) or "Medium"
    risk_reasons = _hf_std_risk_reasons(issue, category, defect_type) or []
    trade = _hf_std_trade(category, issue) or "Qualified contractor"
    plain_summary = _hf_std_plain_summary(issue, category, system, component, defect_type)
    recommended_action = _hf_std_recommended_action(issue, trade, category)
    monitoring_plan = _hf_std_monitoring_plan(issue, category, severity)

    candidate_urls = _hf_harden_candidate_urls_from_issue(issue) if "_hf_harden_candidate_urls_from_issue" in globals() else []

    source = {
        "report_item": report_item,
        "report_page": issue.get("source_page") or issue.get("page") or None,
        "report_section": source_section,
        "inspector_title": inspector_title,
        "inspector_finding_text": _hf_std_safe_text(issue.get("inspector_finding_text") or issue.get("source_finding_text") or ""),
        "inspector_recommendation": _hf_std_safe_text(issue.get("inspector_recommendation") or issue.get("source_recommendation") or ""),
        "report_pdf_url": issue.get("report_pdf_url") or "",
        "report_page_url": issue.get("report_page_url") or "",
    }

    homefax = {
        "category": category,
        "system": system,
        "component": component,
        "location_area": source_section,
        "defect_type": defect_type,
        "severity": severity,
        "risk_level": severity,
        "risk_reasons": risk_reasons,
        "plain_summary": plain_summary,
        "recommended_trade": trade,
        "recommended_action": recommended_action,
        "monitoring_plan": monitoring_plan,
    }

    evidence = {
        "primary_image_url": issue.get("image_url") or "",
        "candidate_image_urls": candidate_urls,
        "candidate_image_count": len(candidate_urls),
        "homeowner_selected_image_url": issue.get("homeowner_selected_image_url") or "",
        "verified_image_url": issue.get("verified_image_url") or "",
        "image_status": issue.get("image_match_status") or "needs_review",
    }

    workflow = {
        "homeowner_decision": issue.get("homeowner_decision") or "unreviewed",
        "homeowner_image_decision": issue.get("homeowner_image_decision") or "unreviewed",
        "admin_review_status": issue.get("admin_review_status") or "pending",
        "baseline_locked": bool(issue.get("baseline_locked")),
        "current_status": issue.get("current_status") or issue.get("status") or "active",
    }

    return {
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "source": source,
        "homefax": homefax,
        "evidence": evidence,
        "workflow": workflow,
        "generated_at": _hf_std_datetime.utcnow().isoformat() + "Z",
    }



# ============================================================
# HomeFax Standard Preview Route Cleanup Patch 1
# Fixes:
# - old /homefax-standard-report-preview route now uses v2 logic
# - fence defects classify as Exterior / Fence
# ============================================================


def _hf_cleanup_blob(issue: dict) -> str:
    if "_hf_harden_blob" in globals():
        return _hf_harden_blob(issue)

    parts = [
        issue.get("title") if issue else "",
        issue.get("summary") if issue else "",
        issue.get("section") if issue else "",
        issue.get("source_report_section") if issue else "",
        issue.get("source_finding_title") if issue else "",
    ]

    return " ".join(str(part or "") for part in parts).lower()


def _hf_cleanup_is_fence_issue(issue: dict) -> bool:
    blob = _hf_cleanup_blob(issue)
    return "fence" in blob


def _hf_std_category(issue: dict) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_cleanup_blob(issue)

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return "Roofing"

    if _hf_cleanup_is_fence_issue(issue):
        return "Exterior"

    if any(x in blob for x in ["gfci", "afci", "electric", "electrical", "wiring", "breaker", "panel", "meter", "disconnect", "receptacle", "outlet"]):
        return "Electrical"

    if any(x in blob for x in ["roof", "flashing", "gutter", "gutters", "downspout", "shingle", "penetration"]):
        return "Roofing"

    if any(x in blob for x in ["plumbing", "water", "leak", "pipe", "drain", "valve", "shut-off", "shut off", "heater", "hot water"]):
        return "Plumbing"

    if any(x in blob for x in ["exterior", "siding", "wall-covering", "wall covering", "eaves", "soffit", "fascia", "window", "door", "deck", "porch", "patio", "handrail", "railing", "grading", "vegetation", "fence"]):
        return "Exterior"

    if any(x in blob for x in ["heating", "cooling", "hvac", "thermostat", "furnace", "air conditioner"]):
        return "HVAC"

    if any(x in blob for x in ["kitchen", "bathroom", "interior", "floor", "ceiling", "wall", "range", "appliance"]):
        return "Interior"

    return "General"


def _hf_std_system(issue: dict, category: str) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_cleanup_blob(issue)
    section = _hf_std_normalize_section(issue) if "_hf_std_normalize_section" in globals() else str((issue or {}).get("section") or "").strip()

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return "Gutters & Downspouts"

    if _hf_cleanup_is_fence_issue(issue):
        return "Fence / Site Exterior"

    if "gfci" in blob:
        return "GFCI Protection"
    if "afci" in blob:
        return "AFCI Protection"
    if "meter" in blob:
        return "Electrical Service"
    if "disconnect" in blob:
        return "Main Service Disconnect"
    if "panel" in blob or "breaker" in blob or "knockout" in blob:
        return "Panelboards & Breakers"
    if "wiring" in blob:
        return "Electrical Wiring"

    if "water heater" in blob or "hot water" in blob:
        return "Water Heater / Hot Water Source"
    if "shut-off" in blob or "shut off" in blob or "valve" in blob:
        return "Water Shut-Off Valve"
    if "water supply" in blob:
        return "Water Supply"
    if "drain" in blob or "pipe" in blob:
        return "Drain / Waste / Vent"

    if "gutter" in blob or "gutters" in blob or "downspout" in blob:
        return "Gutters & Downspouts"
    if "flashing" in blob:
        return "Roof Flashing"
    if "roof" in blob:
        return "Roof Covering"

    if "window" in blob:
        return "Windows"
    if "door" in blob:
        return "Doors"
    if "deck" in blob or "porch" in blob or "patio" in blob:
        return "Deck / Porch / Patio"
    if "handrail" in blob or "railing" in blob:
        return "Railings / Guards / Handrails"
    if "soffit" in blob or "fascia" in blob or "eaves" in blob:
        return "Eaves / Soffits / Fascia"

    if "thermostat" in blob:
        return "Thermostat"
    if "heating" in blob:
        return "Heating System"
    if "cooling" in blob:
        return "Cooling System"

    return section or category or "General"


def _hf_std_component(issue: dict, system: str) -> str:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_cleanup_blob(issue)

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return "Downspout discharge / drainage termination"

    if _hf_cleanup_is_fence_issue(issue):
        return "Fence"

    if "downspout" in blob:
        return "Downspout"
    if "gutter" in blob or "gutters" in blob:
        return "Gutter"

    if "gfci" in blob:
        return "GFCI outlet or circuit"
    if "afci" in blob:
        return "AFCI circuit protection"
    if "meter base" in blob:
        return "Electric meter / meter base"
    if "meter" in blob:
        return "Electric meter"
    if "disconnect" in blob:
        return "Main service disconnect"
    if "breaker" in blob:
        return "Breaker / electrical panel"
    if "panel" in blob:
        return "Electrical panel"
    if "wiring" in blob:
        return "Electrical wiring"

    if "valve" in blob or "shut-off" in blob or "shut off" in blob:
        return "Water shut-off valve"
    if "water heater" in blob:
        return "Water heater"
    if "pipe" in blob:
        return "Pipe / support"
    if "drain" in blob:
        return "Drain line"

    if "flashing" in blob:
        return "Flashing"
    if "roof" in blob:
        return "Roof covering or roof component"

    if "window" in blob:
        return "Window"
    if "door" in blob:
        return "Door"
    if "deck" in blob:
        return "Deck component"
    if "handrail" in blob:
        return "Handrail"
    if "railing" in blob:
        return "Railing"

    return system or "Component to confirm"


def _hf_std_plain_summary(issue: dict, category: str, system: str, component: str, defect_type: str) -> str:
    title = _hf_std_normalize_title(issue) if "_hf_std_normalize_title" in globals() else str((issue or {}).get("title") or "").strip()
    location = _hf_std_normalize_section(issue) if "_hf_std_normalize_section" in globals() else str((issue or {}).get("section") or "").strip()

    category = str(category or "General").strip()
    system = str(system or category).strip()
    component = str(component or system).strip()
    defect_type = str(defect_type or "Needs review").strip()

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return "The inspection report notes that downspouts may be discharging too close to the house. This can move roof water toward the foundation instead of away from it."

    if _hf_cleanup_is_fence_issue(issue):
        return "The inspection report notes a fence or site-exterior concern that should be reviewed for repair, safety, or maintenance."

    if category == "Roofing" and ("Gutter" in system or "Downspout" in system):
        return f"The inspection report notes a gutter or downspout concern involving {component or system}."

    if category == "Electrical" and "GFCI" in system:
        return f"The inspection report notes possible missing or defective GFCI protection at {location or component}."

    if category == "Electrical" and "AFCI" in system:
        return f"The inspection report notes possible missing or defective AFCI protection at {location or component}."

    if category == "Electrical":
        return f"The inspection report notes an electrical concern involving {component or system}."

    if category == "Plumbing" and "Leak" in defect_type:
        return f"The inspection report notes an active or suspected water leak at {location or component}."

    if category == "Plumbing":
        return f"The inspection report notes a plumbing concern involving {component or system}."

    if category == "Roofing":
        return f"The inspection report notes a roof or drainage concern involving {component or system}."

    if category == "Exterior":
        return f"The inspection report notes an exterior concern involving {component or system}."

    if category == "HVAC":
        return f"The inspection report notes an HVAC concern involving {component or system}."

    if title:
        return f"The inspection report notes: {title}."

    return "The inspection report notes a finding that should be reviewed."


def _hf_std_trade(category: str, issue: dict) -> str:
    if _hf_cleanup_is_fence_issue(issue):
        return "Qualified contractor"

    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_cleanup_blob(issue)

    if category == "Electrical":
        return "Licensed electrician"
    if category == "Plumbing":
        return "Licensed plumber"
    if category == "Roofing":
        return "Qualified roofing contractor"
    if category == "HVAC":
        return "Qualified HVAC technician"

    if "window" in blob or "door" in blob or "deck" in blob or "handrail" in blob or "fence" in blob:
        return "Qualified contractor"

    return "Qualified contractor"


def _hf_std_risk_reasons(issue: dict, category: str, defect_type: str) -> list:
    blob = _hf_std_lower_blob(issue) if "_hf_std_lower_blob" in globals() else _hf_cleanup_blob(issue)

    if "_hf_harden_is_downspout_drainage" in globals() and _hf_harden_is_downspout_drainage(issue):
        return ["foundation moisture risk", "basement or crawlspace water risk", "poor roof drainage"]

    if _hf_cleanup_is_fence_issue(issue):
        return ["site safety", "property boundary maintenance", "exterior deterioration"]

    if category == "Electrical":
        if "gfci" in blob:
            return ["shock risk", "wet-area electrical safety", "code/safety review"]
        if "afci" in blob:
            return ["electrical fire risk", "arc-fault protection", "code/safety review"]
        return ["shock risk", "fire risk", "electrical service reliability"]

    if category == "Plumbing":
        if "leak" in blob:
            return ["water damage", "mold risk", "hidden deterioration"]
        return ["water service reliability", "water damage risk", "maintenance concern"]

    if category == "Roofing":
        return ["water intrusion", "moisture damage", "roof system deterioration"]

    if category == "Exterior":
        return ["moisture intrusion", "deterioration", "safety or maintenance concern"]

    if category == "HVAC":
        return ["comfort issue", "system reliability", "efficiency or safety concern"]

    return ["maintenance tracking", "repair planning", "future monitoring"]


def _hf_standard_preview_payload(record_id: str, limit: int = 10) -> dict:
    _hf_std_ensure_schema()
    issues = _hf_std_fetch_issues(record_id)

    preview = []

    for issue in issues[: max(1, min(int(limit or 10), 50))]:
        standard = _hf_std_build_standard_json(issue)

        preview.append(
            {
                "id": issue.get("id"),
                "title": issue.get("title"),
                "source_item_number": standard["source"].get("report_item"),
                "source_finding_title": standard["source"].get("inspector_title"),
                "source_finding_text": standard["source"].get("inspector_finding_text"),
                "source_recommendation": standard["source"].get("inspector_recommendation"),
                "category": standard["homefax"].get("category"),
                "system": standard["homefax"].get("system"),
                "component": standard["homefax"].get("component"),
                "defect_type": standard["homefax"].get("defect_type"),
                "plain_summary": standard["homefax"].get("plain_summary"),
                "recommended_trade": standard["homefax"].get("recommended_trade"),
                "recommended_action": standard["homefax"].get("recommended_action"),
                "monitoring_plan": standard["homefax"].get("monitoring_plan"),
                "risk_reasons": standard["homefax"].get("risk_reasons"),
                "primary_image_url": standard["evidence"].get("primary_image_url"),
                "candidate_image_count": standard["evidence"].get("candidate_image_count", 0),
                "candidate_image_urls": standard["evidence"].get("candidate_image_urls", [])[:10],
            }
        )

    return {
        "success": True,
        "record_id": record_id,
        "schema_version": _HF_STANDARD_SCHEMA_VERSION,
        "preview_version": "v2",
        "issues_previewed": len(preview),
        "issues_total": len(issues),
        "issues": preview,
    }


# NOTE:
# FastAPI keeps the first registered route when duplicate path+method handlers exist.
# So instead of redefining /homefax-standard-report-preview here, we provide a canonical
# cleanup endpoint and will test against it. In a later file refactor, remove the old
# earlier preview function and point the original route to this payload helper.
@app.get("/records/{record_id}/homefax-standard-report-preview-clean")
def homefax_standard_report_preview_clean(record_id: str, limit: int = 10):
    return _hf_standard_preview_payload(record_id, limit)


@app.get("/records/{record_id}/homefax-standard-report-preview-v3")
def homefax_standard_report_preview_v3(record_id: str, limit: int = 10):
    return _hf_standard_preview_payload(record_id, limit)


@app.post("/records/{record_id}/homefax-standard-schema/backfill-v3")
def homefax_standard_schema_backfill_v3(record_id: str):
    return _hf_std_backfill_record(record_id)



# ============================================================
# Original Inspector Finding Mapping Pass 1
#
# Purpose:
# - Fill standard source fields from ai_adapter_learning_runs.raw_result.
# - This is not full PDF paragraph extraction yet.
# - It maps structured parser source data into verified_issues:
#   source_finding_text, source_recommendation, source_page,
#   source_report_section.
# ============================================================

import json as _hf_map_json
import re as _hf_map_re


def _hf_map_safe_text(value) -> str:
    return _hf_map_re.sub(r"\s+", " ", str(value or "")).strip()


def _hf_map_parse_json(value):
    if value is None:
        return None

    if isinstance(value, (dict, list)):
        return value

    text = str(value).strip()
    if not text:
        return None

    try:
        return _hf_map_json.loads(text)
    except Exception:
        return None


def _hf_map_get_connection():
    if "_hf_report_db_connection" in globals():
        return _hf_report_db_connection()

    for name in ("get_db_connection", "get_connection", "db_connection"):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No database connection helper found.")


def _hf_map_extract_findings_from_raw_result(raw_result):
    parsed = _hf_map_parse_json(raw_result)

    if not isinstance(parsed, dict):
        return []

    findings = parsed.get("findings") or parsed.get("issues") or parsed.get("extractedIssues") or []

    if not isinstance(findings, list):
        return []

    clean = []

    for finding in findings:
        if not isinstance(finding, dict):
            continue

        source_number = _hf_map_safe_text(
            finding.get("source_number")
            or finding.get("source_item_number")
            or finding.get("report_item")
            or finding.get("item_number")
        )

        title = _hf_map_safe_text(
            finding.get("title")
            or finding.get("issueTitle")
            or finding.get("issue_title")
        )

        notes = _hf_map_safe_text(
            finding.get("notes")
            or finding.get("finding_text")
            or finding.get("summary")
            or finding.get("description")
        )

        recommendation = _hf_map_safe_text(
            finding.get("recommendation")
            or finding.get("recommended_action")
            or finding.get("action")
        )

        system = _hf_map_safe_text(finding.get("system"))
        location = _hf_map_safe_text(finding.get("location"))
        component = _hf_map_safe_text(finding.get("component"))

        source_report_section = location or system or component

        source_page = (
            finding.get("detail_page")
            or finding.get("source_page")
            or finding.get("page")
            or finding.get("summary_page")
        )

        try:
            source_page = int(source_page) if source_page is not None and str(source_page).strip() else None
        except Exception:
            source_page = None

        if not source_number:
            continue

        clean.append(
            {
                "source_number": source_number,
                "title": title,
                "notes": notes,
                "recommendation": recommendation,
                "system": system,
                "location": location,
                "component": component,
                "source_report_section": source_report_section,
                "source_page": source_page,
                "raw": finding,
            }
        )

    return clean


def _hf_map_latest_learning_run_candidates(limit: int = 10):
    conn = _hf_map_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, record_id, filename, issue_count, raw_result, extracted_schema, created_at
                FROM ai_adapter_learning_runs
                WHERE raw_result IS NOT NULL
                ORDER BY id DESC
                LIMIT %s
                """,
                (int(limit),),
            )

            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                cols = [desc[0] for desc in cursor.description]
                rows = [dict(zip(cols, row)) for row in rows]

            return rows

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_map_fetch_verified_issues_for_record(record_id: str):
    conn = _hf_map_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, record_id, title, section, summary,
                       source_item_number, source_finding_title,
                       source_finding_text, source_recommendation,
                       source_page, source_report_section
                FROM verified_issues
                WHERE record_id = %s
                ORDER BY id ASC
                """,
                (record_id,),
            )

            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                cols = [desc[0] for desc in cursor.description]
                rows = [dict(zip(cols, row)) for row in rows]

            return rows

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_map_choose_best_learning_run(record_id: str):
    verified = _hf_map_fetch_verified_issues_for_record(record_id)
    source_numbers = {
        _hf_map_safe_text(row.get("source_item_number"))
        for row in verified
        if _hf_map_safe_text(row.get("source_item_number"))
    }

    best = None
    best_score = -1

    candidates = _hf_map_latest_learning_run_candidates(limit=10)

    for run in candidates:
        findings = _hf_map_extract_findings_from_raw_result(run.get("raw_result"))
        finding_numbers = {f["source_number"] for f in findings}

        overlap = len(source_numbers.intersection(finding_numbers))

        score = overlap

        # Bonus if issue counts look close.
        try:
            if int(run.get("issue_count") or 0) == len(verified):
                score += 5
        except Exception:
            pass

        # Bonus if same record id.
        if _hf_map_safe_text(run.get("record_id")) == _hf_map_safe_text(record_id):
            score += 20

        if score > best_score:
            best_score = score
            best = {
                "run": run,
                "findings": findings,
                "overlap": overlap,
                "score": score,
                "verified_count": len(verified),
                "source_numbers_count": len(source_numbers),
            }

    return best


def _hf_map_update_verified_issues_from_learning_run(record_id: str, dry_run: bool = False):
    best = _hf_map_choose_best_learning_run(record_id)

    if not best:
        return {
            "success": False,
            "record_id": record_id,
            "error": "No ai_adapter_learning_runs.raw_result candidate found.",
        }

    findings_by_source = {
        f["source_number"]: f
        for f in best["findings"]
        if f.get("source_number")
    }

    verified = _hf_map_fetch_verified_issues_for_record(record_id)

    updates = []
    unmatched = []

    for issue in verified:
        source_item = _hf_map_safe_text(issue.get("source_item_number"))

        match = findings_by_source.get(source_item)

        if not match:
            unmatched.append(
                {
                    "id": issue.get("id"),
                    "source_item_number": source_item,
                    "title": issue.get("title"),
                }
            )
            continue

        source_finding_text = match.get("notes") or issue.get("source_finding_text") or ""
        source_recommendation = match.get("recommendation") or issue.get("source_recommendation") or ""
        source_page = match.get("source_page") or issue.get("source_page")
        source_report_section = match.get("source_report_section") or issue.get("source_report_section") or issue.get("section") or ""

        updates.append(
            {
                "id": issue.get("id"),
                "source_item_number": source_item,
                "source_finding_title": issue.get("source_finding_title") or match.get("title"),
                "source_finding_text": source_finding_text,
                "source_recommendation": source_recommendation,
                "source_page": source_page,
                "source_report_section": source_report_section,
                "matched_title": match.get("title"),
            }
        )

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "record_id": record_id,
            "learning_run": {
                "id": best["run"].get("id"),
                "record_id": best["run"].get("record_id"),
                "filename": best["run"].get("filename"),
                "issue_count": best["run"].get("issue_count"),
                "overlap": best["overlap"],
                "score": best["score"],
            },
            "verified_count": len(verified),
            "updates_count": len(updates),
            "unmatched_count": len(unmatched),
            "sample_updates": updates[:10],
            "sample_unmatched": unmatched[:10],
        }

    conn = _hf_map_get_connection()

    try:
        with conn.cursor() as cursor:
            for update in updates:
                cursor.execute(
                    """
                    UPDATE verified_issues
                    SET
                        source_finding_text = %s,
                        source_recommendation = %s,
                        source_page = %s,
                        source_report_section = %s
                    WHERE id = %s
                    """,
                    (
                        update["source_finding_text"],
                        update["source_recommendation"],
                        update["source_page"],
                        update["source_report_section"],
                        update["id"],
                    ),
                )

        conn.commit()

    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Re-run HomeFax standard backfill so homefax_standard_json reflects mapped source fields.
    backfill_result = None
    if "_hf_std_backfill_record" in globals():
        try:
            backfill_result = _hf_std_backfill_record(record_id)
        except Exception as error:
            backfill_result = {
                "success": False,
                "error": str(error),
            }

    return {
        "success": True,
        "dry_run": False,
        "record_id": record_id,
        "learning_run": {
            "id": best["run"].get("id"),
            "record_id": best["run"].get("record_id"),
            "filename": best["run"].get("filename"),
            "issue_count": best["run"].get("issue_count"),
            "overlap": best["overlap"],
            "score": best["score"],
        },
        "verified_count": len(verified),
        "updates_count": len(updates),
        "unmatched_count": len(unmatched),
        "sample_updates": updates[:10],
        "sample_unmatched": unmatched[:10],
        "standard_backfill": backfill_result,
    }


@app.get("/records/{record_id}/original-finding-mapping-preview")
def original_finding_mapping_preview(record_id: str):
    return _hf_map_update_verified_issues_from_learning_run(record_id, dry_run=True)


@app.post("/records/{record_id}/original-finding-mapping/backfill")
def original_finding_mapping_backfill(record_id: str):
    return _hf_map_update_verified_issues_from_learning_run(record_id, dry_run=False)



# ============================================================
# Original Inspector Finding PDF Extraction Pass 1
#
# Purpose:
# - Extract actual nearby source text from the stored original PDF.
# - Uses verified_issues.source_page, source_item_number, and title.
# - This improves source_finding_text beyond parser-generated summaries.
#
# Notes:
# - This is text-layer extraction only.
# - If the PDF page is image-only, OCR will be a later pass.
# ============================================================

import os as _hf_pdf_os
import re as _hf_pdf_re
import json as _hf_pdf_json
from pathlib import Path as _hf_pdf_Path


def hf_pdf_safe_text(value) -> str:
    return _hf_pdf_re.sub(r"\s+", " ", str(value or "")).strip()


def _hf_pdf_normalize_for_match(value) -> str:
    text = str(value or "").lower()
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = _hf_pdf_re.sub(r"[^a-z0-9]+", " ", text)
    return _hf_pdf_re.sub(r"\s+", " ", text).strip()


def _hf_pdf_get_connection():
    if "_hf_report_db_connection" in globals():
        return _hf_report_db_connection()

    for name in ("get_db_connection", "get_connection", "db_connection"):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No database connection helper found.")


def _hf_pdf_fetch_verified_issues(record_id: str):
    conn = _hf_pdf_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, record_id, title, section, summary,
                       source_item_number, source_report_section,
                       source_finding_title, source_finding_text,
                       source_recommendation, source_page,
                       report_pdf_url, original_report_path
                FROM verified_issues
                WHERE record_id = %s
                ORDER BY id ASC
                """,
                (record_id,),
            )

            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                cols = [desc[0] for desc in cursor.description]
                rows = [dict(zip(cols, row)) for row in rows]

            return rows

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_pdf_find_original_pdf_path(record_id: str):
    """
    Find the stored original report PDF.

    Search order:
    1. verified_issues.original_report_path
    2. existing helper functions, if present
    3. common local original_reports directory patterns
    4. common repo/output locations
    """

    # 1. Try DB-stored original_report_path.
    try:
        issues = _hf_pdf_fetch_verified_issues(record_id)
        for issue in issues:
            raw_path = issue.get("original_report_path")
            if raw_path:
                candidate = _hf_pdf_Path(str(raw_path)).expanduser()
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
    except Exception:
        pass

    # 2. Try previous helpers if they exist.
    for helper_name in [
        "_hf_report_find_existing_pdf",
        "_hf_report_pdf_path",
        "_hf_original_report_path",
        "find_original_report_path",
    ]:
        helper = globals().get(helper_name)
        if callable(helper):
            try:
                candidate = helper(record_id)
                if candidate:
                    path = _hf_pdf_Path(str(candidate)).expanduser()
                    if path.exists() and path.is_file():
                        return str(path)
            except Exception:
                pass

    base = _hf_pdf_Path(".").resolve()

    candidates = [
        base / "original_reports" / f"{record_id}.pdf",
        base / "original_reports" / f"{record_id}.PDF",
        base / "reports" / f"{record_id}.pdf",
        base / "uploads" / f"{record_id}.pdf",
        base / "output" / f"{record_id}.pdf",
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    # 3. Glob common places.
    search_dirs = [
        base / "original_reports",
        base / "reports",
        base / "uploads",
        base / "output",
        base,
    ]

    record_slug = _hf_pdf_normalize_for_match(record_id)

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        try:
            for candidate in search_dir.glob("*.pdf"):
                candidate_slug = _hf_pdf_normalize_for_match(candidate.name)
                if record_slug in candidate_slug or "6039" in candidate_slug or "carpenter" in candidate_slug:
                    return str(candidate)
        except Exception:
            pass

    return None


def _hf_pdf_extract_text_pages(pdf_path: str):
    """
    Returns a list of:
      {page: 1-based page number, text: raw text}

    Uses PyMuPDF first, then pypdf fallback.
    """

    path = _hf_pdf_Path(pdf_path)

    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Preferred: PyMuPDF / fitz
    try:
        import fitz

        doc = fitz.open(str(path))
        pages = []

        try:
            for index in range(len(doc)):
                page = doc[index]
                text = page.get_text("text") or ""
                pages.append(
                    {
                        "page": index + 1,
                        "text": text,
                    }
                )
        finally:
            doc.close()

        return pages

    except Exception as fitz_error:
        fitz_error_text = str(fitz_error)

    # Fallback: pypdf
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = []

        for index, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""

            pages.append(
                {
                    "page": index + 1,
                    "text": text,
                }
            )

        return pages

    except Exception as pypdf_error:
        raise RuntimeError(
            "Could not extract PDF text with PyMuPDF or pypdf. "
            f"PyMuPDF error: {fitz_error_text}; pypdf error: {pypdf_error}"
        )


def _hf_pdf_item_regex(item_number: str):
    """
    Create forgiving regex for item numbers like 2.4.5.
    Handles spaces around dots.
    """

    item = _hf_pdf_safe_text(item_number)

    if not item:
        return None

    parts = [_hf_pdf_re.escape(p) for p in item.split(".") if p]

    if not parts:
        return None

    pattern = r"(?<!\d)" + r"\s*\.\s*".join(parts) + r"(?!\d)"
    return _hf_pdf_re.compile(pattern, _hf_pdf_re.IGNORECASE)


def _hf_pdf_next_item_regex():
    return _hf_pdf_re.compile(r"(?<!\d)(\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2})(?!\d)")


def _hf_pdf_clean_chunk(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\x00", " ")
    text = text.replace("\r", "\n")
    text = _hf_pdf_re.sub(r"[ \t]+", " ", text)
    text = _hf_pdf_re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    # Remove excessive page footer/header noise patterns without being destructive.
    lines = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue

        low = clean.lower()

        if low in {"inspection report", "home inspection report"}:
            continue

        if "big ben inspections" in low and len(clean) < 80:
            continue

        if _hf_pdf_re.fullmatch(r"page\s+\d+(\s+of\s+\d+)?", low):
            continue

        lines.append(clean)

    text = "\n".join(lines)
    text = _hf_pdf_re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _hf_pdf_sentence_recommendation(chunk: str) -> str:
    if not chunk:
        return ""

    # Split into rough sentences/lines.
    pieces = _hf_pdf_re.split(r"(?<=[.!?])\s+|\n+", chunk)

    keywords = [
        "recommend",
        "repair",
        "replace",
        "correct",
        "evaluate",
        "further evaluation",
        "qualified",
        "contractor",
        "electrician",
        "plumber",
        "roofer",
        "monitor",
    ]

    selected = []

    for piece in pieces:
        clean = _hf_pdf_safe_text(piece)
        low = clean.lower()

        if clean and any(keyword in low for keyword in keywords):
            selected.append(clean)

    # Keep it short.
    if selected:
        return " ".join(selected[:3])[:1200].strip()

    return ""


def _hf_pdf_find_chunk_for_issue(issue: dict, pages: list):
    item_number = _hf_pdf_safe_text(issue.get("source_item_number"))
    title = _hf_pdf_safe_text(issue.get("source_finding_title") or issue.get("title"))
    source_page = issue.get("source_page")

    page_map = {p["page"]: p.get("text") or "" for p in pages}

    candidate_page_numbers = []

    try:
        source_page_int = int(source_page)
    except Exception:
        source_page_int = None

    if source_page_int:
        candidate_page_numbers.extend(
            [
                source_page_int,
                source_page_int - 1,
                source_page_int + 1,
            ]
        )

    # Add all pages as fallback after preferred pages.
    candidate_page_numbers.extend([p["page"] for p in pages])

    # Dedupe while preserving order.
    seen = set()
    candidate_page_numbers = [
        p for p in candidate_page_numbers
        if p and p not in seen and not seen.add(p) and p in page_map
    ]

    item_re = _hf_pdf_item_regex(item_number)
    title_norm = _hf_pdf_normalize_for_match(title)

    best_result = None

    for page_number in candidate_page_numbers:
        text = page_map.get(page_number) or ""

        if not text.strip():
            continue

        start = None
        matched_by = None

        # First try item number.
        if item_re:
            match = item_re.search(text)
            if match:
                start = match.start()
                matched_by = "item_number"

        # Then try title.
        if start is None and title_norm:
            norm_text = _hf_pdf_normalize_for_match(text)
            idx_norm = norm_text.find(title_norm)

            if idx_norm != -1:
                # Approximate raw start by scanning title words.
                title_words = [w for w in title.split() if len(w) >= 3]
                for word in title_words[:3]:
                    m = _hf_pdf_re.search(_hf_pdf_re.escape(word), text, _hf_pdf_re.IGNORECASE)
                    if m:
                        start = m.start()
                        matched_by = "title"
                        break

        if start is None:
            continue

        # End at next item number after start, or at a reasonable character window.
        after_start = text[start + 5 :]
        next_match = _hf_pdf_next_item_regex().search(after_start)

        if next_match:
            end = start + 5 + next_match.start()
        else:
            end = min(len(text), start + 2500)

        # Avoid tiny chunks.
        if end <= start + 80:
            end = min(len(text), start + 2500)

        chunk = _hf_pdf_clean_chunk(text[start:end])

        if not chunk:
            continue

        result = {
            "matched": True,
            "matched_by": matched_by,
            "source_page": page_number,
            "item_number": item_number,
            "title": title,
            "source_finding_text": chunk[:3000],
            "source_recommendation": _hf_pdf_sentence_recommendation(chunk),
            "text_length": len(chunk),
        }

        # Prefer exact source_page + item number.
        if page_number == source_page_int and matched_by == "item_number":
            return result

        # Otherwise keep first usable result.
        if not best_result:
            best_result = result

    if best_result:
        return best_result

    return {
        "matched": False,
        "matched_by": None,
        "source_page": source_page_int,
        "item_number": item_number,
        "title": title,
        "source_finding_text": "",
        "source_recommendation": "",
        "text_length": 0,
    }


def _hf_pdf_extraction_run(record_id: str, dry_run: bool = True, limit: int = 0):
    pdf_path = _hf_pdf_find_original_pdf_path(record_id)

    if not pdf_path:
        return {
            "success": False,
            "record_id": record_id,
            "error": "Original PDF file not found on local backend. Register or store the original report first.",
            "searched_record_id": record_id,
        }

    issues = _hf_pdf_fetch_verified_issues(record_id)

    if limit and int(limit) > 0:
        issues = issues[: int(limit)]

    pages = _hf_pdf_extract_text_pages(pdf_path)

    page_text_count = sum(1 for p in pages if (p.get("text") or "").strip())

    results = []
    updates = []
    unmatched = []

    for issue in issues:
        extracted = _hf_pdf_find_chunk_for_issue(issue, pages)

        row = {
            "id": issue.get("id"),
            "source_item_number": issue.get("source_item_number"),
            "title": issue.get("source_finding_title") or issue.get("title"),
            "old_source_page": issue.get("source_page"),
            "matched": extracted.get("matched"),
            "matched_by": extracted.get("matched_by"),
            "source_page": extracted.get("source_page") or issue.get("source_page"),
            "source_finding_text": extracted.get("source_finding_text") or "",
            "source_recommendation": extracted.get("source_recommendation") or "",
            "text_length": extracted.get("text_length") or 0,
        }

        results.append(row)

        if row["matched"] and row["source_finding_text"]:
            updates.append(row)
        else:
            unmatched.append(row)

    if not dry_run and updates:
        conn = _hf_pdf_get_connection()

        try:
            with conn.cursor() as cursor:
                for update in updates:
                    # Only overwrite with PDF text when it is meaningfully longer than parser summary.
                    cursor.execute(
                        """
                        UPDATE verified_issues
                        SET
                            source_finding_text = %s,
                            source_recommendation = CASE
                                WHEN %s != '' THEN %s
                                ELSE source_recommendation
                            END,
                            source_page = %s
                        WHERE id = %s
                        """,
                        (
                            update["source_finding_text"],
                            update["source_recommendation"],
                            update["source_recommendation"],
                            update["source_page"],
                            update["id"],
                        ),
                    )

            conn.commit()

        finally:
            try:
                conn.close()
            except Exception:
                pass

    standard_backfill = None

    if not dry_run and "_hf_std_backfill_record" in globals():
        try:
            standard_backfill = _hf_std_backfill_record(record_id)
        except Exception as error:
            standard_backfill = {
                "success": False,
                "error": str(error),
            }

    return {
        "success": True,
        "dry_run": dry_run,
        "record_id": record_id,
        "pdf_path": pdf_path,
        "pages_total": len(pages),
        "pages_with_text": page_text_count,
        "issues_checked": len(issues),
        "matched_count": len(updates),
        "unmatched_count": len(unmatched),
        "sample_results": results[:10],
        "sample_unmatched": unmatched[:10],
        "standard_backfill": standard_backfill,
    }


@app.get("/records/{record_id}/original-finding-pdf-extraction-preview")
def original_finding_pdf_extraction_preview(record_id: str, limit: int = 10):
    return _hf_pdf_extraction_run(record_id, dry_run=True, limit=limit)


@app.post("/records/{record_id}/original-finding-pdf-extraction/backfill")
def original_finding_pdf_extraction_backfill(record_id: str):
    return _hf_pdf_extraction_run(record_id, dry_run=False, limit=0)



# ============================================================
# Original Inspector Finding PDF Extraction Hotfix 1
#
# Fix:
# - Restores missing _hf_pdf_safe_text helper if the PDF extraction
#   patch was partially appended or helper was not available at runtime.
# ============================================================

import re as _hf_pdf_re_hotfix


def _hf_pdf_safe_text(value) -> str:
    """
    Safe string normalizer used by Original Inspector Finding PDF Extraction.
    Defined here as a hotfix so existing PDF extraction functions can resolve it
    at runtime.
    """
    return _hf_pdf_re_hotfix.sub(r"\s+", " ", str(value or "")).strip()


# Defensive fallback in case another helper was skipped during append.
def _hf_pdf_normalize_for_match(value) -> str:
    text = str(value or "").lower()
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = _hf_pdf_re_hotfix.sub(r"[^a-z0-9]+", " ", text)
    return _hf_pdf_re_hotfix.sub(r"\s+", " ", text).strip()



# ============================================================
# PDF Extraction Cleanup Pass 2
#
# Purpose:
# - Clean source_finding_text and source_recommendation after PDF extraction.
# - Fix common PDF text encoding artifacts.
# - Remove repeated footer/header noise.
# - Improve recommendation extraction.
# ============================================================

import re as _hf_pdf_clean_re


def _hf_pdf_clean2_safe_text(value) -> str:
    return str(value or "").replace("\r", "\n").replace("\x00", " ").strip()


def _hf_pdf_clean2_fix_encoding(text: str) -> str:
    text = _hf_pdf_clean2_safe_text(text)

    replacements = {
        "\u00a0": " ",
        "qualiíed": "qualified",
        "Qualiíed": "Qualified",
        "QUALIÍED": "QUALIFIED",
        "rooíng": "roofing",
        "Rooíng": "Roofing",
        "ROOÍNG": "ROOFING",
        "soíit": "soffit",
        "Soíit": "Soffit",
        "íxture": "fixture",
        "Íxture": "Fixture",
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    # Common PDF-ligature artifacts.
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")

    # Normalize spacing but preserve line structure.
    lines = []
    for line in text.splitlines():
        line = _hf_pdf_clean_re.sub(r"[ \t]+", " ", line).strip()
        lines.append(line)

    text = "\n".join(lines)
    text = _hf_pdf_clean_re.sub(r"\n{3,}", "\n\n", text).strip()

    return text


def _hf_pdf_clean2_is_noise_line(line: str) -> bool:
    clean = _hf_pdf_clean_re.sub(r"\s+", " ", str(line or "")).strip()
    low = clean.lower()

    if not clean:
        return True

    footer_bits = [
        "6039 s carpenter st",
        "vasintino johnson",
        "lateef home inspection services",
        "big ben inspections",
    ]

    if any(bit in low for bit in footer_bits):
        return True

    if _hf_pdf_clean_re.fullmatch(r"page\s+\d+(\s+of\s+\d+)?", low):
        return True

    if _hf_pdf_clean_re.fullmatch(r"(major|minor|material|safety|maintenance)\s+defect", low):
        return True

    return False


def _hf_pdf_clean2_clean_source_text(text: str) -> str:
    text = _hf_pdf_clean2_fix_encoding(text)

    cleaned_lines = []

    for line in text.splitlines():
        if _hf_pdf_clean2_is_noise_line(line):
            continue

        cleaned_lines.append(line.strip())

    text = "\n".join(cleaned_lines)
    text = _hf_pdf_clean_re.sub(r"\n{3,}", "\n\n", text).strip()

    return text


def _hf_pdf_clean2_sentence_split(text: str):
    text = _hf_pdf_clean2_fix_encoding(text)
    flat = _hf_pdf_clean_re.sub(r"\s+", " ", text).strip()

    if not flat:
        return []

    pieces = _hf_pdf_clean_re.split(r"(?<=[.!?])\s+", flat)

    return [
        piece.strip()
        for piece in pieces
        if piece and piece.strip()
    ]


def _hf_pdf_clean2_unique_join(parts):
    seen = set()
    clean_parts = []

    for part in parts:
        clean = _hf_pdf_clean_re.sub(r"\s+", " ", str(part or "")).strip()

        if not clean:
            continue

        low = clean.lower()

        if low == "recommendation":
            continue

        if _hf_pdf_clean2_is_noise_line(clean):
            continue

        if low in seen:
            continue

        seen.add(low)
        clean_parts.append(clean)

    return " ".join(clean_parts).strip()


def _hf_pdf_clean2_extract_marker_recommendation(clean_source_text: str):
    lines = [
        line.strip()
        for line in _hf_pdf_clean2_fix_encoding(clean_source_text).splitlines()
        if line.strip()
    ]

    collected = []

    for idx, line in enumerate(lines):
        if line.strip().lower() == "recommendation":
            for follow in lines[idx + 1 : idx + 5]:
                follow_clean = follow.strip()
                follow_low = follow_clean.lower()

                if not follow_clean:
                    continue

                if _hf_pdf_clean2_is_noise_line(follow_clean):
                    break

                if _hf_pdf_clean_re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{1,2}.*", follow_clean):
                    break

                if follow_low in {"recommendation"}:
                    continue

                collected.append(follow_clean)

            break

    return _hf_pdf_clean2_unique_join(collected)


def _hf_pdf_clean2_extract_body_recommendation(clean_source_text: str):
    keywords = [
        "recommend",
        "recommended",
        "correction",
        "further evaluation",
        "contact a qualified",
        "qualified",
        "repair",
        "replace",
        "evaluate",
        "contractor",
        "professional",
        "roofer",
        "plumber",
        "electrician",
        "diy project",
    ]

    selected = []

    for sentence in _hf_pdf_clean2_sentence_split(clean_source_text):
        low = sentence.lower()

        if any(keyword in low for keyword in keywords):
            selected.append(sentence)

    return _hf_pdf_clean2_unique_join(selected[:3])


def _hf_pdf_clean2_clean_recommendation(source_text: str, existing_recommendation: str = "") -> str:
    source_text = _hf_pdf_clean2_clean_source_text(source_text)
    existing_recommendation = _hf_pdf_clean2_fix_encoding(existing_recommendation)

    marker_rec = _hf_pdf_clean2_extract_marker_recommendation(source_text)
    body_rec = _hf_pdf_clean2_extract_body_recommendation(source_text)

    parts = []

    # Body recommendation often contains the actual reason/action.
    if body_rec:
        parts.append(body_rec)

    # Marker recommendation often contains the trade or DIY instruction.
    if marker_rec:
        parts.append(marker_rec)

    if not parts and existing_recommendation:
        parts.append(existing_recommendation)

    recommendation = _hf_pdf_clean2_unique_join(parts)

    # Remove broken trailing fragments created by earlier extraction.
    recommendation = recommendation.replace(" from Recommendation", "")
    recommendation = recommendation.replace(" Recommendation", "")
    recommendation = _hf_pdf_clean_re.sub(r"\s+", " ", recommendation).strip()

    return recommendation[:1200]


def _hf_pdf_clean2_get_connection():
    if "_hf_report_db_connection" in globals():
        return _hf_report_db_connection()

    for name in ("get_db_connection", "get_connection", "db_connection"):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No database connection helper found.")


def _hf_pdf_clean2_fetch_issues(record_id: str):
    conn = _hf_pdf_clean2_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, record_id, title, source_item_number,
                       source_finding_title, source_finding_text,
                       source_recommendation, source_page
                FROM verified_issues
                WHERE record_id = %s
                ORDER BY id ASC
                """,
                (record_id,),
            )

            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                cols = [desc[0] for desc in cursor.description]
                rows = [dict(zip(cols, row)) for row in rows]

            return rows

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_pdf_clean2_run(record_id: str, dry_run: bool = True, limit: int = 0):
    issues = _hf_pdf_clean2_fetch_issues(record_id)

    if limit and int(limit) > 0:
        issues = issues[: int(limit)]

    results = []
    updates = []

    for issue in issues:
        old_text = issue.get("source_finding_text") or ""
        old_rec = issue.get("source_recommendation") or ""

        new_text = _hf_pdf_clean2_clean_source_text(old_text)
        new_rec = _hf_pdf_clean2_clean_recommendation(new_text, old_rec)

        changed = (new_text != old_text) or (new_rec != old_rec)

        row = {
            "id": issue.get("id"),
            "source_item_number": issue.get("source_item_number"),
            "title": issue.get("source_finding_title") or issue.get("title"),
            "source_page": issue.get("source_page"),
            "changed": changed,
            "old_text_preview": old_text[:350],
            "new_text_preview": new_text[:500],
            "old_recommendation": old_rec,
            "new_recommendation": new_rec,
        }

        results.append(row)

        if changed:
            updates.append(
                {
                    "id": issue.get("id"),
                    "source_finding_text": new_text,
                    "source_recommendation": new_rec,
                }
            )

    if not dry_run and updates:
        conn = _hf_pdf_clean2_get_connection()

        try:
            with conn.cursor() as cursor:
                for update in updates:
                    cursor.execute(
                        """
                        UPDATE verified_issues
                        SET source_finding_text = %s,
                            source_recommendation = %s
                        WHERE id = %s
                        """,
                        (
                            update["source_finding_text"],
                            update["source_recommendation"],
                            update["id"],
                        ),
                    )

            conn.commit()

        finally:
            try:
                conn.close()
            except Exception:
                pass

    standard_backfill = None

    if not dry_run and "_hf_std_backfill_record" in globals():
        try:
            standard_backfill = _hf_std_backfill_record(record_id)
        except Exception as error:
            standard_backfill = {
                "success": False,
                "error": str(error),
            }

    return {
        "success": True,
        "dry_run": dry_run,
        "record_id": record_id,
        "issues_checked": len(issues),
        "changed_count": len(updates),
        "sample_results": results[:10],
        "standard_backfill": standard_backfill,
    }


@app.get("/records/{record_id}/pdf-extraction-cleanup-preview")
def pdf_extraction_cleanup_preview(record_id: str, limit: int = 10):
    return _hf_pdf_clean2_run(record_id, dry_run=True, limit=limit)


@app.post("/records/{record_id}/pdf-extraction-cleanup/backfill")
def pdf_extraction_cleanup_backfill(record_id: str):
    return _hf_pdf_clean2_run(record_id, dry_run=False, limit=0)



# ============================================================
# PDF Extraction Cleanup Pass 2B - Safe Cleanup Guard
#
# Purpose:
# - Fix encoding artifacts safely.
# - Improve recommendations.
# - Never overwrite source_finding_text with blank/too-short cleanup.
# - Preserve inspector source text if cleanup is too destructive.
# ============================================================

import re as _hf_pdf_clean2b_re


def _hf_pdf_clean2b_text(value) -> str:
    return str(value or "").replace("\r", "\n").replace("\x00", " ").strip()


def _hf_pdf_clean2b_fix_encoding(text: str) -> str:
    text = _hf_pdf_clean2b_text(text)

    replacements = {
        "\u00a0": " ",
        "qualiíed": "qualified",
        "Qualiíed": "Qualified",
        "QUALIÍED": "QUALIFIED",
        "rooíng": "roofing",
        "Rooíng": "Roofing",
        "ROOÍNG": "ROOFING",
        "Shut-Oì": "Shut-Off",
        "shut-oì": "shut-off",
        "Oì": "Off",
        "oì": "off",
        "íxture": "fixture",
        "Íxture": "Fixture",
        "soíit": "soffit",
        "Soíit": "Soffit",
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")

    lines = []
    for line in text.splitlines():
        line = _hf_pdf_clean2b_re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)

    return "\n".join(lines).strip()


def _hf_pdf_clean2b_is_footer_noise(line: str) -> bool:
    clean = _hf_pdf_clean2b_re.sub(r"\s+", " ", str(line or "")).strip()
    low = clean.lower()

    if not clean:
        return True

    footer_bits = [
        "6039 s carpenter st",
        "vasintino johnson",
        "lateef home inspection services",
        "big ben inspections",
    ]

    if any(bit in low for bit in footer_bits):
        return True

    if _hf_pdf_clean2b_re.fullmatch(r"page\s+\d+(\s+of\s+\d+)?", low):
        return True

    return False


def _hf_pdf_clean2b_clean_source_text(old_text: str) -> str:
    """
    Conservative cleanup:
    - fixes encoding
    - removes footer/address/name/company lines
    - DOES NOT remove defect labels like Major Defect, because sometimes
      those are the only remaining severity/source context.
    - never returns blank if old text had meaningful content
    """

    fixed = _hf_pdf_clean2b_fix_encoding(old_text)

    if not fixed:
        return ""

    kept = []

    for line in fixed.splitlines():
        if _hf_pdf_clean2b_is_footer_noise(line):
            continue
        kept.append(line.strip())

    cleaned = "\n".join([line for line in kept if line]).strip()

    # Safety guard: never wipe out text.
    old_flat = _hf_pdf_clean2b_re.sub(r"\s+", " ", fixed).strip()
    new_flat = _hf_pdf_clean2b_re.sub(r"\s+", " ", cleaned).strip()

    if old_flat and (not new_flat or len(new_flat) < 40):
        return fixed

    # Safety guard: if cleanup removed too much, keep encoding-fixed original.
    if old_flat and len(new_flat) < max(40, int(len(old_flat) * 0.35)):
        return fixed

    return cleaned


def _hf_pdf_clean2b_lines(text: str):
    return [
        line.strip()
        for line in _hf_pdf_clean2b_fix_encoding(text).splitlines()
        if line.strip()
    ]


def _hf_pdf_clean2b_sentence_split(text: str):
    flat = _hf_pdf_clean2b_re.sub(r"\s+", " ", _hf_pdf_clean2b_fix_encoding(text)).strip()
    if not flat:
        return []
    return [
        p.strip()
        for p in _hf_pdf_clean2b_re.split(r"(?<=[.!?])\s+", flat)
        if p.strip()
    ]


def _hf_pdf_clean2b_unique(parts):
    seen = set()
    out = []

    for part in parts:
        clean = _hf_pdf_clean2b_re.sub(r"\s+", " ", str(part or "")).strip()
        if not clean:
            continue

        clean = clean.replace(" Recommendation", "").strip()
        clean = clean.replace("Recommendation ", "").strip()

        if _hf_pdf_clean2b_is_footer_noise(clean):
            continue

        # Remove trailing severity label from recommendation only.
        clean = _hf_pdf_clean2b_re.sub(
            r"\s+(Major|Material|Minor|Maintenance|Safety)\s+Defect\s*$",
            "",
            clean,
            flags=_hf_pdf_clean2b_re.IGNORECASE,
        ).strip()

        low = clean.lower()
        if not clean or low in seen:
            continue

        seen.add(low)
        out.append(clean)

    return " ".join(out).strip()


def _hf_pdf_clean2b_marker_recommendation(source_text: str):
    lines = _hf_pdf_clean2b_lines(source_text)

    for idx, line in enumerate(lines):
        if line.lower() == "recommendation":
            found = []

            for follow in lines[idx + 1 : idx + 5]:
                low = follow.lower()

                if _hf_pdf_clean2b_is_footer_noise(follow):
                    break

                if _hf_pdf_clean2b_re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{1,2}.*", follow):
                    break

                if low == "recommendation":
                    continue

                found.append(follow)

            return _hf_pdf_clean2b_unique(found)

    return ""


def _hf_pdf_clean2b_body_recommendation(source_text: str):
    selected = []

    keywords = [
        "recommend",
        "recommended",
        "correction",
        "further evaluation",
        "contact a qualified",
        "qualified",
        "repair",
        "replace",
        "contractor",
        "professional",
        "roofer",
        "plumber",
        "electrician",
        "diy project",
    ]

    for sentence in _hf_pdf_clean2b_sentence_split(source_text):
        low = sentence.lower()

        if any(k in low for k in keywords):
            selected.append(sentence)

    return _hf_pdf_clean2b_unique(selected[:3])


def _hf_pdf_clean2b_clean_recommendation(source_text: str, old_rec: str) -> str:
    source_text = _hf_pdf_clean2b_clean_source_text(source_text)
    old_rec = _hf_pdf_clean2b_fix_encoding(old_rec)

    marker = _hf_pdf_clean2b_marker_recommendation(source_text)
    body = _hf_pdf_clean2b_body_recommendation(source_text)

    parts = []

    if body:
        parts.append(body)

    if marker:
        parts.append(marker)

    if not parts and old_rec:
        parts.append(old_rec)

    rec = _hf_pdf_clean2b_unique(parts)

    rec = rec.replace(" from Recommendation", "")
    rec = rec.replace(" from the foundation. Recommended DIY Project", " from the foundation. Recommended DIY Project")
    rec = _hf_pdf_clean2b_re.sub(r"\s+", " ", rec).strip()

    # Safety guard: never replace a useful recommendation with blank.
    if not rec and old_rec:
        return old_rec

    return rec[:1200]


def _hf_pdf_clean2b_get_connection():
    if "_hf_report_db_connection" in globals():
        return _hf_report_db_connection()

    for name in ("get_db_connection", "get_connection", "db_connection"):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No database connection helper found.")


def _hf_pdf_clean2b_fetch_issues(record_id: str):
    conn = _hf_pdf_clean2b_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, record_id, title, source_item_number,
                       source_finding_title, source_finding_text,
                       source_recommendation, source_page
                FROM verified_issues
                WHERE record_id = %s
                ORDER BY id ASC
                """,
                (record_id,),
            )

            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                cols = [desc[0] for desc in cursor.description]
                rows = [dict(zip(cols, row)) for row in rows]

            return rows

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_pdf_clean2b_run(record_id: str, dry_run: bool = True, limit: int = 0):
    issues = _hf_pdf_clean2b_fetch_issues(record_id)

    if limit and int(limit) > 0:
        issues = issues[: int(limit)]

    results = []
    updates = []

    for issue in issues:
        old_text = issue.get("source_finding_text") or ""
        old_rec = issue.get("source_recommendation") or ""

        new_text = _hf_pdf_clean2b_clean_source_text(old_text)
        new_rec = _hf_pdf_clean2b_clean_recommendation(new_text, old_rec)

        changed = (new_text != old_text) or (new_rec != old_rec)

        row = {
            "id": issue.get("id"),
            "source_item_number": issue.get("source_item_number"),
            "title": issue.get("source_finding_title") or issue.get("title"),
            "source_page": issue.get("source_page"),
            "changed": changed,
            "old_text_preview": old_text[:350],
            "new_text_preview": new_text[:600],
            "old_recommendation": old_rec,
            "new_recommendation": new_rec,
            "old_text_length": len(old_text),
            "new_text_length": len(new_text),
        }

        results.append(row)

        if changed:
            updates.append(
                {
                    "id": issue.get("id"),
                    "source_finding_text": new_text,
                    "source_recommendation": new_rec,
                }
            )

    if not dry_run and updates:
        conn = _hf_pdf_clean2b_get_connection()

        try:
            with conn.cursor() as cursor:
                for update in updates:
                    cursor.execute(
                        """
                        UPDATE verified_issues
                        SET source_finding_text = %s,
                            source_recommendation = %s
                        WHERE id = %s
                        """,
                        (
                            update["source_finding_text"],
                            update["source_recommendation"],
                            update["id"],
                        ),
                    )

            conn.commit()

        finally:
            try:
                conn.close()
            except Exception:
                pass

    standard_backfill = None

    if not dry_run and "_hf_std_backfill_record" in globals():
        try:
            standard_backfill = _hf_std_backfill_record(record_id)
        except Exception as error:
            standard_backfill = {
                "success": False,
                "error": str(error),
            }

    return {
        "success": True,
        "dry_run": dry_run,
        "record_id": record_id,
        "issues_checked": len(issues),
        "changed_count": len(updates),
        "sample_results": results[:10],
        "standard_backfill": standard_backfill,
    }


@app.get("/records/{record_id}/pdf-extraction-cleanup-preview-v2")
def pdf_extraction_cleanup_preview_v2(record_id: str, limit: int = 10):
    return _hf_pdf_clean2b_run(record_id, dry_run=True, limit=limit)


@app.post("/records/{record_id}/pdf-extraction-cleanup/backfill-v2")
def pdf_extraction_cleanup_backfill_v2(record_id: str):
    return _hf_pdf_clean2b_run(record_id, dry_run=False, limit=0)



# ============================================================
# Clean Preview Endpoint Location Fields Patch 1
#
# Purpose:
# - Add first-class source/location fields to standard report preview.
# - Preserve existing standard finding fields.
# - Make dashboard cards source/page/location ready.
#
# New endpoint:
# GET /records/{record_id}/homefax-standard-report-preview-clean-v4
# ============================================================

import json as _hf_loc_json
import re as _hf_loc_re


def _hf_loc_safe_text(value) -> str:
    return str(value or "").replace("\r", "\n").replace("\x00", " ").strip()


def _hf_loc_one_line(value) -> str:
    return _hf_loc_re.sub(r"\s+", " ", _hf_loc_safe_text(value)).strip()


def _hf_loc_parse_json(value, fallback=None):
    if fallback is None:
        fallback = None

    if value is None:
        return fallback

    if isinstance(value, (dict, list)):
        return value

    text = str(value).strip()

    if not text:
        return fallback

    try:
        return _hf_loc_json.loads(text)
    except Exception:
        return fallback


def _hf_loc_get_connection():
    if "_hf_report_db_connection" in globals():
        return _hf_report_db_connection()

    for name in ("get_db_connection", "get_connection", "db_connection"):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No database connection helper found.")


def _hf_loc_fix_encoding(text: str) -> str:
    text = _hf_loc_safe_text(text)

    replacements = {
        "\u00a0": " ",
        "qualiíed": "qualified",
        "Qualiíed": "Qualified",
        "rooíng": "roofing",
        "Rooíng": "Roofing",
        "îashing": "flashing",
        "Îashing": "Flashing",
        "Soïts": "Soffits",
        "soïts": "soffits",
        "eïcient": "efficient",
        "ílled": "filled",
        "íller": "filler",
        "ínger": "finger",
        "Shut-Oì": "Shut-Off",
        "shut-oì": "shut-off",
        "ﬁ": "fi",
        "ﬂ": "fl",
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    return _hf_loc_one_line(text)


def _hf_loc_title_case(value: str) -> str:
    text = _hf_loc_one_line(value)

    if not text:
        return ""

    lower_words = {"of", "at", "the", "and", "in", "to", "for", "or"}

    words = []
    for idx, word in enumerate(text.split(" ")):
        if not word:
            continue

        low = word.lower()

        if idx > 0 and low in lower_words:
            words.append(low)
        else:
            words.append(low[:1].upper() + low[1:])

    return " ".join(words)


def _hf_loc_extract_location_from_text(issue: dict) -> str:
    """
    Conservative location extraction from actual inspector source text.
    This is only a fallback until source_report_section / location columns are populated.
    """

    source = _hf_loc_fix_encoding(issue.get("source_finding_text"))
    title = _hf_loc_fix_encoding(issue.get("source_finding_title") or issue.get("title"))
    text = f"{source} {title}".lower()

    known_locations = [
        "right side of the home",
        "right side of home",
        "left side of the home",
        "left side of home",
        "front porch",
        "front of home",
        "rear of home",
        "back of home",
        "basement",
        "bathroom",
        "kitchen",
        "laundry room",
        "electrical panel cover",
        "electrical panel",
        "main water shut-off valve",
        "water heater",
        "roof penetration",
        "exterior door",
        "bathroom door",
        "kitchen sink",
    ]

    for location in known_locations:
        if location in text:
            return _hf_loc_title_case(location)

    return ""


def _hf_loc_section_from_source_text(issue: dict) -> str:
    """
    Extract likely report section from the beginning of source_finding_text.

    Example:
    '2.4.5 Gutters & Downspouts DOWNSPOUTS DRAIN NEAR HOUSE...'
    -> 'Gutters & Downspouts'
    """

    item_number = _hf_loc_one_line(issue.get("source_item_number"))
    source_text = _hf_loc_one_line(issue.get("source_finding_text"))

    if not source_text:
        return ""

    if item_number and source_text.startswith(item_number):
        rest = source_text[len(item_number):].strip()
    else:
        rest = source_text

    # Known section vocabulary from this report family.
    known_sections = [
        "Roof Covering",
        "Flashing",
        "Gutters & Downspouts",
        "Other Roof Penetrations",
        "Exterior Wall-Covering Materials",
        "Eaves, Soffits, and Fascia",
        "Eaves, Soffits, and Fascia",
        "Representative Number of Windows",
        "All Exterior Doors",
        "Porches, Patios, Decks, Balconies, and Carports",
        "Railings, Guards, and Handrails",
        "Vegetation, Surface Drainage, Retaining Walls, and Grading",
        "Heating System",
        "Thermostat and Normal Operating Controls",
        "Main Water Shut-Off Valve",
        "Water Supply",
        "Hot Water Source",
        "Drain, Waste, & Vent Systems",
        "Electric Meter & Base",
        "Main Service Disconnect",
        "Electrical Wiring",
        "Panelboards & Breakers",
        "GFCIs",
        "Electrical Defects",
        "GFCI & Electric in Bathroom",
        "Heat Source in Bathroom",
        "Door",
        "Laundry Room, Electric, and Tub",
        "Kitchen Sink",
        "GFCI",
        "AFCI",
        "Range/Oven/Cooktop",
    ]

    rest_low = rest.lower()

    for section in known_sections:
        if rest_low.startswith(section.lower()):
            return section

    # Fallback: take words before the all-caps finding title begins.
    tokens = rest.split(" ")
    section_words = []

    for token in tokens[:12]:
        bare = token.strip(".,:;()[]")

        if len(section_words) >= 2 and bare.isupper() and len(bare) > 2:
            break

        section_words.append(token)

    candidate = " ".join(section_words).strip()

    return candidate[:120]


def _hf_loc_build_standard_location_area(issue: dict) -> str:
    explicit = _hf_loc_one_line(
        issue.get("standard_location_area")
        or issue.get("location")
        or issue.get("area")
        or issue.get("room")
        or issue.get("source_location")
        or issue.get("finding_location")
    )

    if explicit:
        return explicit

    text_location = _hf_loc_extract_location_from_text(issue)

    system = _hf_loc_one_line(
        issue.get("standard_system")
        or issue.get("system")
        or issue.get("source_report_section")
    )

    component = _hf_loc_one_line(
        issue.get("standard_component")
        or issue.get("component")
    )

    system_component = " / ".join([part for part in [system, component] if part])

    if text_location and system_component:
        return f"{text_location} — {system_component}"

    if text_location:
        return text_location

    if system_component:
        return system_component

    section = _hf_loc_section_from_source_text(issue)

    if section:
        return section

    return "Location not specified"


def _hf_loc_source_pdf_url(record_id: str) -> str:
    return f"/inspection-report/{record_id}"


def _hf_loc_source_pdf_page_url(record_id: str, source_page) -> str:
    base = _hf_loc_source_pdf_url(record_id)

    if source_page:
        return f"{base}#page={source_page}"

    return base


def _hf_loc_fetch_standard_issues(record_id: str, limit: int = 100):
    """
    Fetch from verified_issues directly so we can return fields missing from
    older clean preview endpoint contracts.
    """

    conn = _hf_loc_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    record_id,
                    title,
                    summary,
                    section,
                    severity,
                    risk_score,
                    risk_level,

                    source_item_number,
                    source_page,
                    source_report_section,
                    source_finding_title,
                    source_finding_text,
                    source_recommendation,

                    standard_category,
                    standard_system,
                    standard_component,
                    standard_defect_type,
                    standard_location_area,
                    standard_severity,
                    standard_risk_reasons,
                    standard_plain_summary,
                    standard_recommended_trade,
                    standard_recommended_action,
                    standard_monitoring_plan,
                    homefax_standard_json,

                    image_url,
                    verified_image_url,
                    candidate_image_urls,

                    status,
                    homeowner_decision,
                    homeowner_note,
                    homeowner_reviewed_at,
                    current_status,
                    hidden_from_review_queue
                FROM verified_issues
                WHERE record_id = %s
                ORDER BY
                    CASE
                        WHEN source_item_number REGEXP '^[0-9]+\\\\.[0-9]+\\\\.[0-9]+'
                        THEN CAST(SUBSTRING_INDEX(source_item_number, '.', 1) AS UNSIGNED)
                        ELSE 999
                    END,
                    CASE
                        WHEN source_item_number REGEXP '^[0-9]+\\\\.[0-9]+\\\\.[0-9]+'
                        THEN CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(source_item_number, '.', 2), '.', -1) AS UNSIGNED)
                        ELSE 999
                    END,
                    CASE
                        WHEN source_item_number REGEXP '^[0-9]+\\\\.[0-9]+\\\\.[0-9]+'
                        THEN CAST(SUBSTRING_INDEX(source_item_number, '.', -1) AS UNSIGNED)
                        ELSE 999
                    END,
                    id ASC
                LIMIT %s
                """,
                (record_id, int(limit or 100)),
            )

            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in rows]

            return rows

    finally:
        try:
            conn.close()
        except Exception:
            pass



def _hf_loc_parse_source_item_number_from_summary(summary: str) -> str:
    text = _hf_loc_one_line(summary)

    match = _hf_loc_re.search(r"Report item\s+([0-9]+(?:\.[0-9]+)+)", text, _hf_loc_re.IGNORECASE)
    if match:
        return _hf_loc_one_line(match.group(1))

    match = _hf_loc_re.search(r"\b([0-9]+(?:\.[0-9]+)+)\b", text)
    if match:
        return _hf_loc_one_line(match.group(1))

    return ""


def _hf_loc_parse_labeled_summary_value(summary: str, label: str) -> str:
    text = _hf_loc_one_line(summary)

    if not text:
        return ""

    # Examples:
    # System: Electrical - GFCIs — Component: Electrical - GFCIs — Review...
    pattern = rf"{_hf_loc_re.escape(label)}:\s*(.*?)(?:\s+—\s+[A-Z][A-Za-z ]+:|\s+—\s+Review|$)"
    match = _hf_loc_re.search(pattern, text)

    if match:
        return _hf_loc_fix_encoding(match.group(1))

    return ""


def _hf_loc_split_system_component(section: str) -> tuple[str, str]:
    text = _hf_loc_fix_encoding(section)

    if " - " in text:
        system, component = text.split(" - ", 1)
        return _hf_loc_one_line(system), _hf_loc_one_line(component)

    if " – " in text:
        system, component = text.split(" – ", 1)
        return _hf_loc_one_line(system), _hf_loc_one_line(component)

    if text:
        return text, text

    return "", ""


def _hf_loc_infer_trade(system: str, component: str, title: str) -> str:
    combined = f"{system} {component} {title}".lower()

    if any(word in combined for word in ["electrical", "gfci", "afci", "breaker", "wiring", "receptacle", "meter", "disconnect"]):
        return "Licensed electrician"

    if any(word in combined for word in ["plumbing", "water", "sink", "pipe", "drain", "valve", "waste", "hot water"]):
        return "Licensed plumber"

    if any(word in combined for word in ["roof", "flashing", "gutter", "downspout", "shingle", "covering"]):
        return "Qualified roofing contractor"

    if any(word in combined for word in ["heating", "cooling", "hvac", "thermostat", "furnace", "heat source"]):
        return "Licensed HVAC contractor"

    if any(word in combined for word in ["window", "door", "wall", "siding", "exterior", "eaves", "fascia", "soffit"]):
        return "Qualified exterior contractor"

    if any(word in combined for word in ["deck", "porch", "handrail", "railing", "guard", "structural", "ledger"]):
        return "Qualified structural contractor"

    return "Qualified professional"


def _hf_loc_build_plain_summary(title: str, system: str, component: str, severity: str) -> str:
    clean_title = _hf_loc_fix_encoding(title)
    clean_system = _hf_loc_fix_encoding(system)
    clean_component = _hf_loc_fix_encoding(component)
    clean_severity = _hf_loc_fix_encoding(severity).lower()

    parts = []

    if clean_title:
        parts.append(f"This finding reports: {clean_title}.")

    area_parts = []
    if clean_system:
        area_parts.append(clean_system)

    if clean_component and clean_component.lower() != clean_system.lower():
        area_parts.append(clean_component)

    area = " - ".join(area_parts)

    if area:
        parts.append(f"It is related to {area}.")

    if clean_severity:
        parts.append(f"The current severity is marked as {clean_severity}.")

    parts.append("Review the inspector finding, confirm the supporting photo, and decide whether this should be repaired, monitored, or dismissed.")

    return _hf_loc_one_line(" ".join(parts))


def _hf_loc_build_monitoring_plan(title: str, system: str, component: str) -> str:
    combined = f"{title} {system} {component}".lower()

    if any(word in combined for word in ["gfci", "afci", "breaker", "wiring", "electrical", "receptacle", "meter", "disconnect"]):
        return "Monitor for tripped devices, non-working outlets, exposed wiring, missing covers, nuisance trips, or safety changes until corrected by a qualified electrician."

    if any(word in combined for word in ["water", "leak", "pipe", "sink", "drain", "valve", "plumbing", "waste"]):
        return "Monitor for active leaks, staining, moisture, corrosion, water pressure changes, slow drainage, odors, or recurring dampness."

    if any(word in combined for word in ["roof", "flashing", "gutter", "downspout", "covering"]):
        return "Monitor during and after rainfall for water entry, loose materials, overflow, ponding, staining, or worsening exterior drainage."

    if any(word in combined for word in ["window", "door", "wall", "siding", "exterior", "eaves", "fascia", "soffit"]):
        return "Monitor for water entry, drafts, rot, loose materials, cracking, staining, or worsening exterior damage."

    if any(word in combined for word in ["heating", "cooling", "hvac", "thermostat", "furnace", "heat source"]):
        return "Monitor for unusual noise, poor heating or cooling performance, short cycling, odors, rust, leaks, or comfort issues."

    if any(word in combined for word in ["deck", "porch", "handrail", "railing", "guard", "structural", "ledger"]):
        return "Monitor for movement, loose components, deterioration, unsafe guard/handrail conditions, water damage, or worsening structural concerns."

    return "Monitor for worsening conditions, completed repairs, moisture issues, safety changes, or recurrence."


def _hf_loc_extract_recommendation_from_summary(summary: str) -> str:
    text = _hf_loc_fix_encoding(summary)

    if "Review and correct as recommended by a qualified contractor" in text:
        return "Review and correct as recommended by a qualified contractor."

    if "Review and correct" in text:
        return "Review and correct as recommended by a qualified professional."

    return ""


def _hf_loc_issue_to_preview(issue: dict) -> dict:
    record_id = _hf_loc_one_line(issue.get("record_id"))
    title = _hf_loc_fix_encoding(issue.get("title"))
    summary = _hf_loc_fix_encoding(issue.get("summary"))
    section = _hf_loc_fix_encoding(issue.get("section"))
    severity = _hf_loc_fix_encoding(issue.get("standard_severity") or issue.get("severity"))
    source_page = issue.get("source_page")
    source_page_clean = source_page if source_page not in ("", None) else None

    standard_json = _hf_loc_parse_json(issue.get("homefax_standard_json"), {}) or {}

    candidate_urls = _hf_loc_parse_json(issue.get("candidate_image_urls"), [])

    if not isinstance(candidate_urls, list):
        candidate_urls = []

    risk_reasons = issue.get("standard_risk_reasons")

    if not risk_reasons and isinstance(standard_json, dict):
        risk_reasons = standard_json.get("risk_reasons")

    parsed_risk = _hf_loc_parse_json(risk_reasons, risk_reasons)

    if isinstance(parsed_risk, str):
        parsed_risk = [
            part.strip()
            for part in parsed_risk.split(",")
            if part.strip()
        ]

    if not isinstance(parsed_risk, list):
        parsed_risk = []

    summary_system = _hf_loc_parse_labeled_summary_value(summary, "System")
    summary_component = _hf_loc_parse_labeled_summary_value(summary, "Component")
    section_system, section_component = _hf_loc_split_system_component(section)

    system = _hf_loc_one_line(
        issue.get("standard_system")
        or standard_json.get("system")
        or summary_system
        or section_system
    )

    component = _hf_loc_one_line(
        issue.get("standard_component")
        or standard_json.get("component")
        or summary_component
        or section_component
    )

    category = _hf_loc_one_line(
        issue.get("standard_category")
        or standard_json.get("category")
        or system
    )

    defect_type = _hf_loc_one_line(
        issue.get("standard_defect_type")
        or standard_json.get("defect_type")
        or title
    )

    source_item_number = _hf_loc_one_line(
        issue.get("source_item_number")
        or _hf_loc_parse_source_item_number_from_summary(summary)
    )

    source_report_section = _hf_loc_one_line(
        issue.get("source_report_section")
        or section
        or system
        or _hf_loc_section_from_source_text({
            **issue,
            "source_item_number": source_item_number,
            "source_finding_text": issue.get("source_finding_text") or summary,
        })
    )

    source_finding_text = _hf_loc_fix_encoding(
        issue.get("source_finding_text")
        or summary
        or title
    )

    source_finding_title = _hf_loc_one_line(
        issue.get("source_finding_title")
        or title
    )

    source_recommendation = _hf_loc_fix_encoding(
        issue.get("source_recommendation")
        or _hf_loc_extract_recommendation_from_summary(summary)
    )

    recommended_action = _hf_loc_one_line(
        issue.get("standard_recommended_action")
        or standard_json.get("recommended_action")
        or source_recommendation
        or "Review and correct as recommended by a qualified professional."
    )

    recommended_trade = _hf_loc_one_line(
        issue.get("standard_recommended_trade")
        or standard_json.get("recommended_trade")
        or _hf_loc_infer_trade(system, component, title)
    )

    plain_summary = _hf_loc_one_line(
        issue.get("standard_plain_summary")
        or standard_json.get("plain_summary")
        or _hf_loc_build_plain_summary(title, system, component, severity)
    )

    monitoring_plan = _hf_loc_one_line(
        issue.get("standard_monitoring_plan")
        or standard_json.get("monitoring_plan")
        or _hf_loc_build_monitoring_plan(title, system, component)
    )

    standard_location_area = _hf_loc_build_standard_location_area({
        **issue,
        "source_item_number": source_item_number,
        "source_finding_text": source_finding_text,
        "source_report_section": source_report_section,
        "standard_system": system,
        "standard_component": component,
    })

    primary_image_url = _hf_loc_one_line(
        issue.get("verified_image_url")
        or issue.get("image_url")
    )

    return {
        "id": issue.get("id"),
        "title": title,
        "record_id": record_id,

        # Source anchor fields
        "source_item_number": source_item_number,
        "source_page": source_page_clean,
        "source_report_section": source_report_section,
        "source_pdf_url": _hf_loc_source_pdf_url(record_id),
        "source_pdf_page_url": _hf_loc_source_pdf_page_url(record_id, source_page_clean),

        # Original inspector fields
        "source_finding_title": source_finding_title,
        "source_finding_text": source_finding_text,
        "source_recommendation": source_recommendation,

        # Standard/HomeFax fields
        "standard_location_area": standard_location_area,
        "location": standard_location_area,
        "category": category,
        "system": system,
        "component": component,
        "defect_type": defect_type,
        "severity": severity,
        "plain_summary": plain_summary,
        "recommended_trade": recommended_trade,
        "recommended_action": recommended_action,
        "monitoring_plan": monitoring_plan,
        "risk_reasons": parsed_risk,

        # Evidence fields
        "primary_image_url": primary_image_url,
        "candidate_image_count": len(candidate_urls),
        "candidate_image_urls": candidate_urls,

        # Review/workflow state fields
        "status": _hf_loc_one_line(issue.get("status")),
        "homeowner_decision": _hf_loc_one_line(issue.get("homeowner_decision")),
        "homeowner_note": _hf_loc_one_line(issue.get("homeowner_note")),
        "homeowner_reviewed_at": str(issue.get("homeowner_reviewed_at") or ""),
        "current_status": _hf_loc_one_line(issue.get("current_status")),
        "hidden_from_review_queue": _hf_loc_one_line(issue.get("hidden_from_review_queue")),
    }


@app.get("/records/{record_id}/homefax-standard-report-preview-clean-v4")
def homefax_standard_report_preview_clean_v4(record_id: str, limit: int = 100):
    issues = _hf_loc_fetch_standard_issues(record_id, limit=limit)
    preview = [_hf_loc_issue_to_preview(issue) for issue in issues]

    return {
        "success": True,
        "preview_version": "clean_v4_location_fields",
        "schema_version": "homefax_standard_finding_v1",
        "record_id": record_id,
        "issues_total": len(preview),
        "issues_previewed": len(preview),
        "issues": preview,
    }



# ============================================================
# Evidence Photo Candidate Cleanup Pass 1
#
# Purpose:
# - Remove placeholder/tool/non-photo images from candidate_image_urls.
# - Keep primary image first.
# - Keep real-looking inspection photos.
# - Preview before backfill.
#
# New endpoints:
# GET  /records/{record_id}/evidence-photo-candidate-cleanup-preview
# POST /records/{record_id}/evidence-photo-candidate-cleanup/backfill
# ============================================================

import json as _hf_img_clean_json
import re as _hf_img_clean_re
from urllib.parse import unquote as _hf_img_clean_unquote


def _hf_img_clean_safe_text(value) -> str:
    return str(value or "").replace("\x00", " ").strip()


def _hf_img_clean_parse_json(value, fallback=None):
    if fallback is None:
        fallback = []

    if value is None:
        return fallback

    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return value

    text = str(value).strip()

    if not text:
        return fallback

    try:
        parsed = _hf_img_clean_json.loads(text)
        return parsed
    except Exception:
        return fallback


def _hf_img_clean_get_connection():
    if "_hf_report_db_connection" in globals():
        return _hf_report_db_connection()

    for name in ("get_db_connection", "get_connection", "db_connection"):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No database connection helper found.")


def _hf_img_clean_normalize_url(url: str) -> str:
    url = _hf_img_clean_safe_text(url)
    url = _hf_img_clean_unquote(url)
    url = url.replace("\\/", "/")
    return url


def _hf_img_clean_filename(url: str) -> str:
    clean = _hf_img_clean_normalize_url(url)
    clean = clean.split("?")[0].split("#")[0]
    return clean.rsplit("/", 1)[-1].lower()


def _hf_img_clean_is_image_url(url: str) -> bool:
    clean = _hf_img_clean_normalize_url(url).lower().split("?")[0].split("#")[0]
    return clean.endswith((".jpg", ".jpeg", ".png", ".webp"))


def _hf_img_clean_is_placeholder_url(url: str) -> bool:
    """
    Conservative placeholder detection.

    We remove obvious UI/tool/placeholder candidates, but we do not delete
    ambiguous report photos unless they clearly look non-photo.
    """

    clean = _hf_img_clean_normalize_url(url).lower()
    filename = _hf_img_clean_filename(url)

    if not clean:
        return True

    if not _hf_img_clean_is_image_url(clean):
        return True

    placeholder_tokens = [
        "placeholder",
        "no-image",
        "no_image",
        "missing-image",
        "missing_image",
        "default-image",
        "default_image",
        "imageoff",
        "image-off",
        "icon",
        "wrench",
        "tool",
        "repair-icon",
        "camera-placeholder",
    ]

    if any(token in clean for token in placeholder_tokens):
        return True

    # The dashboard shows black/white generic tool symbols. In this report family
    # they often appear as repeated generic PNG assets, not real page photos.
    generic_png_hashes = [
        "9c7e25779a00",
        "718979aa6ac3",
        "105e4b1fa173",
    ]

    if filename.endswith(".png") and any(token in filename for token in generic_png_hashes):
        return True

    # Keep real extracted JPEG report photos by default.
    return False


def _hf_img_clean_url_score(url: str, issue: dict) -> int:
    """
    Higher score means better evidence candidate.
    """

    clean = _hf_img_clean_normalize_url(url).lower()
    filename = _hf_img_clean_filename(url)

    score = 0

    if _hf_img_clean_is_placeholder_url(url):
        return -999

    if filename.endswith((".jpg", ".jpeg")):
        score += 30

    if filename.endswith(".webp"):
        score += 20

    if filename.endswith(".png"):
        score += 5

    if "/inspection-images/" in clean or "/inspection-images-s3/" in clean:
        score += 20

    if "page_" in filename and "_img_" in filename:
        score += 20

    primary = _hf_img_clean_normalize_url(
        issue.get("verified_image_url") or issue.get("image_url") or ""
    )

    if primary and _hf_img_clean_normalize_url(url) == primary:
        score += 100

    # Prefer candidates close to source page when page is available.
    source_page = issue.get("source_page")
    if source_page not in ("", None):
        try:
            source_page_int = int(source_page)
            page_match = _hf_img_clean_re.search(r"page_(\d+)_img_", filename)
            if page_match:
                image_page = int(page_match.group(1))
                distance = abs(image_page - source_page_int)

                if distance == 0:
                    score += 35
                elif distance == 1:
                    score += 25
                elif distance == 2:
                    score += 15
                elif distance <= 5:
                    score += 5
                else:
                    score -= min(distance, 20)
        except Exception:
            pass

    return score


def _hf_img_clean_unique_urls(urls):
    seen = set()
    output = []

    for url in urls:
        clean = _hf_img_clean_normalize_url(url)

        if not clean:
            continue

        key = clean.lower()

        if key in seen:
            continue

        seen.add(key)
        output.append(clean)

    return output


def _hf_img_clean_issue_candidates(issue: dict, max_images: int = 6):
    raw_candidates = _hf_img_clean_parse_json(issue.get("candidate_image_urls"), [])

    if not isinstance(raw_candidates, list):
        raw_candidates = []

    primary = _hf_img_clean_normalize_url(
        issue.get("verified_image_url") or issue.get("image_url") or ""
    )

    combined = []

    if primary:
        combined.append(primary)

    combined.extend(raw_candidates)
    combined = _hf_img_clean_unique_urls(combined)

    kept = []
    removed = []

    for url in combined:
        if _hf_img_clean_is_placeholder_url(url):
            removed.append(
                {
                    "url": url,
                    "reason": "placeholder_or_non_photo",
                }
            )
        else:
            kept.append(url)

    kept = sorted(
        kept,
        key=lambda candidate_url: _hf_img_clean_url_score(candidate_url, issue),
        reverse=True,
    )

    # Primary must remain first if it is valid.
    if primary and primary in kept:
        kept = [primary] + [url for url in kept if url != primary]

    kept = kept[: int(max_images or 6)]

    return {
        "old_candidates": raw_candidates,
        "primary_image_url": primary,
        "new_candidates": kept,
        "removed_candidates": removed,
        "old_count": len(raw_candidates),
        "new_count": len(kept),
        "removed_count": len(removed),
        "changed": raw_candidates != kept,
    }


def _hf_img_clean_fetch_issues(record_id: str):
    conn = _hf_img_clean_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    record_id,
                    title,
                    source_item_number,
                    source_page,
                    source_finding_title,
                    standard_system,
                    standard_component,
                    image_url,
                    verified_image_url,
                    candidate_image_urls
                FROM verified_issues
                WHERE record_id = %s
                ORDER BY
                    CASE
                        WHEN source_item_number REGEXP '^[0-9]+\\\\.[0-9]+\\\\.[0-9]+'
                        THEN CAST(SUBSTRING_INDEX(source_item_number, '.', 1) AS UNSIGNED)
                        ELSE 999
                    END,
                    CASE
                        WHEN source_item_number REGEXP '^[0-9]+\\\\.[0-9]+\\\\.[0-9]+'
                        THEN CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(source_item_number, '.', 2), '.', -1) AS UNSIGNED)
                        ELSE 999
                    END,
                    CASE
                        WHEN source_item_number REGEXP '^[0-9]+\\\\.[0-9]+\\\\.[0-9]+'
                        THEN CAST(SUBSTRING_INDEX(source_item_number, '.', -1) AS UNSIGNED)
                        ELSE 999
                    END,
                    id ASC
                """,
                (record_id,),
            )

            rows = cursor.fetchall() or []

            if rows and not isinstance(rows[0], dict):
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in rows]

            return rows

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_img_clean_run(record_id: str, dry_run: bool = True, max_images: int = 6, limit: int = 0):
    issues = _hf_img_clean_fetch_issues(record_id)

    if limit and int(limit) > 0:
        issues = issues[: int(limit)]

    results = []
    updates = []

    for issue in issues:
        cleanup = _hf_img_clean_issue_candidates(issue, max_images=max_images)

        row = {
            "id": issue.get("id"),
            "source_item_number": issue.get("source_item_number"),
            "title": issue.get("source_finding_title") or issue.get("title"),
            "source_page": issue.get("source_page"),
            "old_count": cleanup["old_count"],
            "new_count": cleanup["new_count"],
            "removed_count": cleanup["removed_count"],
            "changed": cleanup["changed"],
            "primary_image_url": cleanup["primary_image_url"],
            "new_candidates": cleanup["new_candidates"],
            "removed_candidates": cleanup["removed_candidates"],
        }

        results.append(row)

        if cleanup["changed"]:
            updates.append(
                {
                    "id": issue.get("id"),
                    "candidate_image_urls": _hf_img_clean_json.dumps(cleanup["new_candidates"]),
                }
            )

    if not dry_run and updates:
        conn = _hf_img_clean_get_connection()

        try:
            with conn.cursor() as cursor:
                for update in updates:
                    cursor.execute(
                        """
                        UPDATE verified_issues
                        SET candidate_image_urls = %s
                        WHERE id = %s
                        """,
                        (
                            update["candidate_image_urls"],
                            update["id"],
                        ),
                    )

            conn.commit()

        finally:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "success": True,
        "dry_run": dry_run,
        "record_id": record_id,
        "issues_checked": len(issues),
        "changed_count": len(updates),
        "max_images": int(max_images or 6),
        "results_preview": results[:15],
        "total_removed_candidates": sum(item["removed_count"] for item in results),
    }


@app.get("/records/{record_id}/evidence-photo-candidate-cleanup-preview")
def evidence_photo_candidate_cleanup_preview(record_id: str, max_images: int = 6, limit: int = 15):
    return _hf_img_clean_run(
        record_id=record_id,
        dry_run=True,
        max_images=max_images,
        limit=limit,
    )


@app.post("/records/{record_id}/evidence-photo-candidate-cleanup/backfill")
def evidence_photo_candidate_cleanup_backfill(record_id: str, max_images: int = 6):
    return _hf_img_clean_run(
        record_id=record_id,
        dry_run=False,
        max_images=max_images,
        limit=0,
    )



# ============================================================
# Standard Review Action Endpoint Pass 1
#
# Purpose:
# - Dedicated compatibility endpoint for HomeFax standard finding cards.
# - Updates existing verified_issues workflow columns.
#
# Endpoint:
# PATCH /verified-issue/{issue_id}/standard-review-action
# ============================================================

from datetime import datetime as _hf_review_datetime
from pydantic import BaseModel as _hf_review_BaseModel
from typing import Optional as _hf_review_Optional


class _HFStandardReviewActionPayload(_hf_review_BaseModel):
    decision: str
    note: _hf_review_Optional[str] = ""
    homeowner_image_decision: _hf_review_Optional[str] = ""
    homeowner_selected_image_url: _hf_review_Optional[str] = ""
    homeowner_selected_image_note: _hf_review_Optional[str] = ""

    # Dual Action + Monitoring Decision Pass 1
    monitoring_required: _hf_review_Optional[str] = ""
    monitoring_trigger: _hf_review_Optional[str] = ""
    monitoring_plan_text: _hf_review_Optional[str] = ""
    post_repair_monitoring_required: _hf_review_Optional[str] = ""

    homeowner_user_id: _hf_review_Optional[str] = None
    homeowner_email: _hf_review_Optional[str] = None


def _hf_review_get_connection():
    if "_hf_report_db_connection" in globals():
        return _hf_report_db_connection()

    for name in ("get_db_connection", "get_connection", "db_connection"):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No database connection helper found.")


# Homeowner Image Selection Save Pass 1A
def _hf_review_add_column_if_missing(cursor, table_name: str, column_name: str, column_definition: str):
    helper = globals().get("add_column_if_missing")
    full_column_definition = f"{column_name} {column_definition}"

    if callable(helper):
        return helper(cursor, table_name, column_name, full_column_definition)

    try:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {full_column_definition}")
    except Exception as exc:
        message = str(exc).lower()
        if "duplicate column" not in message and "1060" not in message:
            raise


def _hf_review_ensure_homeowner_image_selection_schema():
    conn = _hf_review_get_connection()

    try:
        with conn.cursor() as cursor:
            _hf_review_add_column_if_missing(
                cursor,
                "verified_issues",
                "homeowner_image_decision",
                "VARCHAR(100) DEFAULT 'unreviewed'",
            )
            _hf_review_add_column_if_missing(
                cursor,
                "verified_issues",
                "homeowner_selected_image_url",
                "TEXT NULL",
            )
            _hf_review_add_column_if_missing(
                cursor,
                "verified_issues",
                "homeowner_selected_image_note",
                "TEXT NULL",
            )
            _hf_review_add_column_if_missing(
                cursor,
                "verified_issues",
                "homeowner_selected_image_updated_at",
                "DATETIME NULL",
            )

            # Dual Action + Monitoring Decision Pass 1
            _hf_review_add_column_if_missing(
                cursor,
                "verified_issues",
                "monitoring_required",
                "VARCHAR(16) DEFAULT 'no'",
            )
            _hf_review_add_column_if_missing(
                cursor,
                "verified_issues",
                "monitoring_trigger",
                "VARCHAR(128) DEFAULT ''",
            )
            _hf_review_add_column_if_missing(
                cursor,
                "verified_issues",
                "monitoring_plan_text",
                "TEXT NULL",
            )
            _hf_review_add_column_if_missing(
                cursor,
                "verified_issues",
                "post_repair_monitoring_required",
                "VARCHAR(16) DEFAULT 'no'",
            )

        conn.commit()

    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_review_normalize_decision(value: str) -> str:
    decision = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

    allowed = {
        "monitor",
        "repair_needed",
        "already_repaired",
        "not_an_issue",
        "wrong_photo",
        "needs_contractor",
    }

    if decision not in allowed:
        raise ValueError(f"Unsupported review decision: {decision}")

    return decision


def _hf_review_status_for_decision(decision: str):
    """
    Maps standard card decision to current workflow fields.
    """
    if decision == "monitor":
        return {
            "status": "monitor",
            "current_status": "monitoring",
            "hidden_from_review_queue": "no",
        }

    if decision == "repair_needed":
        return {
            "status": "repair_needed",
            "current_status": "repair_needed",
            "hidden_from_review_queue": "no",
        }

    if decision == "already_repaired":
        return {
            "status": "repaired",
            "current_status": "repaired",
            "hidden_from_review_queue": "yes",
        }

    if decision == "not_an_issue":
        return {
            "status": "dismissed",
            "current_status": "dismissed",
            "hidden_from_review_queue": "yes",
        }

    if decision == "wrong_photo":
        return {
            "status": "image_review_needed",
            "current_status": "needs_image_review",
            "hidden_from_review_queue": "no",
        }

    if decision == "needs_contractor":
        return {
            "status": "needs_contractor",
            "current_status": "needs_contractor",
            "hidden_from_review_queue": "no",
        }

    return {
        "status": "reviewed",
        "current_status": "open",
        "hidden_from_review_queue": "no",
    }



# Dual Action Monitoring Plan Sync Pass 2
def _hf_dual_monitoring_yes(value) -> bool:
    return str(value or "").strip().lower() in {"yes", "true", "1", "y", "on"}


def _hf_dual_monitoring_text_blob(issue: dict, monitoring_trigger: str = "", monitoring_plan_text: str = "") -> str:
    parts = [
        issue.get("source_finding_title"),
        issue.get("title"),
        issue.get("source_item_number"),
        issue.get("system"),
        issue.get("component"),
        issue.get("category"),
        issue.get("location"),
        issue.get("recommendation"),
        issue.get("action"),
        issue.get("trade"),
        monitoring_trigger,
        monitoring_plan_text,
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _hf_dual_monitoring_profile(issue: dict, monitoring_trigger: str = "", monitoring_plan_text: str = "") -> dict:
    text = _hf_dual_monitoring_text_blob(issue, monitoring_trigger, monitoring_plan_text)

    trigger = str(monitoring_trigger or "").strip().lower()

    # Foundation / settlement / soil profile
    if any(word in text for word in [
        "settlement",
        "foundation",
        "structural",
        "crack",
        "bowing",
        "movement",
        "heaving",
        "soil",
        "grading",
        "slope",
        "erosion",
        "expansive",
        "dry soil",
        "lawn",
    ]):
        return {
            "risk_type": "foundation_soil_movement",
            "allowed_capabilities": [
                "WEATHER_RAIN",
                "WEATHER_DROUGHT",
                "SOIL_MOISTURE",
                "FOUNDATION_MOVEMENT",
                "PHOTO_EVIDENCE",
                "MANUAL_CHECK",
            ],
            "monitoring_rules": {
                "trigger_group": trigger or "foundation_soil_watch",
                "weather_triggers": ["heavy_rain", "extended_dry_period", "drought"],
                "device_triggers": ["SOIL_MOISTURE", "FOUNDATION_MOVEMENT"],
                "manual_triggers": ["new_crack_photo", "crack_width_change", "water_entry", "new_staining"],
            },
        }

    # Roof / flashing / exterior envelope profile
    if any(word in text for word in [
        "roof",
        "flashing",
        "shingle",
        "gutter",
        "downspout",
        "fascia",
        "soffit",
        "chimney",
        "skylight",
        "siding",
        "exterior envelope",
    ]):
        return {
            "risk_type": "roof_envelope_weather",
            "allowed_capabilities": [
                "WEATHER_RAIN",
                "WEATHER_WIND",
                "ROOF_ENVELOPE",
                "MOISTURE",
                "PHOTO_EVIDENCE",
                "MANUAL_CHECK",
            ],
            "monitoring_rules": {
                "trigger_group": trigger or "roof_weather_watch",
                "weather_triggers": ["heavy_rain", "high_wind", "storm"],
                "device_triggers": ["MOISTURE"],
                "manual_triggers": ["new_roof_photo", "staining", "leak_report", "loose_material"],
            },
        }

    # Water / plumbing / basement moisture profile
    if any(word in text for word in [
        "leak",
        "moisture",
        "water",
        "plumbing",
        "basement",
        "sump",
        "drain",
        "seepage",
        "stain",
        "mold",
        "humidity",
    ]):
        return {
            "risk_type": "water_moisture",
            "allowed_capabilities": [
                "WATER_LEAK",
                "WATER_SHUTOFF",
                "MOISTURE",
                "HUMIDITY",
                "WEATHER_RAIN",
                "SUMP_PUMP",
                "PHOTO_EVIDENCE",
                "MANUAL_CHECK",
            ],
            "monitoring_rules": {
                "trigger_group": trigger or "water_moisture_watch",
                "weather_triggers": ["heavy_rain"],
                "device_triggers": ["WATER_LEAK", "MOISTURE", "HUMIDITY", "SUMP_PUMP"],
                "manual_triggers": ["new_staining", "odor", "standing_water", "new_photo"],
            },
        }

    # Electrical profile
    if any(word in text for word in [
        "electrical",
        "gfci",
        "outlet",
        "breaker",
        "panel",
        "wire",
        "wiring",
        "junction",
        "ground",
        "polarity",
    ]):
        return {
            "risk_type": "electrical_safety",
            "allowed_capabilities": [
                "ELECTRICAL_LOAD",
                "ELECTRICAL_ANOMALY",
                "PHOTO_EVIDENCE",
                "MANUAL_CHECK",
            ],
            "monitoring_rules": {
                "trigger_group": trigger or "electrical_safety_watch",
                "weather_triggers": [],
                "device_triggers": ["ELECTRICAL_LOAD", "ELECTRICAL_ANOMALY"],
                "manual_triggers": ["repair_photo", "electrician_update", "recurrence_report"],
            },
        }

    # HVAC / environment profile
    if any(word in text for word in [
        "hvac",
        "furnace",
        "air conditioner",
        "ac",
        "condenser",
        "thermostat",
        "temperature",
        "vent",
        "duct",
    ]):
        return {
            "risk_type": "hvac_environment",
            "allowed_capabilities": [
                "HVAC_RUNTIME",
                "TEMPERATURE",
                "HUMIDITY",
                "PHOTO_EVIDENCE",
                "MANUAL_CHECK",
            ],
            "monitoring_rules": {
                "trigger_group": trigger or "hvac_environment_watch",
                "weather_triggers": ["extreme_temperature"],
                "device_triggers": ["HVAC_RUNTIME", "TEMPERATURE", "HUMIDITY"],
                "manual_triggers": ["service_update", "comfort_issue", "new_photo"],
            },
        }

    return {
        "risk_type": "general_monitoring",
        "allowed_capabilities": [
            "PHOTO_EVIDENCE",
            "MANUAL_CHECK",
        ],
        "monitoring_rules": {
            "trigger_group": trigger or "general_watch",
            "weather_triggers": [],
            "device_triggers": [],
            "manual_triggers": ["new_photo", "status_update", "recurrence_report"],
        },
    }


# Dual Action Monitoring Plan SQL Quote Fix
# Dual Action Monitoring Plan Sync Pass 2 Schema Compatibility Fix
def _hf_dual_monitoring_sync_plan(issue_id: int, monitoring_required: str, monitoring_trigger: str, monitoring_plan_text: str, post_repair_monitoring_required: str = "") -> dict:
    if not _hf_dual_monitoring_yes(monitoring_required):
        return {
            "attempted": False,
            "created_or_updated": False,
            "reason": "monitoring_required is not yes",
        }

    conn = None
    cursor = None

    try:
        schema_helper = globals().get("_hf_mon_ensure_schema")
        if callable(schema_helper):
            schema_helper()

        get_connection = globals().get("_hf_mon_get_connection") or globals().get("get_db_connection")
        if not callable(get_connection):
            return {
                "attempted": True,
                "created_or_updated": False,
                "error": "No database connection helper available for monitoring sync.",
            }

        conn = get_connection()
        cursor = conn.cursor()

        # Keep the existing monitoring_plans schema compatible with the newer dual-action logic.
        add_column = globals().get("_hf_review_add_column_if_missing")
        if callable(add_column):
            try:
                add_column(cursor, "monitoring_plans", "monitoring_rules", "TEXT NULL")
                add_column(cursor, "monitoring_plans", "monitoring_trigger", "VARCHAR(128) DEFAULT ''")
                add_column(cursor, "monitoring_plans", "post_repair_monitoring_required", "VARCHAR(16) DEFAULT 'no'")
            except Exception:
                # Do not block plan sync if optional display columns cannot be added.
                pass

        cursor.execute(
            """
            SELECT *
            FROM verified_issues
            WHERE id = %s
            """,
            (issue_id,),
        )
        issue = cursor.fetchone()

        if not issue:
            return {
                "attempted": True,
                "created_or_updated": False,
                "error": "Issue not found.",
            }

        profile = _hf_dual_monitoring_profile(issue, monitoring_trigger, monitoring_plan_text)

        record_id = (
            issue.get("record_id")
            or issue.get("inspection_record_id")
            or issue.get("submission_id")
            or issue.get("source_record_id")
            or ""
        )

        tenant_id = issue.get("tenant_id") or "lateef-home-inspection"
        property_id = issue.get("property_id") or record_id or ""
        system = issue.get("system") or issue.get("category") or ""
        component = issue.get("component") or ""
        location = issue.get("location") or ""

        current_status = issue.get("current_status") or issue.get("status") or "monitoring"

        plan_status = "active"
        if current_status in {"closed", "resolved"} and _hf_dual_monitoring_yes(post_repair_monitoring_required):
            plan_status = "post_repair_watch"

        import json as _hf_dual_json

        allowed_capabilities_json = _hf_dual_json.dumps(profile["allowed_capabilities"])
        monitoring_rules_json = _hf_dual_json.dumps(profile["monitoring_rules"])

        # Existing production schema uses source_issue_id, not issue_id.
        cursor.execute(
            """
            SELECT id
            FROM monitoring_plans
            WHERE source_issue_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (issue_id,),
        )
        existing_plan = cursor.fetchone()

        if existing_plan:
            plan_id = existing_plan.get("id")

            cursor.execute(
                """
                UPDATE monitoring_plans
                SET tenant_id = %s,
                    property_id = %s,
                    record_id = %s,
                    source_issue_id = %s,
                    `system` = %s,
                    `component` = %s,
                    `location` = %s,
                    risk_type = %s,
                    `status` = %s,
                    monitoring_plan_text = %s,
                    allowed_capabilities = %s,
                    monitoring_rules = %s,
                    monitoring_trigger = %s,
                    post_repair_monitoring_required = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    tenant_id,
                    property_id,
                    record_id,
                    issue_id,
                    system,
                    component,
                    location,
                    profile["risk_type"],
                    plan_status,
                    monitoring_plan_text,
                    allowed_capabilities_json,
                    monitoring_rules_json,
                    monitoring_trigger,
                    post_repair_monitoring_required or "no",
                    plan_id,
                ),
            )

            created = False
        else:
            cursor.execute(
                """
                INSERT INTO monitoring_plans (
                    tenant_id,
                    property_id,
                    record_id,
                    source_issue_id,
                    `system`,
                    `component`,
                    `location`,
                    risk_type,
                    `status`,
                    monitoring_plan_text,
                    allowed_capabilities,
                    monitoring_rules,
                    monitoring_trigger,
                    post_repair_monitoring_required,
                    created_from,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    tenant_id,
                    property_id,
                    record_id,
                    issue_id,
                    system,
                    component,
                    location,
                    profile["risk_type"],
                    plan_status,
                    monitoring_plan_text,
                    allowed_capabilities_json,
                    monitoring_rules_json,
                    monitoring_trigger,
                    post_repair_monitoring_required or "no",
                    "dual_action_review",
                ),
            )
            plan_id = cursor.lastrowid
            created = True

        cursor.execute(
            """
            UPDATE verified_issues
            SET monitoring_required = %s,
                monitoring_trigger = %s,
                monitoring_plan_text = %s,
                post_repair_monitoring_required = %s,
                monitoring_plan_id = %s
            WHERE id = %s
            """,
            (
                "yes",
                monitoring_trigger,
                monitoring_plan_text,
                post_repair_monitoring_required or "no",
                plan_id,
                issue_id,
            ),
        )

        conn.commit()

        return {
            "attempted": True,
            "created_or_updated": True,
            "created": created,
            "plan_id": plan_id,
            "risk_type": profile["risk_type"],
            "allowed_capabilities": profile["allowed_capabilities"],
            "monitoring_rules": profile["monitoring_rules"],
            "plan_status": plan_status,
            "schema_key": "source_issue_id",
        }

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        return {
            "attempted": True,
            "created_or_updated": False,
            "error": str(exc),
        }

    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@app.patch("/verified-issue/{issue_id}/standard-review-action")
def update_verified_issue_standard_review_action(
    issue_id: int,
    payload: _HFStandardReviewActionPayload,
):
    _hf_guard_verified_issue_not_baseline_locked(issue_id, "standard-review-action")
    _hf_review_ensure_homeowner_image_selection_schema()

    decision = _hf_review_normalize_decision(payload.decision)
    note = str(payload.note or "").strip()
    status_map = _hf_review_status_for_decision(decision)

    homeowner_selected_image_url = str(payload.homeowner_selected_image_url or "").strip()
    homeowner_selected_image_note = str(payload.homeowner_selected_image_note or note or "").strip()
    homeowner_image_decision = str(payload.homeowner_image_decision or "").strip().lower().replace("-", "_").replace(" ", "_")

    # Dual Action + Monitoring Decision Pass 1
    monitoring_required = str(payload.monitoring_required or "").strip().lower()
    monitoring_trigger = str(payload.monitoring_trigger or "").strip().lower().replace("-", "_").replace(" ", "_")
    monitoring_plan_text = str(payload.monitoring_plan_text or "").strip()
    post_repair_monitoring_required = str(payload.post_repair_monitoring_required or "").strip().lower()

    if monitoring_required in {"true", "1", "yes", "y", "on"}:
        monitoring_required = "yes"
    elif monitoring_required in {"false", "0", "no", "n", "off", ""}:
        monitoring_required = "no"

    if post_repair_monitoring_required in {"true", "1", "yes", "y", "on"}:
        post_repair_monitoring_required = "yes"
    elif post_repair_monitoring_required in {"false", "0", "no", "n", "off", ""}:
        post_repair_monitoring_required = "no"

    # A pure monitor decision always requires monitoring.
    if decision == "monitor":
        monitoring_required = "yes"

    # Already repaired can still need post-repair watch.
    if decision == "already_repaired" and post_repair_monitoring_required == "yes":
        monitoring_required = "yes"

    if monitoring_required == "yes" and not monitoring_trigger:
        monitoring_trigger = "general_watch"

    if monitoring_required == "yes" and not monitoring_plan_text:
        if monitoring_trigger in {"weather_rain_wind", "rain_high_wind", "rain", "high_wind"}:
            monitoring_plan_text = (
                "Monitor during and after heavy rain or high winds for water entry, loose materials, "
                "staining, leaks, movement, or worsening exterior conditions."
            )
        elif decision in {"needs_contractor", "repair_needed"}:
            monitoring_plan_text = (
                "Monitor this issue until repair is completed, then continue post-repair follow-up for recurrence, "
                "worsening conditions, or new evidence."
            )
        else:
            monitoring_plan_text = (
                "Monitor this issue for changes, recurrence, worsening conditions, or related evidence updates."
            )

    if not homeowner_image_decision:
        if decision == "wrong_photo":
            homeowner_image_decision = "mismatch"
        elif homeowner_selected_image_url:
            homeowner_image_decision = "selected"
        else:
            homeowner_image_decision = "no_image"

    allowed_homeowner_image_decisions = {
        "unreviewed",
        "accepted",
        "selected",
        "mismatch",
        "needs_review",
        "no_image",
    }

    if homeowner_image_decision not in allowed_homeowner_image_decisions:
        return {
            "success": False,
            "error": "invalid_homeowner_image_decision",
            "allowed": sorted(allowed_homeowner_image_decisions),
            "received": homeowner_image_decision,
        }

    conn = _hf_review_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, source_item_number, source_finding_title,
                       homeowner_decision, homeowner_note, status, current_status,
                       image_url, verified_image_url, homeowner_selected_image_url,
                       homeowner_image_decision, monitoring_required,
                       monitoring_trigger, monitoring_plan_text,
                       post_repair_monitoring_required
                FROM verified_issues
                WHERE id = %s
                """,
                (issue_id,),
            )

            existing = cursor.fetchone()

            if not existing:
                return {
                    "success": False,
                    "error": "verified_issue_not_found",
                    "issue_id": issue_id,
                }

            cursor.execute(
                """
                UPDATE verified_issues
                SET homeowner_decision = %s,
                    homeowner_note = %s,
                    homeowner_image_decision = %s,
                    homeowner_selected_image_url = %s,
                    homeowner_selected_image_note = %s,
                    homeowner_selected_image_updated_at = NOW(),
                    monitoring_required = %s,
                    monitoring_trigger = %s,
                    monitoring_plan_text = %s,
                    post_repair_monitoring_required = %s,
                    homeowner_reviewed_at = NOW(),
                    status = %s,
                    current_status = %s,
                    hidden_from_review_queue = %s
                WHERE id = %s
                """,
                (
                    decision,
                    note,
                    homeowner_image_decision,
                    homeowner_selected_image_url,
                    homeowner_selected_image_note,
                    monitoring_required,
                    monitoring_trigger,
                    monitoring_plan_text,
                    post_repair_monitoring_required,
                    status_map["status"],
                    status_map["current_status"],
                    status_map["hidden_from_review_queue"],
                    issue_id,
                ),
            )

        conn.commit()

        # Dual Action Monitoring Plan Sync Pass 2
        monitoring_lifecycle = _hf_dual_monitoring_sync_plan(
            issue_id=issue_id,
            monitoring_required=monitoring_required,
            monitoring_trigger=monitoring_trigger,
            monitoring_plan_text=monitoring_plan_text,
            post_repair_monitoring_required=post_repair_monitoring_required,
        )

        return {
            "success": True,
            "issue_id": issue_id,
            "decision": decision,
            "note": note,
            "homeowner_image_decision": homeowner_image_decision,
            "homeowner_selected_image_url": homeowner_selected_image_url,
            "homeowner_selected_image_note": homeowner_selected_image_note,
            "monitoring_required": monitoring_required,
            "monitoring_trigger": monitoring_trigger,
            "monitoring_plan_text": monitoring_plan_text,
            "post_repair_monitoring_required": post_repair_monitoring_required,
            "monitoring_lifecycle": monitoring_lifecycle,
            "status": status_map["status"],
            "current_status": status_map["current_status"],
            "hidden_from_review_queue": status_map["hidden_from_review_queue"],
        }

    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# HomeFax Intake Standard API Preview Pass 1
#
# Purpose:
# - Expose the official HomeFax Intake Standard v1 payload.
# - Reuse the existing clean-v4 standard preview output.
# - Reuse tools/homefax_intake_standard_mapper_v1.py mapping logic.
#
# Safety:
# - Preview only.
# - No database writes.
# - No n8n calls.
# - No dashboard changes.
# ============================================================

@app.get("/homefax-intake-standard-api-health")
def homefax_intake_standard_api_health():
    """
    Health check for HomeFax Intake Standard API Preview Pass 1.
    """
    return {
        "success": True,
        "service": "homefax_intake_standard_api_preview",
        "version": "1.0",
        "endpoints": [
            "/records/{record_id}/homefax-intake-standard-preview-v1"
        ],
        "writes_to_database": False,
        "calls_n8n": False,
        "status": "ready",
    }


@app.get("/records/{record_id}/homefax-intake-standard-preview-v1")
def homefax_intake_standard_preview_v1(record_id: str, limit: int = 100):
    """
    Build a HomeFax Intake Standard v1 preview payload from the current
    HomeFax standard clean-v4 preview endpoint.

    This endpoint is intentionally read-only.
    """
    try:
        from tools.homefax_intake_standard_mapper_v1 import build_homefax_intake_payload

        if limit < 1:
            limit = 1

        if limit > 500:
            limit = 500

        # Reuse the existing standard preview function that powers the dashboard.
        preview_payload = homefax_standard_report_preview_clean_v4(
            record_id=record_id,
            limit=limit,
        )

        if not isinstance(preview_payload, dict):
            return {
                "success": False,
                "error": "clean_v4_preview_returned_non_dict",
                "record_id": record_id,
            }

        if preview_payload.get("success") is not True:
            return {
                "success": False,
                "error": "clean_v4_preview_failed",
                "record_id": record_id,
                "clean_v4_preview": preview_payload,
            }

        mapped_payload = build_homefax_intake_payload(
            preview_payload=preview_payload,
            record_id=record_id,
            tenant_id="lateef-home-inspection",
        )

        return {
            "success": True,
            "preview_version": "homefax_intake_standard_preview_v1",
            "record_id": record_id,
            "homefax_intake_standard_version": mapped_payload.get("homefax_intake_standard_version"),
            "issues_count": mapped_payload.get("processing", {}).get("issues_count"),
            "candidate_images_count": mapped_payload.get("processing", {}).get("candidate_images_count"),
            "payload": mapped_payload,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": "homefax_intake_standard_preview_v1_failed",
            "record_id": record_id,
            "detail": str(exc),
        }

# ============================================================
# HomeFax Intake Standard Validation Endpoint Pass 1
#
# Purpose:
# - Validate HomeFax Intake Standard v1 payloads before processing.
# - Give n8n, Zite, manual imports, future partner APIs, and tests
#   a safe way to confirm payload readiness.
#
# Safety:
# - Validation only.
# - No database writes.
# - No n8n calls.
# - No dashboard changes.
# ============================================================

def _hf_intake_val_text(value) -> str:
    return str(value or "").strip()


def _hf_intake_val_is_obj(value) -> bool:
    return isinstance(value, dict)


def _hf_intake_val_is_array(value) -> bool:
    return isinstance(value, list)


def _hf_intake_val_bool(value, default=False) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return default

    text = str(value).strip().lower()

    if text in {"1", "true", "yes", "y", "on"}:
        return True

    if text in {"0", "false", "no", "n", "off"}:
        return False

    return default


def _hf_intake_validate_payload(payload: dict) -> dict:
    """
    Validate a HomeFax Intake Standard v1 payload.

    This validator intentionally returns warnings/errors instead of raising
    so n8n/Zite/manual imports can get useful feedback.
    """
    errors = []
    warnings = []
    counts = {
        "standard_findings": 0,
        "findings_with_candidate_images_array": 0,
        "findings_with_verified_images": 0,
        "findings_baseline_locked": 0,
        "findings_ready_for_review": 0,
    }

    if not isinstance(payload, dict):
        return {
            "valid": False,
            "errors": ["payload must be a JSON object"],
            "warnings": [],
            "counts": counts,
        }

    required_top_level = [
        "homefax_intake_standard_version",
        "record_id",
        "tenant_id",
        "source",
        "property",
        "homeowner",
        "inspection",
        "original_report",
        "processing",
        "standard_findings",
        "audit",
    ]

    for key in required_top_level:
        if key not in payload:
            errors.append(f"missing required top-level field: {key}")

    version = _hf_intake_val_text(payload.get("homefax_intake_standard_version"))
    if version != "1.0":
        errors.append("homefax_intake_standard_version must be '1.0'")

    if not _hf_intake_val_text(payload.get("record_id")):
        errors.append("record_id is required")

    if not _hf_intake_val_text(payload.get("tenant_id")):
        errors.append("tenant_id is required")

    object_fields = [
        "source",
        "property",
        "homeowner",
        "inspection",
        "original_report",
        "processing",
        "audit",
    ]

    for key in object_fields:
        if key in payload and not _hf_intake_val_is_obj(payload.get(key)):
            errors.append(f"{key} must be an object")

    standard_findings = payload.get("standard_findings")

    if not _hf_intake_val_is_array(standard_findings):
        errors.append("standard_findings must be an array")
        standard_findings = []

    counts["standard_findings"] = len(standard_findings)

    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    property_obj = payload.get("property") if isinstance(payload.get("property"), dict) else {}
    homeowner = payload.get("homeowner") if isinstance(payload.get("homeowner"), dict) else {}
    original_report = payload.get("original_report") if isinstance(payload.get("original_report"), dict) else {}
    processing = payload.get("processing") if isinstance(payload.get("processing"), dict) else {}

    if not _hf_intake_val_text(source.get("source_system")):
        warnings.append("source.source_system is blank")

    if not _hf_intake_val_text(property_obj.get("address_full")):
        errors.append("property.address_full is required")

    if not _hf_intake_val_text(homeowner.get("email")):
        warnings.append("homeowner.email is blank")

    if not _hf_intake_val_text(original_report.get("file_name")):
        errors.append("original_report.file_name is required")

    if not _hf_intake_val_text(processing.get("pipeline_stage")):
        warnings.append("processing.pipeline_stage is blank")

    if processing.get("issues_count") not in ("", None):
        try:
            declared_count = int(processing.get("issues_count"))
            if declared_count != len(standard_findings):
                warnings.append(
                    f"processing.issues_count ({declared_count}) does not match standard_findings length ({len(standard_findings)})"
                )
        except Exception:
            warnings.append("processing.issues_count is not numeric")

    allowed_image_statuses = {
        "none",
        "suggested",
        "verified",
        "mismatch",
        "image_review_needed",
    }

    allowed_homeowner_decisions = {
        "unreviewed",
        "monitor",
        "repair_needed",
        "needs_contractor",
        "wrong_photo",
        "already_repaired",
        "not_an_issue",
    }

    allowed_final_statuses = {
        "not_approved",
        "approved",
        "rejected",
    }

    for index, finding in enumerate(standard_findings):
        label = f"standard_findings[{index}]"

        if not isinstance(finding, dict):
            errors.append(f"{label} must be an object")
            continue

        finding_id = _hf_intake_val_text(finding.get("finding_id")) or f"index {index}"

        source_obj = finding.get("source")
        homefax_obj = finding.get("homefax")
        evidence_obj = finding.get("evidence")
        review_state = finding.get("review_state")
        admin_state = finding.get("admin_state")
        monitoring = finding.get("monitoring")

        for section_name, section_value in [
            ("source", source_obj),
            ("homefax", homefax_obj),
            ("evidence", evidence_obj),
            ("review_state", review_state),
            ("admin_state", admin_state),
            ("monitoring", monitoring),
        ]:
            if not isinstance(section_value, dict):
                errors.append(f"{label} ({finding_id}) missing or invalid object: {section_name}")

        source_obj = source_obj if isinstance(source_obj, dict) else {}
        homefax_obj = homefax_obj if isinstance(homefax_obj, dict) else {}
        evidence_obj = evidence_obj if isinstance(evidence_obj, dict) else {}
        review_state = review_state if isinstance(review_state, dict) else {}
        admin_state = admin_state if isinstance(admin_state, dict) else {}
        monitoring = monitoring if isinstance(monitoring, dict) else {}

        if not _hf_intake_val_text(source_obj.get("source_finding_title")):
            errors.append(f"{label} ({finding_id}) source.source_finding_title is required")

        if not _hf_intake_val_text(source_obj.get("source_item_number")):
            warnings.append(f"{label} ({finding_id}) source.source_item_number is blank")

        if not _hf_intake_val_text(source_obj.get("source_finding_text")):
            warnings.append(f"{label} ({finding_id}) source.source_finding_text is blank")

        if not _hf_intake_val_text(homefax_obj.get("category")):
            warnings.append(f"{label} ({finding_id}) homefax.category is blank")

        if not _hf_intake_val_text(homefax_obj.get("system")):
            warnings.append(f"{label} ({finding_id}) homefax.system is blank")

        if not _hf_intake_val_text(homefax_obj.get("component")):
            warnings.append(f"{label} ({finding_id}) homefax.component is blank")

        candidate_image_urls = evidence_obj.get("candidate_image_urls")
        if not isinstance(candidate_image_urls, list):
            errors.append(f"{label} ({finding_id}) evidence.candidate_image_urls must be an array")
        else:
            counts["findings_with_candidate_images_array"] += 1

        image_status = _hf_intake_val_text(evidence_obj.get("image_match_status")) or "none"
        verified_image_url = _hf_intake_val_text(evidence_obj.get("verified_image_url"))

        if image_status not in allowed_image_statuses:
            errors.append(f"{label} ({finding_id}) invalid evidence.image_match_status: {image_status}")

        if verified_image_url:
            counts["findings_with_verified_images"] += 1

        if verified_image_url and image_status != "verified":
            errors.append(
                f"{label} ({finding_id}) verified_image_url must be blank unless image_match_status is verified"
            )

        homeowner_decision = _hf_intake_val_text(review_state.get("homeowner_decision")) or "unreviewed"

        if homeowner_decision not in allowed_homeowner_decisions:
            errors.append(f"{label} ({finding_id}) invalid review_state.homeowner_decision: {homeowner_decision}")

        if homeowner_decision != "unreviewed":
            counts["findings_ready_for_review"] += 1

        final_status = _hf_intake_val_text(admin_state.get("final_approval_status")) or "not_approved"
        baseline_locked = _hf_intake_val_bool(admin_state.get("baseline_locked"), False)

        if final_status not in allowed_final_statuses:
            errors.append(f"{label} ({finding_id}) invalid admin_state.final_approval_status: {final_status}")

        if baseline_locked:
            counts["findings_baseline_locked"] += 1

        if baseline_locked and final_status != "approved":
            errors.append(
                f"{label} ({finding_id}) baseline_locked cannot be true unless final_approval_status is approved"
            )

        alert_status = _hf_intake_val_text(monitoring.get("alert_status")) or "none"
        allowed_alert_statuses = {
            "none",
            "active",
            "sent",
            "acknowledged",
            "resolved",
            "suppressed",
        }

        if alert_status not in allowed_alert_statuses:
            warnings.append(f"{label} ({finding_id}) unknown monitoring.alert_status: {alert_status}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
    }


@app.get("/homefax-intake-standard-validation-health")
def homefax_intake_standard_validation_health():
    """
    Health check for HomeFax Intake Standard Validation Endpoint Pass 1.
    """
    return {
        "success": True,
        "service": "homefax_intake_standard_validation",
        "version": "1.0",
        "endpoints": [
            "POST /homefax-intake-standard/validate-payload"
        ],
        "writes_to_database": False,
        "calls_n8n": False,
        "status": "ready",
    }


@app.post("/homefax-intake-standard/validate-payload")
def homefax_intake_standard_validate_payload(payload: dict):
    """
    Validate a submitted HomeFax Intake Standard v1 payload.

    This endpoint is intentionally read-only.
    """
    result = _hf_intake_validate_payload(payload)

    return {
        "success": True,
        "validator_version": "homefax_intake_standard_validator_v1",
        "payload_valid": result.get("valid"),
        "errors_count": len(result.get("errors", [])),
        "warnings_count": len(result.get("warnings", [])),
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
        "counts": result.get("counts", {}),
    }


# ============================================================
# HomeFax Monitoring Lifecycle Backend Pass 1
#
# Purpose:
# - Create durable monitoring plans from locked HomeFax issues.
# - Accept normalized device/manual monitoring events.
# - Keep the device system future-proof by using provider/capability/event records.
#
# Product rules:
# - Admin verifies HomeFax record quality.
# - Admin does not manage contractor decisions.
# - All devices feed monitoring events.
# - Device data becomes HomeFax monitoring timeline data, not a separate dashboard island.
#
# New endpoints:
# GET  /monitoring-lifecycle-health
# POST /monitoring-lifecycle/init
# POST /monitoring-plans/from-issue/{issue_id}
# GET  /monitoring-plans/{record_id}
# POST /integration-events/mock
# GET  /monitoring-events/{record_id}
# ============================================================

import json as _hf_mon_json
import datetime as _hf_mon_datetime
from typing import Any as _hf_mon_Any
from typing import Dict as _hf_mon_Dict
from typing import List as _hf_mon_List
from typing import Optional as _hf_mon_Optional

from fastapi import HTTPException as _hf_mon_HTTPException
from pydantic import BaseModel as _hf_mon_BaseModel


def _hf_mon_safe_text(value) -> str:
    return str(value or "").replace("\x00", " ").strip()


def _hf_mon_one_line(value) -> str:
    return " ".join(_hf_mon_safe_text(value).split())


def _hf_mon_to_json(value) -> str:
    if value is None:
        value = []
    return _hf_mon_json.dumps(value, ensure_ascii=False)


def _hf_mon_parse_json(value, fallback=None):
    if fallback is None:
        fallback = []

    if value is None:
        return fallback

    if isinstance(value, (list, dict)):
        return value

    text = str(value).strip()

    if not text:
        return fallback

    try:
        return _hf_mon_json.loads(text)
    except Exception:
        return fallback


def _hf_mon_now_string() -> str:
    return _hf_mon_datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _hf_mon_get_connection():
    """
    Reuse whichever DB connection helper exists in the current main.py.
    This keeps the patch compatible with the existing backend structure.
    """

    for name in (
        "_hf_report_db_connection",
        "_hf_loc_get_connection",
        "get_db_connection",
        "get_connection",
        "db_connection",
    ):
        fn = globals().get(name)
        if callable(fn):
            return fn()

    raise RuntimeError("No database connection helper found for monitoring lifecycle.")


def _hf_mon_rows_as_dicts(cursor, rows):
    if not rows:
        return []

    first = rows[0]

    if isinstance(first, dict):
        return rows

    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def _hf_mon_fetch_all(sql: str, params=None):
    conn = _hf_mon_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            rows = cursor.fetchall() or []
            return _hf_mon_rows_as_dicts(cursor, rows)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_mon_fetch_one(sql: str, params=None):
    rows = _hf_mon_fetch_all(sql, params)
    return rows[0] if rows else None


def _hf_mon_execute(sql: str, params=None):
    conn = _hf_mon_get_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            last_id = getattr(cursor, "lastrowid", None)
        conn.commit()
        return last_id
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hf_mon_try_execute(sql: str, params=None):
    try:
        return {
            "ok": True,
            "last_id": _hf_mon_execute(sql, params),
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "last_id": None,
            "error": str(exc),
        }


def _hf_mon_ensure_schema():
    """
    Create monitoring tables and add optional monitoring fields to verified_issues.
    This endpoint is safe to call repeatedly.
    """

    results = []

    ddl_statements = [
        """
        CREATE TABLE IF NOT EXISTS monitoring_plans (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          tenant_id VARCHAR(128) NOT NULL DEFAULT 'lateef-home-inspection',
          property_id VARCHAR(128) DEFAULT '',
          record_id VARCHAR(255) NOT NULL,
          source_issue_id BIGINT NOT NULL,
          `system` VARCHAR(255) DEFAULT '',
          component VARCHAR(255) DEFAULT '',
          location VARCHAR(255) DEFAULT '',
          risk_type VARCHAR(128) DEFAULT '',
          allowed_capabilities JSON NULL,
          monitoring_plan_text TEXT NULL,
          status VARCHAR(64) DEFAULT 'active',
          created_from VARCHAR(128) DEFAULT 'admin_final_lock',
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uniq_monitoring_source_issue (source_issue_id),
          INDEX idx_monitoring_record_id (record_id),
          INDEX idx_monitoring_status (status),
          INDEX idx_monitoring_risk_type (risk_type)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_integrations (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          tenant_id VARCHAR(128) NOT NULL DEFAULT 'lateef-home-inspection',
          user_id VARCHAR(128) DEFAULT '',
          property_id VARCHAR(128) DEFAULT '',
          provider VARCHAR(128) NOT NULL,
          provider_display_name VARCHAR(255) DEFAULT '',
          connection_type VARCHAR(64) DEFAULT '',
          capabilities JSON NULL,
          status VARCHAR(64) DEFAULT 'disconnected',
          access_token_encrypted TEXT NULL,
          refresh_token_encrypted TEXT NULL,
          last_sync_at TIMESTAMP NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX idx_user_integrations_property (property_id),
          INDEX idx_user_integrations_provider (provider),
          INDEX idx_user_integrations_status (status)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS property_device_locations (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          tenant_id VARCHAR(128) NOT NULL DEFAULT 'lateef-home-inspection',
          property_id VARCHAR(128) DEFAULT '',
          provider VARCHAR(128) DEFAULT '',
          device_id VARCHAR(255) DEFAULT '',
          device_name VARCHAR(255) DEFAULT '',
          location VARCHAR(255) DEFAULT '',
          `system` VARCHAR(255) DEFAULT '',
          component VARCHAR(255) DEFAULT '',
          capabilities JSON NULL,
          status VARCHAR(64) DEFAULT 'active',
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX idx_device_locations_property (property_id),
          INDEX idx_device_locations_provider_device (provider, device_id),
          INDEX idx_device_locations_status (status)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS integration_events (
          id BIGINT AUTO_INCREMENT PRIMARY KEY,
          tenant_id VARCHAR(128) NOT NULL DEFAULT 'lateef-home-inspection',
          property_id VARCHAR(128) DEFAULT '',
          record_id VARCHAR(255) DEFAULT '',
          user_integration_id BIGINT NULL,
          monitoring_plan_id BIGINT NULL,
          source_issue_id BIGINT NULL,
          source_type VARCHAR(64) NOT NULL DEFAULT 'device_event',
          provider VARCHAR(128) DEFAULT '',
          device_id VARCHAR(255) DEFAULT '',
          device_name VARCHAR(255) DEFAULT '',
          capability VARCHAR(128) DEFAULT '',
          `system` VARCHAR(255) DEFAULT '',
          component VARCHAR(255) DEFAULT '',
          location VARCHAR(255) DEFAULT '',
          title VARCHAR(255) DEFAULT '',
          summary TEXT NULL,
          severity VARCHAR(64) DEFAULT 'info',
          event_status VARCHAR(64) DEFAULT 'unreviewed',
          homeowner_acknowledged VARCHAR(16) DEFAULT 'no',
          homeowner_note TEXT NULL,
          raw_payload JSON NULL,
          occurred_at TIMESTAMP NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX idx_integration_events_record (record_id),
          INDEX idx_integration_events_plan (monitoring_plan_id),
          INDEX idx_integration_events_issue (source_issue_id),
          INDEX idx_integration_events_capability (capability),
          INDEX idx_integration_events_status (event_status)
        )
        """,
    ]

    for statement in ddl_statements:
        results.append(_hf_mon_try_execute(statement))

    # Optional workflow columns on verified_issues.
    # Duplicate-column errors are safe and expected after first run.
    optional_alters = [
        "ALTER TABLE verified_issues ADD COLUMN admin_decision VARCHAR(128) NULL",
        "ALTER TABLE verified_issues ADD COLUMN monitoring_required VARCHAR(16) DEFAULT 'no'",
        "ALTER TABLE verified_issues ADD COLUMN monitoring_plan_id BIGINT NULL",
    ]

    for statement in optional_alters:
        result = _hf_mon_try_execute(statement)

        if not result["ok"] and "Duplicate column" in result["error"]:
            result["ok"] = True
            result["error"] = "already_exists"

        results.append(result)

    return results


def _hf_mon_get_issue(issue_id: int):
    issue = _hf_mon_fetch_one(
        "SELECT * FROM verified_issues WHERE id = %s LIMIT 1",
        (int(issue_id),),
    )

    if not issue:
        raise _hf_mon_HTTPException(status_code=404, detail=f"Verified issue {issue_id} not found.")

    return issue


def _hf_mon_normalized_decision(issue: dict) -> str:
    return _hf_mon_one_line(
        issue.get("homeowner_decision")
        or issue.get("homeowner_review_decision")
        or ""
    ).lower()


def _hf_mon_issue_should_monitor(issue: dict, force: bool = False) -> bool:
    if force:
        return True

    decision = _hf_mon_normalized_decision(issue)
    current_status = _hf_mon_one_line(issue.get("current_status") or issue.get("status")).lower()
    monitoring_required = _hf_mon_one_line(issue.get("monitoring_required")).lower()

    return (
        decision in {"monitor", "monitor_this", "monitoring"}
        or current_status == "monitoring"
        or monitoring_required in {"yes", "true", "1"}
    )


def _hf_mon_infer_system(issue: dict) -> str:
    return _hf_mon_one_line(
        issue.get("standard_system")
        or issue.get("system")
        or issue.get("section")
        or issue.get("source_report_section")
        or ""
    )


def _hf_mon_infer_component(issue: dict) -> str:
    return _hf_mon_one_line(
        issue.get("standard_component")
        or issue.get("component")
        or issue.get("source_report_section")
        or ""
    )


def _hf_mon_infer_location(issue: dict) -> str:
    return _hf_mon_one_line(
        issue.get("standard_location_area")
        or issue.get("location")
        or issue.get("area")
        or issue.get("room")
        or issue.get("source_report_section")
        or ""
    )


def _hf_mon_infer_monitoring_text(issue: dict) -> str:
    return _hf_mon_one_line(
        issue.get("standard_monitoring_plan")
        or issue.get("monitoring_plan")
        or "Monitor this issue for changes, recurrence, worsening conditions, or related device alerts."
    )


def _hf_mon_infer_risk_type(issue: dict) -> str:
    haystack = " ".join([
        _hf_mon_one_line(issue.get("title")).lower(),
        _hf_mon_one_line(issue.get("summary")).lower(),
        _hf_mon_infer_system(issue).lower(),
        _hf_mon_infer_component(issue).lower(),
        _hf_mon_infer_location(issue).lower(),
    ])

    if any(token in haystack for token in ["leak", "water", "plumbing", "moisture", "sump", "drain"]):
        return "water_moisture"

    if any(token in haystack for token in ["mold", "humidity", "air quality", "radon", "iaq", "co2", "voc"]):
        return "indoor_air_quality"

    if any(token in haystack for token in ["electrical", "gfci", "breaker", "panel", "wire", "outlet"]):
        return "electrical"

    if any(token in haystack for token in ["hvac", "furnace", "air conditioner", "thermostat", "heating", "cooling"]):
        return "hvac"

    if any(token in haystack for token in ["foundation", "settlement", "crack", "structural", "soil"]):
        return "structural"

    if any(token in haystack for token in ["roof", "gutter", "downspout", "flashing", "wind", "rain"]):
        return "weather_envelope"

    return "general_monitoring"


def _hf_mon_allowed_capabilities(risk_type: str):
    mapping = {
        "water_moisture": ["WATER_LEAK", "WATER_SHUTOFF", "MOISTURE", "HUMIDITY", "PHOTO_EVIDENCE"],
        "indoor_air_quality": ["HUMIDITY", "MOLD_RISK", "RADON", "CO2", "VOC", "AIR_QUALITY", "PHOTO_EVIDENCE"],
        "electrical": ["ELECTRICAL_LOAD", "ELECTRICAL_ANOMALY", "PHOTO_EVIDENCE"],
        "hvac": ["TEMPERATURE", "THERMAL", "HVAC_RUNTIME", "HUMIDITY", "PHOTO_EVIDENCE"],
        "structural": ["FOUNDATION_MOVEMENT", "SOIL_MOISTURE", "PHOTO_EVIDENCE", "VIDEO_EVIDENCE"],
        "weather_envelope": ["WEATHER_RAIN", "WEATHER_WIND", "MOISTURE", "PHOTO_EVIDENCE"],
        "general_monitoring": ["PHOTO_EVIDENCE", "DOCUMENT_EVIDENCE", "MANUAL_CHECK"],
    }

    return mapping.get(risk_type, mapping["general_monitoring"])


def _hf_mon_find_plan_by_issue(issue_id: int):
    return _hf_mon_fetch_one(
        """
        SELECT *
        FROM monitoring_plans
        WHERE source_issue_id = %s
        LIMIT 1
        """,
        (int(issue_id),),
    )


def _hf_mon_find_plan_by_id(plan_id: int):
    return _hf_mon_fetch_one(
        """
        SELECT *
        FROM monitoring_plans
        WHERE id = %s
        LIMIT 1
        """,
        (int(plan_id),),
    )


def _hf_mon_create_or_update_plan_from_issue(issue_id: int, force: bool = False):
    _hf_mon_ensure_schema()

    issue = _hf_mon_get_issue(issue_id)

    if not _hf_mon_issue_should_monitor(issue, force=force):
        raise _hf_mon_HTTPException(
            status_code=409,
            detail="This issue is not marked for monitoring. Use force=true only for admin backfill or repair.",
        )

    record_id = _hf_mon_one_line(issue.get("record_id"))
    tenant_id = _hf_mon_one_line(issue.get("tenant_id") or "lateef-home-inspection")
    property_id = _hf_mon_one_line(issue.get("property_id") or "")
    system = _hf_mon_infer_system(issue)
    component = _hf_mon_infer_component(issue)
    location = _hf_mon_infer_location(issue)
    risk_type = _hf_mon_infer_risk_type(issue)
    allowed_capabilities = _hf_mon_allowed_capabilities(risk_type)
    monitoring_plan_text = _hf_mon_infer_monitoring_text(issue)

    _hf_mon_execute(
        """
        INSERT INTO monitoring_plans (
          tenant_id,
          property_id,
          record_id,
          source_issue_id,
          `system`,
          component,
          location,
          risk_type,
          allowed_capabilities,
          monitoring_plan_text,
          status,
          created_from
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, 'active', 'admin_final_lock')
        ON DUPLICATE KEY UPDATE
          tenant_id = VALUES(tenant_id),
          property_id = VALUES(property_id),
          record_id = VALUES(record_id),
          `system` = VALUES(`system`),
          component = VALUES(component),
          location = VALUES(location),
          risk_type = VALUES(risk_type),
          allowed_capabilities = VALUES(allowed_capabilities),
          monitoring_plan_text = VALUES(monitoring_plan_text),
          status = 'active',
          updated_at = CURRENT_TIMESTAMP
        """,
        (
            tenant_id,
            property_id,
            record_id,
            int(issue_id),
            system,
            component,
            location,
            risk_type,
            _hf_mon_to_json(allowed_capabilities),
            monitoring_plan_text,
        ),
    )

    plan = _hf_mon_find_plan_by_issue(issue_id)

    if plan:
        _hf_mon_try_execute(
            """
            UPDATE verified_issues
            SET
              monitoring_required = 'yes',
              monitoring_plan_id = %s,
              current_status = CASE
                WHEN COALESCE(current_status, '') IN ('', 'open', 'active') THEN 'monitoring'
                ELSE current_status
              END,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (plan["id"], int(issue_id)),
        )

    return {
        "issue": issue,
        "plan": plan,
        "allowed_capabilities": allowed_capabilities,
    }


class _HFMonCreatePlanRequest(_hf_mon_BaseModel):
    force: bool = False


class _HFMonMockEventRequest(_hf_mon_BaseModel):
    record_id: _hf_mon_Optional[str] = ""
    monitoring_plan_id: _hf_mon_Optional[int] = None
    source_issue_id: _hf_mon_Optional[int] = None
    tenant_id: _hf_mon_Optional[str] = "lateef-home-inspection"
    property_id: _hf_mon_Optional[str] = ""
    source_type: _hf_mon_Optional[str] = "device_event"
    provider: _hf_mon_Optional[str] = "manual"
    device_id: _hf_mon_Optional[str] = ""
    device_name: _hf_mon_Optional[str] = ""
    capability: _hf_mon_Optional[str] = "MANUAL_CHECK"
    system: _hf_mon_Optional[str] = ""
    component: _hf_mon_Optional[str] = ""
    location: _hf_mon_Optional[str] = ""
    title: _hf_mon_Optional[str] = "Manual monitoring event"
    summary: _hf_mon_Optional[str] = ""
    severity: _hf_mon_Optional[str] = "info"
    event_status: _hf_mon_Optional[str] = "unreviewed"
    homeowner_acknowledged: _hf_mon_Optional[str] = "no"
    homeowner_note: _hf_mon_Optional[str] = ""
    raw_payload: _hf_mon_Optional[_hf_mon_Dict[str, _hf_mon_Any]] = None
    occurred_at: _hf_mon_Optional[str] = None




class _HFMonitoringEventReviewPayload(BaseModel):
    event_status: str | None = None
    review_decision: str | None = None
    review_note: str | None = None
    reviewed_by: str | None = "admin"
    followup_required: bool | None = None



# Monitoring Event Review Backend Pass 1
def _hf_mon_ensure_event_review_schema():
    """
    Adds review/audit fields to integration_events.

    MySQL-compatible.
    These fields let HomeFax review monitoring/device/weather/manual events
    without mutating the locked verified issue baseline.
    """
    statements = [
        "ALTER TABLE integration_events ADD COLUMN review_decision VARCHAR(128) NULL",
        "ALTER TABLE integration_events ADD COLUMN review_note TEXT NULL",
        "ALTER TABLE integration_events ADD COLUMN reviewed_by VARCHAR(128) NULL",
        "ALTER TABLE integration_events ADD COLUMN reviewed_at TIMESTAMP NULL",
        "ALTER TABLE integration_events ADD COLUMN resolved_at TIMESTAMP NULL",
        "ALTER TABLE integration_events ADD COLUMN escalated_at TIMESTAMP NULL",
        "ALTER TABLE integration_events ADD COLUMN followup_required BOOLEAN DEFAULT FALSE",
    ]

    results = []

    for statement in statements:
        result = _hf_mon_try_execute(statement)

        if not result.get("ok") and "Duplicate column" in str(result.get("error")):
            result["ok"] = True
            result["error"] = "already_exists"

        results.append(result)

    failed = [item for item in results if not item.get("ok")]

    if failed:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "monitoring_event_review_schema_failed",
                "failed": failed,
            },
        )

    return results





# ============================================================
# Device Event Intelligence Backend Pass 1
#
# Purpose:
# - Ingest homeowner-owned device/weather/sensor events.
# - Normalize provider-specific data into HomeFax capabilities.
# - Auto-match events to property systems, findings, and monitoring plans.
# - Generate homeowner-ready compiled insights.
# - Keep admin out of normal device telemetry review.
#
# New endpoints:
# POST  /device-events/ingest
# GET   /device-events/{record_id}/insights
# PATCH /device-event/{event_id}/homeowner-confirmation
# ============================================================

class _HFDeviceEventIngestPayload(BaseModel):
    tenant_id: str | None = "lateef-home-inspection"
    property_id: str | None = ""
    record_id: str
    provider: str
    provider_event_id: str | None = ""
    source_type: str | None = "device_event"
    device_id: str | None = ""
    device_name: str | None = ""
    capability: str | None = ""
    severity: str | None = "info"
    system: str | None = ""
    component: str | None = ""
    location: str | None = ""
    title: str | None = ""
    summary: str | None = ""
    raw_payload: dict | list | str | None = None
    occurred_at: str | None = None


class _HFDeviceEventHomeownerConfirmationPayload(BaseModel):
    homeowner_confirmation_status: str
    homeowner_note: str | None = ""
    homeowner_acknowledged: str | None = "yes"


def _hf_device_json_dumps(value):
    try:
        import json

        if value is None:
            return "{}"

        if isinstance(value, str):
            return value

        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


def _hf_device_normalize(value) -> str:
    return str(value or "").strip()


def _hf_device_lower(value) -> str:
    return _hf_device_normalize(value).lower()


def _hf_device_infer_capability(payload: _HFDeviceEventIngestPayload) -> str:
    explicit = _hf_device_normalize(payload.capability).upper()

    if explicit:
        return explicit

    haystack = " ".join([
        _hf_device_lower(payload.provider),
        _hf_device_lower(payload.device_name),
        _hf_device_lower(payload.title),
        _hf_device_lower(payload.summary),
        _hf_device_json_dumps(payload.raw_payload).lower(),
    ])

    if any(token in haystack for token in ["leak", "water_detected", "water detected", "moisture"]):
        return "WATER_LEAK"

    if any(token in haystack for token in ["humidity", "mold", "damp", "iaq"]):
        return "HUMIDITY"

    if any(token in haystack for token in ["ting", "voltage", "electrical", "arc", "power", "breaker"]):
        return "ELECTRICAL_ANOMALY"

    if any(token in haystack for token in ["thermostat", "hvac", "runtime", "temperature"]):
        return "HVAC_RUNTIME"

    if any(token in haystack for token in ["rain", "storm", "wind", "weather"]):
        return "WEATHER_RAIN"

    return "GENERAL_DEVICE_EVENT"


def _hf_device_infer_system(capability: str, payload: _HFDeviceEventIngestPayload) -> str:
    explicit = _hf_device_normalize(payload.system)

    if explicit:
        return explicit

    cap = _hf_device_normalize(capability).upper()

    if cap in {"WATER_LEAK", "MOISTURE"}:
        return "Plumbing"

    if cap in {"ELECTRICAL_ANOMALY", "ELECTRICAL_LOAD"}:
        return "Electrical"

    if cap in {"HVAC_RUNTIME", "TEMPERATURE"}:
        return "HVAC"

    if cap in {"HUMIDITY", "MOLD_RISK", "AIR_QUALITY", "VOC", "CO2", "RADON"}:
        return "Indoor Air Quality"

    if cap in {"WEATHER_RAIN", "WEATHER_WIND", "WEATHER_DROUGHT"}:
        return "Weather"

    return ""


def _hf_device_infer_title(capability: str, payload: _HFDeviceEventIngestPayload) -> str:
    explicit = _hf_device_normalize(payload.title)

    if explicit:
        return explicit

    labels = {
        "WATER_LEAK": "Water leak monitoring event",
        "MOISTURE": "Moisture monitoring event",
        "ELECTRICAL_ANOMALY": "Electrical monitoring event",
        "HVAC_RUNTIME": "HVAC monitoring event",
        "TEMPERATURE": "Temperature monitoring event",
        "HUMIDITY": "Humidity monitoring event",
        "MOLD_RISK": "Mold risk monitoring event",
        "WEATHER_RAIN": "Weather rain risk event",
        "WEATHER_WIND": "Weather wind risk event",
        "GENERAL_DEVICE_EVENT": "Device monitoring event",
    }

    return labels.get(_hf_device_normalize(capability).upper(), "Device monitoring event")


def _hf_device_find_matching_plan(record_id: str, capability: str, system_name: str):
    plans = _hf_mon_fetch_all(
        """
        SELECT *
        FROM monitoring_plans
        WHERE record_id = %s
        ORDER BY id ASC
        """,
        (_hf_mon_one_line(record_id),),
    )

    cap = _hf_device_normalize(capability).upper()
    system_lower = _hf_device_lower(system_name)

    best_plan = None
    best_score = 0
    best_reason = ""

    for plan in plans:
        allowed = plan.get("allowed_capabilities") or ""
        risk_type = _hf_device_lower(plan.get("risk_type"))
        plan_text = " ".join([
            _hf_device_lower(plan.get("title")),
            _hf_device_lower(plan.get("monitoring_plan_text")),
            risk_type,
            _hf_device_lower(allowed),
        ])

        score = 0
        reasons = []

        if cap and cap in str(allowed).upper():
            score += 60
            reasons.append(f"capability {cap} is allowed by monitoring plan")

        if system_lower and system_lower in plan_text:
            score += 15
            reasons.append(f"system {system_name} appears in plan context")

        if cap in {"WATER_LEAK", "MOISTURE"} and any(token in plan_text for token in ["water", "moisture", "leak", "plumbing"]):
            score += 25
            reasons.append("water/moisture event matched water-related monitoring context")

        if cap == "ELECTRICAL_ANOMALY" and any(token in plan_text for token in ["electrical", "gfci", "breaker", "panel"]):
            score += 25
            reasons.append("electrical event matched electrical monitoring context")

        if cap in {"WEATHER_RAIN", "WEATHER_WIND"} and any(token in plan_text for token in ["roof", "weather", "rain", "wind", "foundation", "drainage"]):
            score += 25
            reasons.append("weather event matched weather-sensitive monitoring context")

        if score > best_score:
            best_plan = plan
            best_score = score
            best_reason = "; ".join(reasons)

    if not best_plan:
        return None, 0.0, "No matching monitoring plan found."

    confidence = min(round(best_score / 100, 2), 0.99)

    return best_plan, confidence, best_reason or "Monitoring plan matched by rules engine."


def _hf_device_find_related_issue_ids(record_id: str, capability: str, system_name: str):
    cap = _hf_device_normalize(capability).upper()
    system_lower = _hf_device_lower(system_name)

    issue_rows = _hf_mon_fetch_all(
        """
        SELECT id, title, section, summary, current_status, baseline_locked, final_approval_status
        FROM verified_issues
        WHERE record_id = %s
        ORDER BY id ASC
        """,
        (_hf_mon_one_line(record_id),),
    )

    matches = []

    for issue in issue_rows:
        haystack = " ".join([
            _hf_device_lower(issue.get("title")),
            _hf_device_lower(issue.get("section")),
            _hf_device_lower(issue.get("summary")),
            _hf_device_lower(issue.get("current_status")),
        ])

        score = 0

        if system_lower and system_lower in haystack:
            score += 20

        if cap in {"WATER_LEAK", "MOISTURE"} and any(token in haystack for token in ["leak", "water", "plumbing", "moisture", "drain"]):
            score += 50

        if cap == "ELECTRICAL_ANOMALY" and any(token in haystack for token in ["electrical", "gfci", "breaker", "panel", "wire", "outlet"]):
            score += 50

        if cap in {"HUMIDITY", "MOLD_RISK"} and any(token in haystack for token in ["mold", "humidity", "attic", "crawl", "ventilation"]):
            score += 50

        if cap in {"HVAC_RUNTIME", "TEMPERATURE"} and any(token in haystack for token in ["hvac", "heating", "cooling", "furnace", "thermostat"]):
            score += 50

        if score > 0:
            matches.append((score, issue.get("id")))

    matches.sort(reverse=True)

    return [issue_id for _, issue_id in matches[:5] if issue_id is not None]


def _hf_device_compile_insight(payload: _HFDeviceEventIngestPayload, capability: str, system_name: str, plan, confidence: float, related_issue_ids):
    cap_label = capability.replace("_", " ").title()
    system_label = system_name or "home system"

    if capability == "WATER_LEAK":
        title = "Water leak monitoring detected an event"
        action = "Inspect the related area and upload a follow-up photo if moisture is present."
    elif capability == "ELECTRICAL_ANOMALY":
        title = "Electrical monitoring detected an anomaly"
        action = "Review the provider alert. If alerts repeat or the provider recommends service, contact a licensed electrician."
    elif capability in {"HUMIDITY", "MOLD_RISK"}:
        title = "Indoor air or humidity monitoring detected a concern"
        action = "Check the affected area for moisture, odor, staining, or ventilation problems."
    elif capability in {"HVAC_RUNTIME", "TEMPERATURE"}:
        title = "HVAC or temperature monitoring detected a change"
        action = "Check comfort, thermostat settings, filter status, and recent HVAC behavior."
    elif capability.startswith("WEATHER_"):
        title = "Weather risk detected for a monitored home condition"
        action = "Review related monitoring plans and check vulnerable areas after the weather event."
    else:
        title = f"{cap_label} event received"
        action = "Review this device event and confirm whether it is relevant to your home."

    if plan:
        summary = (
            f"HomeFax matched this {cap_label.lower()} event to an active monitoring plan "
            f"for {system_label}. Match confidence: {confidence}."
        )
    elif related_issue_ids:
        summary = (
            f"HomeFax matched this {cap_label.lower()} event to related HomeFax findings "
            f"for {system_label}. Match confidence: {confidence}."
        )
    else:
        summary = (
            f"HomeFax received this {cap_label.lower()} event for the property. "
            "No high-confidence monitoring plan match was found yet."
        )

    return title, summary, action


def _hf_device_ensure_intelligence_schema():
    statements = [
        "ALTER TABLE integration_events ADD COLUMN provider_event_id VARCHAR(255) NULL",
        "ALTER TABLE integration_events ADD COLUMN match_status VARCHAR(64) DEFAULT 'unmatched'",
        "ALTER TABLE integration_events ADD COLUMN match_confidence DECIMAL(5,2) DEFAULT 0",
        "ALTER TABLE integration_events ADD COLUMN matched_by VARCHAR(64) DEFAULT 'rules_engine'",
        "ALTER TABLE integration_events ADD COLUMN match_reason TEXT NULL",
        "ALTER TABLE integration_events ADD COLUMN matched_issue_ids_json JSON NULL",
        "ALTER TABLE integration_events ADD COLUMN homeowner_confirmation_status VARCHAR(64) DEFAULT 'not_required'",
        "ALTER TABLE integration_events ADD COLUMN event_lifecycle_status VARCHAR(64) DEFAULT 'compiled'",
        "ALTER TABLE integration_events ADD COLUMN alert_status VARCHAR(64) DEFAULT 'not_sent'",
        "ALTER TABLE integration_events ADD COLUMN compiled_insight_title VARCHAR(255) NULL",
        "ALTER TABLE integration_events ADD COLUMN compiled_insight_summary TEXT NULL",
        "ALTER TABLE integration_events ADD COLUMN recommended_homeowner_action TEXT NULL",
    ]

    results = []

    for statement in statements:
        result = _hf_mon_try_execute(statement)

        if not result.get("ok") and "Duplicate column" in str(result.get("error")):
            result["ok"] = True
            result["error"] = "already_exists"

        results.append(result)

    failed = [item for item in results if not item.get("ok")]

    if failed:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "device_event_intelligence_schema_failed",
                "failed": failed,
            },
        )

    return results


@app.post("/device-events/ingest")
def ingest_device_event(payload: _HFDeviceEventIngestPayload):
    """
    Ingest and automatically compile a homeowner device/weather/sensor event.

    This is not an admin review workflow. HomeFax normalizes, classifies,
    auto-matches, scores, and creates a homeowner-ready insight.
    """
    _hf_mon_ensure_schema()
    _hf_device_ensure_intelligence_schema()

    record_id = _hf_device_normalize(payload.record_id)

    if not record_id:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "record_id_required",
                "message": "record_id is required.",
            },
        )

    provider = _hf_device_lower(payload.provider)

    if not provider:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "provider_required",
                "message": "provider is required.",
            },
        )

    capability = _hf_device_infer_capability(payload)
    system_name = _hf_device_infer_system(capability, payload)
    title = _hf_device_infer_title(capability, payload)
    severity = _hf_device_lower(payload.severity) or "info"

    matched_plan, confidence, match_reason = _hf_device_find_matching_plan(record_id, capability, system_name)
    related_issue_ids = _hf_device_find_related_issue_ids(record_id, capability, system_name)

    monitoring_plan_id = matched_plan.get("id") if matched_plan else None
    source_issue_id = matched_plan.get("source_issue_id") if matched_plan else (related_issue_ids[0] if related_issue_ids else None)

    if matched_plan:
        match_status = "auto_matched"
    elif related_issue_ids:
        match_status = "auto_matched"
        confidence = max(confidence, 0.65)
        if match_reason == "No matching monitoring plan found.":
            match_reason = "Matched to related verified issue by rules engine."
    else:
        match_status = "unmatched"
        confidence = 0.0

    homeowner_confirmation_status = "pending" if severity in {"high", "critical"} or confidence < 0.75 else "not_required"
    event_lifecycle_status = "compiled"
    alert_status = "ready" if severity in {"high", "critical"} else "not_sent"

    compiled_title, compiled_summary, recommended_action = _hf_device_compile_insight(
        payload,
        capability,
        system_name,
        matched_plan,
        confidence,
        related_issue_ids,
    )

    raw_payload = _hf_device_json_dumps(payload.raw_payload)
    matched_issue_ids_json = _hf_device_json_dumps(related_issue_ids)

    conn = None

    try:
        conn = _hf_mon_get_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO integration_events (
                  tenant_id,
                  property_id,
                  record_id,
                  user_integration_id,
                  monitoring_plan_id,
                  source_issue_id,
                  source_type,
                  provider,
                  provider_event_id,
                  device_id,
                  device_name,
                  capability,
                  `system`,
                  component,
                  location,
                  title,
                  summary,
                  severity,
                  event_status,
                  homeowner_acknowledged,
                  homeowner_note,
                  raw_payload,
                  occurred_at,
                  match_status,
                  match_confidence,
                  matched_by,
                  match_reason,
                  matched_issue_ids_json,
                  homeowner_confirmation_status,
                  event_lifecycle_status,
                  alert_status,
                  compiled_insight_title,
                  compiled_insight_summary,
                  recommended_homeowner_action
                )
                VALUES (
                  %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  CAST(%s AS JSON),
                  %s,
                  %s, %s, %s, %s,
                  CAST(%s AS JSON),
                  %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    _hf_device_normalize(payload.tenant_id) or "lateef-home-inspection",
                    _hf_device_normalize(payload.property_id),
                    record_id,
                    monitoring_plan_id,
                    source_issue_id,
                    _hf_device_normalize(payload.source_type) or "device_event",
                    provider,
                    _hf_device_normalize(payload.provider_event_id),
                    _hf_device_normalize(payload.device_id),
                    _hf_device_normalize(payload.device_name),
                    capability,
                    system_name,
                    _hf_device_normalize(payload.component),
                    _hf_device_normalize(payload.location),
                    title,
                    _hf_device_normalize(payload.summary) or compiled_summary,
                    severity,
                    "compiled",
                    "no",
                    "",
                    raw_payload,
                    _hf_device_normalize(payload.occurred_at) or None,
                    match_status,
                    confidence,
                    "rules_engine",
                    match_reason,
                    matched_issue_ids_json,
                    homeowner_confirmation_status,
                    event_lifecycle_status,
                    alert_status,
                    compiled_title,
                    compiled_summary,
                    recommended_action,
                ),
            )

            event_id = cursor.lastrowid

            cursor.execute(
                """
                SELECT *
                FROM integration_events
                WHERE id = %s
                LIMIT 1
                """,
                (event_id,),
            )
            row = cursor.fetchone()

        conn.commit()

        return {
            "success": True,
            "message": "Device event ingested and compiled by HomeFax.",
            "event": row,
            "intelligence": {
                "match_status": match_status,
                "match_confidence": float(confidence),
                "matched_by": "rules_engine",
                "match_reason": match_reason,
                "matched_issue_ids": related_issue_ids,
                "monitoring_plan_id": monitoring_plan_id,
                "source_issue_id": source_issue_id,
                "homeowner_confirmation_status": homeowner_confirmation_status,
                "alert_status": alert_status,
                "compiled_insight_title": compiled_title,
                "compiled_insight_summary": compiled_summary,
                "recommended_homeowner_action": recommended_action,
            },
        }

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "device_event_ingest_failed",
                "message": str(exc),
            },
        )

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


# Weather Provider Duplicate Filter Pass 1
@app.get("/device-events/{record_id}/insights")
def device_event_insights_for_record(record_id: str):
    """
    Return homeowner-ready compiled device/weather/sensor insights for a record.
    """
    _hf_mon_ensure_schema()
    _hf_device_ensure_intelligence_schema()

    events = _hf_mon_fetch_all(
        """
        SELECT *
        FROM integration_events
        WHERE record_id = %s
          AND COALESCE(event_lifecycle_status, '') <> 'archived_duplicate'
          AND COALESCE(event_status, '') <> 'archived_duplicate'
        ORDER BY occurred_at DESC, id DESC
        """,
        (_hf_mon_one_line(record_id),),
    )

    for event in events:
        event["raw_payload"] = _hf_mon_parse_json(event.get("raw_payload"), {})
        event["matched_issue_ids_json"] = _hf_mon_parse_json(event.get("matched_issue_ids_json"), [])

    return {
        "success": True,
        "record_id": record_id,
        "count": len(events),
        "insights": events,
    }


@app.patch("/device-event/{event_id}/homeowner-confirmation")
def confirm_device_event_by_homeowner(event_id: int, payload: _HFDeviceEventHomeownerConfirmationPayload):
    """
    Let the homeowner confirm, deny, handle, or comment on a compiled device event.

    This is the correct human-in-the-loop step for homeowner-owned devices.
    """
    _hf_mon_ensure_schema()
    _hf_device_ensure_intelligence_schema()

    allowed_statuses = {
        "pending",
        "confirmed",
        "denied",
        "photo_uploaded",
        "handled",
        "not_relevant",
        "still_happening",
    }

    status = _hf_device_lower(payload.homeowner_confirmation_status)

    if status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "invalid_homeowner_confirmation_status",
                "message": f"Invalid homeowner_confirmation_status: {status}",
                "allowed_statuses": sorted(allowed_statuses),
            },
        )

    homeowner_acknowledged = _hf_device_normalize(payload.homeowner_acknowledged) or "yes"
    homeowner_note = _hf_device_normalize(payload.homeowner_note)

    if status in {"confirmed", "photo_uploaded", "still_happening"}:
        lifecycle_status = "acknowledged_by_homeowner"
    elif status == "handled":
        lifecycle_status = "resolved_by_homeowner"
    elif status in {"denied", "not_relevant"}:
        lifecycle_status = "archived"
    else:
        lifecycle_status = "compiled"

    conn = None

    try:
        conn = _hf_mon_get_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM integration_events
                WHERE id = %s
                LIMIT 1
                """,
                (event_id,),
            )
            existing = cursor.fetchone()

            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "success": False,
                        "error": "device_event_not_found",
                        "message": f"Device event {event_id} was not found.",
                    },
                )

            cursor.execute(
                """
                UPDATE integration_events
                SET
                    homeowner_confirmation_status = %s,
                    homeowner_acknowledged = %s,
                    homeowner_note = %s,
                    event_lifecycle_status = %s
                WHERE id = %s
                """,
                (
                    status,
                    homeowner_acknowledged,
                    homeowner_note,
                    lifecycle_status,
                    event_id,
                ),
            )

            cursor.execute(
                """
                SELECT *
                FROM integration_events
                WHERE id = %s
                LIMIT 1
                """,
                (event_id,),
            )
            row = cursor.fetchone()

        conn.commit()

        return {
            "success": True,
            "message": "Homeowner device event confirmation saved.",
            "event": row,
        }

    except HTTPException:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        raise

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "homeowner_device_event_confirmation_failed",
                "message": str(exc),
            },
        )

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass




# ============================================================
# Device Event Backfill Pass 1
#
# Purpose:
# - Reprocess older integration_events through the Device Event Intelligence layer.
# - Fill compiled homeowner insight fields for events created before intelligence existed.
# - Keep admin out of normal device telemetry review.
#
# New endpoint:
# POST /device-events/{record_id}/backfill-intelligence
# ============================================================

class _HFDeviceEventBackfillPayload(BaseModel):
    force: bool | None = False
    limit: int | None = 100


class _HFDeviceEventBackfillShim:
    """
    Small object used to reuse Device Event Intelligence helper functions
    against existing integration_events rows.
    """

    def __init__(self, row: dict):
        self.tenant_id = row.get("tenant_id") or "lateef-home-inspection"
        self.property_id = row.get("property_id") or ""
        self.record_id = row.get("record_id") or ""
        self.provider = row.get("provider") or ""
        self.provider_event_id = row.get("provider_event_id") or ""
        self.source_type = row.get("source_type") or "device_event"
        self.device_id = row.get("device_id") or ""
        self.device_name = row.get("device_name") or ""
        self.capability = row.get("capability") or ""
        self.severity = row.get("severity") or "info"
        self.system = row.get("system") or row.get("`system`") or ""
        self.component = row.get("component") or ""
        self.location = row.get("location") or ""
        self.title = row.get("title") or ""
        self.summary = row.get("summary") or ""
        self.raw_payload = row.get("raw_payload") or {}
        self.occurred_at = row.get("occurred_at")


def _hf_device_event_needs_backfill(row: dict, force: bool = False) -> bool:
    if force:
        return True

    missing_values = [
        row.get("compiled_insight_title"),
        row.get("compiled_insight_summary"),
        row.get("recommended_homeowner_action"),
        row.get("match_status"),
    ]

    if any(value is None or str(value).strip() == "" for value in missing_values):
        return True

    if str(row.get("match_status") or "").strip().lower() in {"", "unmatched"}:
        return True

    return False


def _hf_device_backfill_event_row(row: dict, force: bool = False):
    event_id = row.get("id")

    if not event_id:
        return {
            "ok": False,
            "error": "missing_event_id",
            "event_id": None,
        }

    if not _hf_device_event_needs_backfill(row, force=force):
        return {
            "ok": True,
            "skipped": True,
            "event_id": event_id,
            "reason": "already_has_intelligence",
        }

    payload = _HFDeviceEventBackfillShim(row)

    record_id = _hf_device_normalize(payload.record_id)
    capability = _hf_device_infer_capability(payload)
    system_name = _hf_device_infer_system(capability, payload)
    severity = _hf_device_lower(payload.severity) or "info"

    matched_plan, confidence, match_reason = _hf_device_find_matching_plan(record_id, capability, system_name)
    related_issue_ids = _hf_device_find_related_issue_ids(record_id, capability, system_name)

    monitoring_plan_id = matched_plan.get("id") if matched_plan else row.get("monitoring_plan_id")
    source_issue_id = (
        matched_plan.get("source_issue_id")
        if matched_plan
        else row.get("source_issue_id") or (related_issue_ids[0] if related_issue_ids else None)
    )

    if matched_plan:
        match_status = "auto_matched"
    elif related_issue_ids:
        match_status = "auto_matched"
        confidence = max(confidence, 0.65)
        if match_reason == "No matching monitoring plan found.":
            match_reason = "Matched to related verified issue by rules engine."
    else:
        match_status = "unmatched"
        confidence = 0.0

    homeowner_confirmation_status = row.get("homeowner_confirmation_status")

    if not homeowner_confirmation_status:
        homeowner_confirmation_status = (
            "pending"
            if severity in {"high", "critical"} or confidence < 0.75
            else "not_required"
        )

    event_lifecycle_status = row.get("event_lifecycle_status") or "compiled"
    alert_status = row.get("alert_status") or ("ready" if severity in {"high", "critical"} else "not_sent")

    compiled_title, compiled_summary, recommended_action = _hf_device_compile_insight(
        payload,
        capability,
        system_name,
        matched_plan,
        confidence,
        related_issue_ids,
    )

    if not compiled_summary and row.get("summary"):
        compiled_summary = row.get("summary")

    matched_issue_ids_json = _hf_device_json_dumps(related_issue_ids)

    conn = None

    try:
        conn = _hf_mon_get_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE integration_events
                SET
                    monitoring_plan_id = %s,
                    source_issue_id = %s,
                    capability = %s,
                    `system` = %s,
                    match_status = %s,
                    match_confidence = %s,
                    matched_by = %s,
                    match_reason = %s,
                    matched_issue_ids_json = CAST(%s AS JSON),
                    homeowner_confirmation_status = %s,
                    event_lifecycle_status = %s,
                    alert_status = %s,
                    compiled_insight_title = %s,
                    compiled_insight_summary = %s,
                    recommended_homeowner_action = %s
                WHERE id = %s
                """,
                (
                    monitoring_plan_id,
                    source_issue_id,
                    capability,
                    system_name,
                    match_status,
                    confidence,
                    "rules_engine",
                    match_reason,
                    matched_issue_ids_json,
                    homeowner_confirmation_status,
                    event_lifecycle_status,
                    alert_status,
                    compiled_title,
                    compiled_summary,
                    recommended_action,
                    event_id,
                ),
            )

            cursor.execute(
                """
                SELECT *
                FROM integration_events
                WHERE id = %s
                LIMIT 1
                """,
                (event_id,),
            )
            updated = cursor.fetchone()

        conn.commit()

        return {
            "ok": True,
            "skipped": False,
            "event_id": event_id,
            "provider": row.get("provider"),
            "capability": capability,
            "match_status": match_status,
            "match_confidence": float(confidence),
            "monitoring_plan_id": monitoring_plan_id,
            "source_issue_id": source_issue_id,
            "matched_issue_ids": related_issue_ids,
            "compiled_insight_title": compiled_title,
            "updated_event": updated,
        }

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        return {
            "ok": False,
            "skipped": False,
            "event_id": event_id,
            "error": str(exc),
        }

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@app.post("/device-events/{record_id}/backfill-intelligence")
def backfill_device_event_intelligence_for_record(record_id: str, payload: _HFDeviceEventBackfillPayload = _HFDeviceEventBackfillPayload()):
    """
    Backfill compiled HomeFax intelligence fields for existing integration_events rows.

    This is useful for events created before Device Event Intelligence Backend Pass 1.
    """
    _hf_mon_ensure_schema()
    _hf_device_ensure_intelligence_schema()

    limit = int(payload.limit or 100)

    if limit < 1:
        limit = 1

    if limit > 500:
        limit = 500

    events = _hf_mon_fetch_all(
        """
        SELECT *
        FROM integration_events
        WHERE record_id = %s
        ORDER BY id ASC
        LIMIT %s
        """,
        (_hf_mon_one_line(record_id), limit),
    )

    results = []
    updated_count = 0
    skipped_count = 0
    failed_count = 0

    for row in events:
        result = _hf_device_backfill_event_row(row, force=bool(payload.force))
        results.append(result)

        if result.get("ok") and result.get("skipped"):
            skipped_count += 1
        elif result.get("ok"):
            updated_count += 1
        else:
            failed_count += 1

    return {
        "success": failed_count == 0,
        "record_id": record_id,
        "checked_count": len(events),
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "results": results,
    }




# ============================================================
# Weather Event Intelligence Pass 1
#
# Purpose:
# - Ingest weather risk events into the same HomeFax intelligence pipeline.
# - Auto-match weather to weather-sensitive monitoring plans.
# - Create homeowner-ready compiled insights.
# - No admin review required for normal weather telemetry.
#
# New endpoints:
# POST /weather-events/ingest
# GET  /weather-events/{record_id}/insights
# ============================================================

class _HFWeatherEventIngestPayload(BaseModel):
    tenant_id: str | None = "lateef-home-inspection"
    property_id: str | None = ""
    record_id: str
    property_address: str | None = ""
    weather_event_type: str
    severity: str | None = "info"
    title: str | None = ""
    summary: str | None = ""
    occurred_at: str | None = None
    forecast_window: str | None = ""
    rainfall_inches: float | None = None
    wind_mph: float | None = None
    temperature_f: float | None = None
    humidity_percent: float | None = None
    raw_payload: dict | list | str | None = None


def _hf_weather_event_type(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _hf_weather_capability(weather_event_type: str) -> str:
    event_type = _hf_weather_event_type(weather_event_type)

    mapping = {
        "heavy_rain": "WEATHER_RAIN",
        "rain": "WEATHER_RAIN",
        "storm": "WEATHER_RAIN",
        "thunderstorm": "WEATHER_RAIN",
        "high_wind": "WEATHER_WIND",
        "wind": "WEATHER_WIND",
        "freeze": "WEATHER_FREEZE",
        "freezing": "WEATHER_FREEZE",
        "heat": "WEATHER_HEAT",
        "extreme_heat": "WEATHER_HEAT",
        "drought": "WEATHER_DROUGHT",
        "dry_period": "WEATHER_DROUGHT",
        "humidity": "HUMIDITY",
        "high_humidity": "HUMIDITY",
    }

    return mapping.get(event_type, "WEATHER_EVENT")


def _hf_weather_system_for_capability(capability: str) -> str:
    cap = str(capability or "").strip().upper()

    if cap in {"WEATHER_RAIN", "WEATHER_WIND", "WEATHER_FREEZE", "WEATHER_HEAT", "WEATHER_DROUGHT"}:
        return "Weather"

    if cap == "HUMIDITY":
        return "Indoor Air Quality"

    return "Weather"


def _hf_weather_default_title(weather_event_type: str, capability: str) -> str:
    event_type = _hf_weather_event_type(weather_event_type)

    labels = {
        "heavy_rain": "Heavy rain risk detected",
        "rain": "Rain risk detected",
        "storm": "Storm risk detected",
        "thunderstorm": "Storm risk detected",
        "high_wind": "High wind risk detected",
        "wind": "Wind risk detected",
        "freeze": "Freeze risk detected",
        "freezing": "Freeze risk detected",
        "heat": "Heat risk detected",
        "extreme_heat": "Extreme heat risk detected",
        "drought": "Drought risk detected",
        "dry_period": "Extended dry period risk detected",
        "humidity": "Humidity risk detected",
        "high_humidity": "High humidity risk detected",
    }

    return labels.get(event_type, capability.replace("_", " ").title())


def _hf_weather_default_summary(payload: _HFWeatherEventIngestPayload, capability: str) -> str:
    parts = []

    if payload.property_address:
        parts.append(f"Weather risk detected near {payload.property_address}.")

    event_label = _hf_weather_event_type(payload.weather_event_type).replace("_", " ")

    if event_label:
        parts.append(f"Event type: {event_label}.")

    if payload.forecast_window:
        parts.append(f"Forecast window: {payload.forecast_window}.")

    if payload.rainfall_inches is not None:
        parts.append(f"Rainfall: {payload.rainfall_inches} inches.")

    if payload.wind_mph is not None:
        parts.append(f"Wind: {payload.wind_mph} mph.")

    if payload.temperature_f is not None:
        parts.append(f"Temperature: {payload.temperature_f}°F.")

    if payload.humidity_percent is not None:
        parts.append(f"Humidity: {payload.humidity_percent}%.")

    if not parts:
        parts.append(f"HomeFax received a {capability.replace('_', ' ').lower()} weather event for this property.")

    return " ".join(parts)


def _hf_weather_raw_payload(payload: _HFWeatherEventIngestPayload, capability: str):
    base = {
        "weather_event_type": _hf_weather_event_type(payload.weather_event_type),
        "capability": capability,
        "property_address": payload.property_address or "",
        "forecast_window": payload.forecast_window or "",
        "rainfall_inches": payload.rainfall_inches,
        "wind_mph": payload.wind_mph,
        "temperature_f": payload.temperature_f,
        "humidity_percent": payload.humidity_percent,
    }

    if payload.raw_payload:
        base["source_payload"] = payload.raw_payload

    return base


@app.post("/weather-events/ingest")
def ingest_weather_event(payload: _HFWeatherEventIngestPayload):
    """
    Ingest a weather event and compile it through the existing HomeFax device/event intelligence layer.

    This endpoint is provider-neutral. Later, live weather providers can call this endpoint
    or feed the same normalized payload shape.
    """
    _hf_mon_ensure_schema()
    _hf_device_ensure_intelligence_schema()

    record_id = _hf_device_normalize(payload.record_id)

    if not record_id:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "record_id_required",
                "message": "record_id is required.",
            },
        )

    weather_event_type = _hf_weather_event_type(payload.weather_event_type)

    if not weather_event_type:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "weather_event_type_required",
                "message": "weather_event_type is required.",
            },
        )

    capability = _hf_weather_capability(weather_event_type)
    system_name = _hf_weather_system_for_capability(capability)
    title = _hf_device_normalize(payload.title) or _hf_weather_default_title(weather_event_type, capability)
    summary = _hf_device_normalize(payload.summary) or _hf_weather_default_summary(payload, capability)

    device_payload = _HFDeviceEventIngestPayload(
        tenant_id=payload.tenant_id or "lateef-home-inspection",
        property_id=payload.property_id or "",
        record_id=record_id,
        provider="weather",
        provider_event_id=f"weather-{record_id}-{weather_event_type}-{_hf_device_normalize(payload.occurred_at) or 'now'}",
        source_type="weather_event",
        device_id="weather-service",
        device_name="HomeFax Weather Intelligence",
        capability=capability,
        severity=payload.severity or "info",
        system=system_name,
        component="",
        location=payload.property_address or "",
        title=title,
        summary=summary,
        raw_payload=_hf_weather_raw_payload(payload, capability),
        occurred_at=payload.occurred_at,
    )

    result = ingest_device_event(device_payload)

    if isinstance(result, dict):
        result["weather_intelligence"] = {
            "weather_event_type": weather_event_type,
            "capability": capability,
            "system": system_name,
            "property_address": payload.property_address or "",
            "forecast_window": payload.forecast_window or "",
        }

    return result


@app.get("/weather-events/{record_id}/insights")
def weather_event_insights_for_record(record_id: str):
    """
    Return weather-only HomeFax intelligence events for a record.
    """
    _hf_mon_ensure_schema()
    _hf_device_ensure_intelligence_schema()

    events = _hf_mon_fetch_all(
        """
        SELECT *
        FROM integration_events
        WHERE record_id = %s
          AND COALESCE(event_lifecycle_status, '') <> 'archived_duplicate'
          AND COALESCE(event_status, '') <> 'archived_duplicate'
          AND (
            source_type = 'weather_event'
            OR provider = 'weather'
            OR capability LIKE 'WEATHER%%'
          )
        ORDER BY occurred_at DESC, id DESC
        """,
        (_hf_mon_one_line(record_id),),
    )

    for event in events:
        event["raw_payload"] = _hf_mon_parse_json(event.get("raw_payload"), {})
        event["matched_issue_ids_json"] = _hf_mon_parse_json(event.get("matched_issue_ids_json"), [])

    return {
        "success": True,
        "record_id": record_id,
        "count": len(events),
        "weather_insights": events,
    }




# ============================================================
# Homeowner Device Connection Registry Pass 1
#
# Purpose:
# - Track homeowner-connected providers/devices at the property/record level.
# - Store provider capabilities before real OAuth/provider integrations exist.
# - Give HomeFax a stable registry for weather, Ting, thermostats, sensors,
#   water leak devices, hubs, manual uploads, and email-alert fallbacks.
#
# New endpoints:
# POST  /device-connections/register
# GET   /device-connections/{record_id}
# PATCH /device-connection/{connection_id}/status
# ============================================================

class _HFDeviceConnectionRegisterPayload(BaseModel):
    tenant_id: str | None = "lateef-home-inspection"
    property_id: str | None = ""
    record_id: str
    homeowner_email: str | None = ""
    provider: str
    provider_account_id: str | None = ""
    connection_label: str | None = ""
    connection_status: str | None = "connected"
    capabilities: list[str] | str | None = None
    device_count: int | None = 0
    health_status: str | None = "healthy"
    notes: str | None = ""


class _HFDeviceConnectionStatusPayload(BaseModel):
    connection_status: str | None = None
    health_status: str | None = None
    last_sync_at: str | None = None
    last_event_at: str | None = None
    device_count: int | None = None
    notes: str | None = None


def _hf_connection_allowed_provider(provider: str) -> str:
    value = _hf_device_lower(provider)

    aliases = {
        "mock_leak_sensor": "mock-leak-sensor",
        "mock leak sensor": "mock-leak-sensor",
        "homeassistant": "home_assistant",
        "home assistant": "home_assistant",
        "manual": "manual_upload",
        "email": "email_alert",
        "weather_service": "weather",
    }

    return aliases.get(value, value)


def _hf_connection_default_capabilities(provider: str):
    provider_key = _hf_connection_allowed_provider(provider)

    defaults = {
        "weather": [
            "WEATHER_RAIN",
            "WEATHER_WIND",
            "WEATHER_FREEZE",
            "WEATHER_HEAT",
            "WEATHER_DROUGHT",
            "HUMIDITY",
        ],
        "ting": [
            "ELECTRICAL_ANOMALY",
            "ELECTRICAL_LOAD",
        ],
        "ecobee": [
            "HVAC_RUNTIME",
            "TEMPERATURE",
            "HUMIDITY",
        ],
        "smartthings": [
            "WATER_LEAK",
            "MOISTURE",
            "TEMPERATURE",
            "HUMIDITY",
            "MOTION",
            "CONTACT",
        ],
        "home_assistant": [
            "WATER_LEAK",
            "MOISTURE",
            "TEMPERATURE",
            "HUMIDITY",
            "ELECTRICAL_ANOMALY",
            "HVAC_RUNTIME",
            "AIR_QUALITY",
        ],
        "mock-leak-sensor": [
            "WATER_LEAK",
            "MOISTURE",
        ],
        "manual_upload": [
            "PHOTO_EVIDENCE",
            "DOCUMENT_EVIDENCE",
            "MANUAL_CHECK",
        ],
        "email_alert": [
            "EMAIL_ALERT",
            "DOCUMENT_EVIDENCE",
        ],
    }

    return defaults.get(provider_key, ["GENERAL_DEVICE_EVENT"])


def _hf_connection_capabilities_json(value, provider: str):
    try:
        import json

        if value is None or value == "":
            capabilities = _hf_connection_default_capabilities(provider)
        elif isinstance(value, str):
            try:
                parsed = json.loads(value)
                capabilities = parsed if isinstance(parsed, list) else [value]
            except Exception:
                capabilities = [
                    item.strip().upper()
                    for item in value.split(",")
                    if item.strip()
                ]
        elif isinstance(value, list):
            capabilities = value
        else:
            capabilities = _hf_connection_default_capabilities(provider)

        cleaned = []

        for item in capabilities:
            capability = str(item or "").strip().upper().replace(" ", "_").replace("-", "_")
            if capability and capability not in cleaned:
                cleaned.append(capability)

        return json.dumps(cleaned)

    except Exception:
        return "[]"


def _hf_device_connection_ensure_schema():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS device_connections (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            tenant_id VARCHAR(128) DEFAULT 'lateef-home-inspection',
            property_id VARCHAR(255) DEFAULT '',
            record_id VARCHAR(255) NOT NULL,
            homeowner_email VARCHAR(255) DEFAULT '',
            provider VARCHAR(128) NOT NULL,
            provider_account_id VARCHAR(255) DEFAULT '',
            connection_label VARCHAR(255) DEFAULT '',
            connection_status VARCHAR(64) DEFAULT 'connected',
            capabilities_json JSON NULL,
            device_count INT DEFAULT 0,
            last_sync_at TIMESTAMP NULL,
            last_event_at TIMESTAMP NULL,
            health_status VARCHAR(64) DEFAULT 'healthy',
            notes TEXT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_device_connections_record_id (record_id),
            INDEX idx_device_connections_provider (provider),
            INDEX idx_device_connections_status (connection_status),
            INDEX idx_device_connections_health (health_status)
        )
        """,
        """
        ALTER TABLE integration_events
        ADD COLUMN device_connection_id BIGINT NULL
        """,
    ]

    results = []

    for statement in statements:
        result = _hf_mon_try_execute(statement)

        if not result.get("ok"):
            error_text = str(result.get("error") or "")
            if "Duplicate column" in error_text or "already exists" in error_text:
                result["ok"] = True
                result["error"] = "already_exists"

        results.append(result)

    failed = [item for item in results if not item.get("ok")]

    if failed:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "device_connection_schema_failed",
                "failed": failed,
            },
        )

    return results


def _hf_device_connection_row_to_response(row):
    if not row:
        return None

    output = dict(row)
    output["capabilities"] = _hf_mon_parse_json(output.get("capabilities_json"), [])
    return output


@app.post("/device-connections/register")
def register_device_connection(payload: _HFDeviceConnectionRegisterPayload):
    """
    Register or update a homeowner-connected provider/device source.

    This is the registry foundation. Real OAuth and provider-specific adapters
    can later write to this same table.
    """
    _hf_mon_ensure_schema()
    _hf_device_connection_ensure_schema()

    record_id = _hf_device_normalize(payload.record_id)
    provider = _hf_connection_allowed_provider(payload.provider)

    if not record_id:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "record_id_required",
                "message": "record_id is required.",
            },
        )

    if not provider:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "provider_required",
                "message": "provider is required.",
            },
        )

    allowed_statuses = {
        "connected",
        "pending",
        "disconnected",
        "error",
        "needs_reauth",
        "disabled",
    }

    connection_status = _hf_device_lower(payload.connection_status) or "connected"

    if connection_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "invalid_connection_status",
                "allowed_statuses": sorted(allowed_statuses),
            },
        )

    allowed_health = {
        "healthy",
        "warning",
        "error",
        "unknown",
        "syncing",
        "stale",
    }

    health_status = _hf_device_lower(payload.health_status) or "healthy"

    if health_status not in allowed_health:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "invalid_health_status",
                "allowed_health_statuses": sorted(allowed_health),
            },
        )

    capabilities_json = _hf_connection_capabilities_json(payload.capabilities, provider)
    provider_account_id = _hf_device_normalize(payload.provider_account_id)
    connection_label = _hf_device_normalize(payload.connection_label) or provider.replace("_", " ").title()
    device_count = int(payload.device_count or 0)

    conn = None

    try:
        conn = _hf_mon_get_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM device_connections
                WHERE record_id = %s
                  AND provider = %s
                  AND COALESCE(provider_account_id, '') = %s
                LIMIT 1
                """,
                (record_id, provider, provider_account_id),
            )
            existing = cursor.fetchone()

            if existing:
                cursor.execute(
                    """
                    UPDATE device_connections
                    SET
                        tenant_id = %s,
                        property_id = %s,
                        homeowner_email = %s,
                        connection_label = %s,
                        connection_status = %s,
                        capabilities_json = CAST(%s AS JSON),
                        device_count = %s,
                        health_status = %s,
                        notes = %s
                    WHERE id = %s
                    """,
                    (
                        _hf_device_normalize(payload.tenant_id) or "lateef-home-inspection",
                        _hf_device_normalize(payload.property_id),
                        _hf_device_normalize(payload.homeowner_email),
                        connection_label,
                        connection_status,
                        capabilities_json,
                        device_count,
                        health_status,
                        _hf_device_normalize(payload.notes),
                        existing.get("id"),
                    ),
                )
                connection_id = existing.get("id")
                created = False
            else:
                cursor.execute(
                    """
                    INSERT INTO device_connections (
                        tenant_id,
                        property_id,
                        record_id,
                        homeowner_email,
                        provider,
                        provider_account_id,
                        connection_label,
                        connection_status,
                        capabilities_json,
                        device_count,
                        health_status,
                        notes
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        CAST(%s AS JSON),
                        %s, %s, %s
                    )
                    """,
                    (
                        _hf_device_normalize(payload.tenant_id) or "lateef-home-inspection",
                        _hf_device_normalize(payload.property_id),
                        record_id,
                        _hf_device_normalize(payload.homeowner_email),
                        provider,
                        provider_account_id,
                        connection_label,
                        connection_status,
                        capabilities_json,
                        device_count,
                        health_status,
                        _hf_device_normalize(payload.notes),
                    ),
                )
                connection_id = cursor.lastrowid
                created = True

            cursor.execute(
                """
                SELECT *
                FROM device_connections
                WHERE id = %s
                LIMIT 1
                """,
                (connection_id,),
            )
            row = cursor.fetchone()

        conn.commit()

        return {
            "success": True,
            "created": created,
            "message": "Device connection registered.",
            "connection": _hf_device_connection_row_to_response(row),
        }

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "device_connection_register_failed",
                "message": str(exc),
            },
        )

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass




# Multi-record Scheduled Health Check Pass 2
@app.get("/device-connections/active-records")
def device_connection_active_records(
    tenant_id: str | None = None,
    include_test_records: bool = True,
    limit: int = 500,
):
    """
    Return record_ids that have active HomeFax device/weather connections.

    Used by n8n scheduled health-check workflows so the schedule can fan out
    across all active monitoring records instead of hardcoding one record.
    """
    _hf_mon_ensure_schema()

    safe_limit = max(1, min(int(limit or 500), 1000))

    where_parts = [
        "COALESCE(record_id, '') <> ''",
        "COALESCE(connection_status, '') NOT IN ('deleted', 'removed')",
    ]
    params = []

    if tenant_id:
        where_parts.append("tenant_id = %s")
        params.append(_hf_mon_one_line(tenant_id))

    if not include_test_records:
        where_parts.append("LOWER(record_id) NOT LIKE %s")
        params.append("%test%")
        where_parts.append("LOWER(record_id) NOT LIKE %s")
        params.append("%smoke%")
        where_parts.append("LOWER(record_id) NOT LIKE %s")
        params.append("%dev%")
        where_parts.append("LOWER(record_id) NOT LIKE %s")
        params.append("%qa%")

    # Keep this query deliberately simple for production DB compatibility.
    # Grouping/counting is done in Python so this endpoint works across
    # MySQL/MariaDB variants and avoids datetime aggregate edge cases.
    rows = _hf_mon_fetch_all(
        f"""
        SELECT
          record_id,
          tenant_id,
          homeowner_email,
          connection_status,
          health_status,
          last_sync_at,
          last_event_at,
          updated_at,
          created_at
        FROM user_integrations
        WHERE {" AND ".join(where_parts)}
        ORDER BY id DESC
        """,
        tuple(params),
    )

    grouped = {}

    for row in rows:
        record_id = _hf_mon_one_line(row.get("record_id"))
        if not record_id:
            continue

        bucket = grouped.setdefault(record_id, {
            "record_id": record_id,
            "tenant_id": _hf_mon_one_line(row.get("tenant_id")),
            "homeowner_email": _hf_mon_one_line(row.get("homeowner_email")),
            "connection_count": 0,
            "connected_count": 0,
            "healthy_count": 0,
            "stale_count": 0,
            "warning_count": 0,
            "latest_activity_at": "",
        })

        if not bucket.get("tenant_id"):
            bucket["tenant_id"] = _hf_mon_one_line(row.get("tenant_id"))

        if not bucket.get("homeowner_email"):
            bucket["homeowner_email"] = _hf_mon_one_line(row.get("homeowner_email"))

        connection_status = _hf_device_lower(row.get("connection_status") or "")
        health_status = _hf_device_lower(row.get("health_status") or "")

        bucket["connection_count"] += 1

        if connection_status == "connected":
            bucket["connected_count"] += 1

        if health_status == "healthy":
            bucket["healthy_count"] += 1
        elif health_status == "stale":
            bucket["stale_count"] += 1
        elif health_status in {"warning", "needs_attention", "error"}:
            bucket["warning_count"] += 1

        candidate_dates = [
            row.get("last_sync_at"),
            row.get("last_event_at"),
            row.get("updated_at"),
            row.get("created_at"),
        ]

        for candidate in candidate_dates:
            candidate_text = str(candidate or "")
            if candidate_text and candidate_text > bucket["latest_activity_at"]:
                bucket["latest_activity_at"] = candidate_text

    records = list(grouped.values())

    records.sort(
        key=lambda item: (
            item.get("latest_activity_at") or "",
            item.get("record_id") or "",
        ),
        reverse=True,
    )

    records = records[:safe_limit]

    return {
        "success": True,
        "count": len(records),
        "tenant_id": tenant_id or "",
        "include_test_records": include_test_records,
        "limit": safe_limit,
        "records": records,
    }




@app.get("/device-connections/{record_id}")
def device_connections_for_record(record_id: str):
    """
    Return all registered homeowner/provider/device connections for a record.
    """
    _hf_mon_ensure_schema()
    _hf_device_connection_ensure_schema()

    rows = _hf_mon_fetch_all(
        """
        SELECT *
        FROM device_connections
        WHERE record_id = %s
        ORDER BY
          CASE connection_status
            WHEN 'connected' THEN 1
            WHEN 'pending' THEN 2
            WHEN 'needs_reauth' THEN 3
            WHEN 'error' THEN 4
            WHEN 'disconnected' THEN 5
            ELSE 6
          END,
          provider ASC,
          id ASC
        """,
        (_hf_mon_one_line(record_id),),
    )

    connections = [_hf_device_connection_row_to_response(row) for row in rows]

    capability_counts = {}

    for connection in connections:
        for capability in connection.get("capabilities", []):
            capability_counts[capability] = capability_counts.get(capability, 0) + 1

    return {
        "success": True,
        "record_id": record_id,
        "count": len(connections),
        "connections": connections,
        "capability_counts": capability_counts,
    }


@app.patch("/device-connection/{connection_id}/status")
def update_device_connection_status(connection_id: int, payload: _HFDeviceConnectionStatusPayload):
    """
    Update connection health/status, sync timestamps, event timestamps, and notes.
    """
    _hf_mon_ensure_schema()
    _hf_device_connection_ensure_schema()

    allowed_statuses = {
        "connected",
        "pending",
        "disconnected",
        "error",
        "needs_reauth",
        "disabled",
    }

    allowed_health = {
        "healthy",
        "warning",
        "error",
        "unknown",
        "syncing",
        "stale",
    }

    updates = []
    params = []

    if payload.connection_status is not None:
        connection_status = _hf_device_lower(payload.connection_status)

        if connection_status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail={
                    "success": False,
                    "error": "invalid_connection_status",
                    "allowed_statuses": sorted(allowed_statuses),
                },
            )

        updates.append("connection_status = %s")
        params.append(connection_status)

    if payload.health_status is not None:
        health_status = _hf_device_lower(payload.health_status)

        if health_status not in allowed_health:
            raise HTTPException(
                status_code=400,
                detail={
                    "success": False,
                    "error": "invalid_health_status",
                    "allowed_health_statuses": sorted(allowed_health),
                },
            )

        updates.append("health_status = %s")
        params.append(health_status)

    if payload.last_sync_at is not None:
        updates.append("last_sync_at = %s")
        params.append(_hf_device_normalize(payload.last_sync_at) or None)

    if payload.last_event_at is not None:
        updates.append("last_event_at = %s")
        params.append(_hf_device_normalize(payload.last_event_at) or None)

    if payload.device_count is not None:
        updates.append("device_count = %s")
        params.append(int(payload.device_count or 0))

    if payload.notes is not None:
        updates.append("notes = %s")
        params.append(_hf_device_normalize(payload.notes))

    if not updates:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "no_updates_provided",
                "message": "Provide at least one field to update.",
            },
        )

    params.append(connection_id)

    conn = None

    try:
        conn = _hf_mon_get_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM device_connections
                WHERE id = %s
                LIMIT 1
                """,
                (connection_id,),
            )
            existing = cursor.fetchone()

            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "success": False,
                        "error": "device_connection_not_found",
                        "message": f"Device connection {connection_id} was not found.",
                    },
                )

            cursor.execute(
                f"""
                UPDATE device_connections
                SET {", ".join(updates)}
                WHERE id = %s
                """,
                params,
            )

            cursor.execute(
                """
                SELECT *
                FROM device_connections
                WHERE id = %s
                LIMIT 1
                """,
                (connection_id,),
            )
            row = cursor.fetchone()

        conn.commit()

        return {
            "success": True,
            "message": "Device connection status updated.",
            "connection": _hf_device_connection_row_to_response(row),
        }

    except HTTPException:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        raise

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "device_connection_status_update_failed",
                "message": str(exc),
            },
        )

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass




# ============================================================
# Weather Provider Adapter Pass 1
#
# Purpose:
# - Convert provider-style weather sync payloads into HomeFax weather events.
# - Reuse the existing Weather Event Intelligence pipeline.
# - Update the weather device connection registry after sync.
# - Keep this provider-neutral so OpenWeather, NOAA, Tomorrow.io, WeatherAPI,
#   or any future provider can plug in later.
#
# New endpoint:
# POST /weather-provider/{record_id}/sync
# ============================================================

class _HFWeatherProviderSyncPayload(BaseModel):
    tenant_id: str | None = "lateef-home-inspection"
    property_id: str | None = ""
    property_address: str | None = ""
    homeowner_email: str | None = ""
    provider: str | None = "weather"
    provider_account_id: str | None = ""
    forecast_window: str | None = "Provider sync"
    occurred_at: str | None = None

    rainfall_inches: float | None = None
    wind_mph: float | None = None
    temperature_f: float | None = None
    humidity_percent: float | None = None
    dry_days: int | None = None

    raw_payload: dict | list | str | None = None

    create_low_risk_events: bool | None = False


def _hf_weather_provider_severity_for_rain(rainfall_inches):
    if rainfall_inches is None:
        return None

    try:
        rain = float(rainfall_inches)
    except Exception:
        return None

    if rain >= 1.0:
        return "high"

    if rain >= 0.5:
        return "medium"

    if rain > 0:
        return "info"

    return None


def _hf_weather_provider_severity_for_wind(wind_mph):
    if wind_mph is None:
        return None

    try:
        wind = float(wind_mph)
    except Exception:
        return None

    if wind >= 58:
        return "high"

    if wind >= 40:
        return "medium"

    if wind >= 25:
        return "info"

    return None


def _hf_weather_provider_severity_for_temperature(temperature_f):
    if temperature_f is None:
        return None, None

    try:
        temp = float(temperature_f)
    except Exception:
        return None, None

    if temp <= 20:
        return "freeze", "high"

    if temp <= 32:
        return "freeze", "medium"

    if temp >= 100:
        return "heat", "high"

    if temp >= 90:
        return "heat", "medium"

    return None, None


def _hf_weather_provider_severity_for_humidity(humidity_percent):
    if humidity_percent is None:
        return None

    try:
        humidity = float(humidity_percent)
    except Exception:
        return None

    if humidity >= 75:
        return "high"

    if humidity >= 65:
        return "medium"

    if humidity >= 55:
        return "info"

    return None


def _hf_weather_provider_severity_for_drought(dry_days):
    if dry_days is None:
        return None

    try:
        days = int(dry_days)
    except Exception:
        return None

    if days >= 21:
        return "high"

    if days >= 14:
        return "medium"

    if days >= 7:
        return "info"

    return None


def _hf_weather_provider_make_raw_payload(payload: _HFWeatherProviderSyncPayload, event_type: str):
    return {
        "adapter": "homefax_weather_provider_adapter_pass_1",
        "provider": payload.provider or "weather",
        "provider_account_id": payload.provider_account_id or "",
        "event_type": event_type,
        "forecast_window": payload.forecast_window or "",
        "rainfall_inches": payload.rainfall_inches,
        "wind_mph": payload.wind_mph,
        "temperature_f": payload.temperature_f,
        "humidity_percent": payload.humidity_percent,
        "dry_days": payload.dry_days,
        "source_payload": payload.raw_payload or {},
    }


def _hf_weather_provider_build_candidate_events(record_id: str, payload: _HFWeatherProviderSyncPayload):
    candidates = []

    rain_severity = _hf_weather_provider_severity_for_rain(payload.rainfall_inches)
    if rain_severity and (rain_severity != "info" or payload.create_low_risk_events):
        candidates.append({
            "weather_event_type": "heavy_rain" if rain_severity in {"medium", "high"} else "rain",
            "severity": rain_severity,
            "title": "Heavy rain risk near monitored home conditions" if rain_severity in {"medium", "high"} else "Rain event near monitored home conditions",
            "summary": f"Weather provider sync detected {payload.rainfall_inches} inches of rain for this property.",
        })

    wind_severity = _hf_weather_provider_severity_for_wind(payload.wind_mph)
    if wind_severity and (wind_severity != "info" or payload.create_low_risk_events):
        candidates.append({
            "weather_event_type": "high_wind",
            "severity": wind_severity,
            "title": "High wind risk near monitored exterior and roof conditions",
            "summary": f"Weather provider sync detected wind near {payload.wind_mph} mph for this property.",
        })

    temp_event_type, temp_severity = _hf_weather_provider_severity_for_temperature(payload.temperature_f)
    if temp_event_type and temp_severity and (temp_severity != "info" or payload.create_low_risk_events):
        if temp_event_type == "freeze":
            title = "Freeze risk near monitored home systems"
            summary = f"Weather provider sync detected freezing temperature near {payload.temperature_f}°F."
        else:
            title = "Heat risk near monitored home systems"
            summary = f"Weather provider sync detected high temperature near {payload.temperature_f}°F."

        candidates.append({
            "weather_event_type": temp_event_type,
            "severity": temp_severity,
            "title": title,
            "summary": summary,
        })

    humidity_severity = _hf_weather_provider_severity_for_humidity(payload.humidity_percent)
    if humidity_severity and (humidity_severity != "info" or payload.create_low_risk_events):
        candidates.append({
            "weather_event_type": "humidity",
            "severity": humidity_severity,
            "title": "Humidity risk near monitored indoor air conditions",
            "summary": f"Weather provider sync detected humidity near {payload.humidity_percent}% for this property.",
        })

    drought_severity = _hf_weather_provider_severity_for_drought(payload.dry_days)
    if drought_severity and (drought_severity != "info" or payload.create_low_risk_events):
        candidates.append({
            "weather_event_type": "drought",
            "severity": drought_severity,
            "title": "Drought or extended dry period risk near monitored foundation conditions",
            "summary": f"Weather provider sync detected an extended dry period of {payload.dry_days} days.",
        })

    return candidates


def _hf_weather_provider_find_weather_connection(record_id: str, provider_account_id: str = ""):
    _hf_device_connection_ensure_schema()

    provider_account_id = _hf_device_normalize(provider_account_id)

    if provider_account_id:
        rows = _hf_mon_fetch_all(
            """
            SELECT *
            FROM device_connections
            WHERE record_id = %s
              AND provider = 'weather'
              AND COALESCE(provider_account_id, '') = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (_hf_mon_one_line(record_id), provider_account_id),
        )

        if rows:
            return rows[0]

    rows = _hf_mon_fetch_all(
        """
        SELECT *
        FROM device_connections
        WHERE record_id = %s
          AND provider = 'weather'
        ORDER BY id ASC
        LIMIT 1
        """,
        (_hf_mon_one_line(record_id),),
    )

    return rows[0] if rows else None


def _hf_weather_provider_update_connection_after_sync(record_id: str, payload: _HFWeatherProviderSyncPayload, last_event_at: str | None, created_count: int):
    _hf_device_connection_ensure_schema()

    connection = _hf_weather_provider_find_weather_connection(
        record_id,
        payload.provider_account_id or "",
    )

    # If the weather connection does not exist yet, create it.
    if not connection:
        register_result = register_device_connection(
            _HFDeviceConnectionRegisterPayload(
                tenant_id=payload.tenant_id or "lateef-home-inspection",
                property_id=payload.property_id or "",
                record_id=record_id,
                homeowner_email=payload.homeowner_email or "",
                provider="weather",
                provider_account_id=payload.provider_account_id or f"weather-{record_id}",
                connection_label="HomeFax Weather Intelligence",
                connection_status="connected",
                device_count=1,
                health_status="healthy",
                notes="Weather connection auto-created by Weather Provider Adapter Pass 1.",
            )
        )
        connection = (register_result or {}).get("connection") or {}

    connection_id = connection.get("id")

    if not connection_id:
        return {
            "updated": False,
            "reason": "missing_connection_id",
            "connection": connection,
        }

    sync_time = payload.occurred_at or last_event_at

    if not sync_time:
        from datetime import datetime
        sync_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    update_result = update_device_connection_status(
        int(connection_id),
        # Weather Connection Health Status Fix Pass 1
        # A successful provider sync is healthy even when all candidate events
        # were skipped because they already existed.
        _HFDeviceConnectionStatusPayload(
            connection_status="connected",
            health_status="healthy",
            last_sync_at=sync_time,
            last_event_at=last_event_at or sync_time,
            device_count=1,
            notes=(
                f"Weather provider adapter sync completed. "
                f"Created {created_count} HomeFax weather event(s)."
            ),
        ),
    )

    return {
        "updated": True,
        "connection": (update_result or {}).get("connection"),
    }




# Weather Provider Adapter Pass 1B - idempotent safe response fix
def _hf_weather_provider_existing_event(record_id: str, provider_event_id: str):
    if not provider_event_id:
        return None

    rows = _hf_mon_fetch_all(
        """
        SELECT *
        FROM integration_events
        WHERE record_id = %s
          AND provider = 'weather'
          AND provider_event_id = %s
        ORDER BY id ASC
        LIMIT 1
        """,
        (_hf_mon_one_line(record_id), provider_event_id),
    )

    return rows[0] if rows else None


def _hf_weather_provider_event_summary(event_row: dict | None, fallback: dict | None = None):
    event_row = event_row or {}
    fallback = fallback or {}

    def pick(key, default=None):
        value = event_row.get(key)
        if value is None or value == "":
            value = fallback.get(key, default)
        return value

    try:
        confidence = float(pick("match_confidence", 0) or 0)
    except Exception:
        confidence = 0.0

    return {
        "id": pick("id"),
        "provider": pick("provider"),
        "source_type": pick("source_type"),
        "provider_event_id": pick("provider_event_id"),
        "capability": pick("capability"),
        "severity": pick("severity"),
        "match_status": pick("match_status"),
        "match_confidence": confidence,
        "monitoring_plan_id": pick("monitoring_plan_id"),
        "source_issue_id": pick("source_issue_id"),
        "compiled_insight_title": pick("compiled_insight_title"),
        "homeowner_confirmation_status": pick("homeowner_confirmation_status"),
        "alert_status": pick("alert_status"),
        "occurred_at": str(pick("occurred_at", "") or ""),
    }


def _hf_weather_provider_make_provider_event_id(record_id: str, weather_event_type: str, occurred_at: str | None):
    clean_time = _hf_device_normalize(occurred_at) or "now"
    return f"weather-{record_id}-{_hf_weather_event_type(weather_event_type)}-{clean_time}"


@app.post("/weather-provider/{record_id}/sync")
def sync_weather_provider_for_record(record_id: str, payload: _HFWeatherProviderSyncPayload):
    """
    Provider-neutral weather adapter sync.

    This endpoint is idempotent by provider_event_id:
    if a provider event already exists for this record, it is skipped instead of duplicated.
    """
    try:
        _hf_mon_ensure_schema()
        _hf_device_ensure_intelligence_schema()
        _hf_device_connection_ensure_schema()

        clean_record_id = _hf_device_normalize(record_id)

        if not clean_record_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "success": False,
                    "error": "record_id_required",
                    "message": "record_id is required.",
                },
            )

        candidates = _hf_weather_provider_build_candidate_events(clean_record_id, payload)

        created_event_summaries = []
        skipped_event_summaries = []
        failed_events = []
        last_event_at = None

        for candidate in candidates:
            weather_event_type = candidate["weather_event_type"]
            provider_event_id = _hf_weather_provider_make_provider_event_id(
                clean_record_id,
                weather_event_type,
                payload.occurred_at,
            )

            existing = _hf_weather_provider_existing_event(clean_record_id, provider_event_id)

            if existing:
                skipped_event_summaries.append(
                    _hf_weather_provider_event_summary(existing, {"provider_event_id": provider_event_id})
                )

                if existing.get("occurred_at"):
                    last_event_at = str(existing.get("occurred_at"))
                elif payload.occurred_at:
                    last_event_at = payload.occurred_at

                continue

            try:
                weather_payload = _HFWeatherEventIngestPayload(
                    tenant_id=payload.tenant_id or "lateef-home-inspection",
                    property_id=payload.property_id or "",
                    record_id=clean_record_id,
                    property_address=payload.property_address or "",
                    weather_event_type=weather_event_type,
                    severity=candidate["severity"],
                    title=candidate["title"],
                    summary=candidate["summary"],
                    occurred_at=payload.occurred_at,
                    forecast_window=payload.forecast_window or "Provider sync",
                    rainfall_inches=payload.rainfall_inches,
                    wind_mph=payload.wind_mph,
                    temperature_f=payload.temperature_f,
                    humidity_percent=payload.humidity_percent,
                    raw_payload=_hf_weather_provider_make_raw_payload(
                        payload,
                        weather_event_type,
                    ),
                )

                result = ingest_weather_event(weather_payload)
                event_row = (result or {}).get("event") or {}
                intelligence = (result or {}).get("intelligence") or {}

                created_event_summaries.append(
                    _hf_weather_provider_event_summary(event_row, intelligence)
                )

                if event_row.get("occurred_at"):
                    last_event_at = str(event_row.get("occurred_at"))
                elif payload.occurred_at:
                    last_event_at = payload.occurred_at

            except HTTPException as exc:
                failed_events.append({
                    "candidate": candidate,
                    "provider_event_id": provider_event_id,
                    "error": exc.detail,
                })
            except Exception as exc:
                failed_events.append({
                    "candidate": candidate,
                    "provider_event_id": provider_event_id,
                    "error": str(exc),
                })

        connection_update_summary = {
            "updated": False,
            "connection_id": None,
            "provider": "weather",
            "connection_status": None,
            "health_status": None,
            "last_sync_at": None,
            "last_event_at": None,
        }

        try:
            connection_update = _hf_weather_provider_update_connection_after_sync(
                clean_record_id,
                payload,
                last_event_at,
                len(created_event_summaries),
            )

            connection = (connection_update or {}).get("connection") or {}

            connection_update_summary = {
                "updated": bool((connection_update or {}).get("updated")),
                "connection_id": connection.get("id"),
                "provider": connection.get("provider"),
                "connection_status": connection.get("connection_status"),
                "health_status": connection.get("health_status"),
                "last_sync_at": str(connection.get("last_sync_at") or ""),
                "last_event_at": str(connection.get("last_event_at") or ""),
            }

        except Exception as exc:
            failed_events.append({
                "candidate": "connection_update",
                "error": str(exc),
            })

        success = len(failed_events) == 0

        return {
            "success": success,
            "message": "Weather provider adapter sync completed." if success else "Weather provider adapter sync completed with errors.",
            "record_id": clean_record_id,
            "candidate_count": len(candidates),
            "created_count": len(created_event_summaries),
            "skipped_count": len(skipped_event_summaries),
            "failed_count": len(failed_events),
            "created_events": created_event_summaries,
            "skipped_events": skipped_event_summaries,
            "failed_events": failed_events,
            "connection_update": connection_update_summary,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "weather_provider_adapter_sync_failed",
                "message": str(exc),
            },
        )



# ============================================================
# Weather Provider Duplicate Cleanup Pass 1
#
# Purpose:
# - Archive duplicate weather provider events created before adapter idempotency.
# - Keep the earliest row for each provider_event_id.
# - Mark later rows as duplicate/archived instead of hard-deleting production data.
#
# New endpoint:
# POST /weather-provider/{record_id}/cleanup-duplicates
# ============================================================

class _HFWeatherProviderDuplicateCleanupPayload(BaseModel):
    dry_run: bool | None = True
    provider: str | None = "weather"


def _hf_weather_provider_duplicate_groups(record_id: str, provider: str = "weather"):
    rows = _hf_mon_fetch_all(
        """
        SELECT
            provider_event_id,
            COUNT(*) AS duplicate_count,
            MIN(id) AS keep_id
        FROM integration_events
        WHERE record_id = %s
          AND provider = %s
          AND provider_event_id IS NOT NULL
          AND provider_event_id <> ''
        GROUP BY provider_event_id
        HAVING COUNT(*) > 1
        ORDER BY provider_event_id ASC
        """,
        (_hf_mon_one_line(record_id), provider),
    )

    return rows


def _hf_weather_provider_duplicate_rows(record_id: str, provider: str, provider_event_id: str, keep_id: int):
    rows = _hf_mon_fetch_all(
        """
        SELECT *
        FROM integration_events
        WHERE record_id = %s
          AND provider = %s
          AND provider_event_id = %s
          AND id <> %s
        ORDER BY id ASC
        """,
        (_hf_mon_one_line(record_id), provider, provider_event_id, keep_id),
    )

    return rows


@app.post("/weather-provider/{record_id}/cleanup-duplicates")
def cleanup_weather_provider_duplicates(record_id: str, payload: _HFWeatherProviderDuplicateCleanupPayload = _HFWeatherProviderDuplicateCleanupPayload()):
    """
    Archive duplicate weather provider events.

    Keeps the earliest event id per provider_event_id and marks later duplicates
    as archived duplicate rows. Use dry_run=true first.
    """
    _hf_mon_ensure_schema()
    _hf_device_ensure_intelligence_schema()

    provider = _hf_device_lower(payload.provider) or "weather"
    dry_run = bool(payload.dry_run)

    groups = _hf_weather_provider_duplicate_groups(record_id, provider)

    cleanup_results = []
    archived_count = 0

    conn = None

    try:
        conn = _hf_mon_get_connection()

        for group in groups:
            provider_event_id = group.get("provider_event_id")
            keep_id = int(group.get("keep_id"))

            duplicate_rows = _hf_weather_provider_duplicate_rows(
                record_id,
                provider,
                provider_event_id,
                keep_id,
            )

            duplicate_ids = [row.get("id") for row in duplicate_rows]

            cleanup_results.append({
                "provider_event_id": provider_event_id,
                "duplicate_count": int(group.get("duplicate_count") or 0),
                "keep_id": keep_id,
                "duplicate_ids_to_archive": duplicate_ids,
            })

            if dry_run or not duplicate_ids:
                continue

            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE integration_events
                    SET
                        event_lifecycle_status = 'archived_duplicate',
                        homeowner_confirmation_status = 'not_relevant',
                        alert_status = 'archived',
                        event_status = 'archived_duplicate',
                        match_reason = CONCAT(
                            COALESCE(match_reason, ''),
                            CASE
                                WHEN COALESCE(match_reason, '') = '' THEN ''
                                ELSE ' | '
                            END,
                            'Archived duplicate weather provider event; kept event id {keep_id}.'
                        )
                    WHERE id IN ({", ".join(["%s"] * len(duplicate_ids))})
                    """,
                    duplicate_ids,
                )

            archived_count += len(duplicate_ids)

        if not dry_run:
            conn.commit()

        return {
            "success": True,
            "record_id": record_id,
            "provider": provider,
            "dry_run": dry_run,
            "duplicate_group_count": len(groups),
            "archived_count": archived_count,
            "results": cleanup_results,
        }

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "weather_provider_duplicate_cleanup_failed",
                "message": str(exc),
            },
        )

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass




# ============================================================
# Device Connection Health Automation Pass 1
#
# Purpose:
# - Automatically evaluate provider/device connection health.
# - Use last_sync_at, last_event_at, provider type, and connection_status.
# - Update device_connections.health_status without touching event history.
#
# New endpoint:
# POST /device-connections/{record_id}/health-check
# ============================================================

class _HFDeviceConnectionHealthCheckPayload(BaseModel):
    dry_run: bool | None = True


def _hf_connection_parse_datetime(value):
    if not value:
        return None

    try:
        from datetime import datetime

        if hasattr(value, "isoformat"):
            return value

        raw = str(value).strip()

        if not raw:
            return None

        # MySQL often returns "YYYY-MM-DD HH:MM:SS"; API rows may use ISO format.
        normalized = raw.replace("T", " ").replace("Z", "").split(".")[0]

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(normalized, fmt)
            except Exception:
                pass

        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None

    except Exception:
        return None


def _hf_connection_age_hours(value):
    from datetime import datetime

    parsed = _hf_connection_parse_datetime(value)

    if not parsed:
        return None

    try:
        return max(0, (datetime.utcnow() - parsed).total_seconds() / 3600)
    except Exception:
        return None


def _hf_connection_best_activity_age_hours(connection):
    sync_age = _hf_connection_age_hours(connection.get("last_sync_at"))
    event_age = _hf_connection_age_hours(connection.get("last_event_at"))

    ages = [age for age in [sync_age, event_age] if age is not None]

    if not ages:
        return None

    return min(ages)


def _hf_connection_expected_policy(provider: str):
    provider_key = _hf_connection_allowed_provider(provider)

    policies = {
        "weather": {
            "healthy_hours": 24,
            "stale_hours": 48,
            "warning_hours": 72,
            "no_activity_health": "warning",
            "reason": "Weather sources should sync at least daily.",
        },
        "ting": {
            "healthy_hours": 168,
            "stale_hours": 336,
            "warning_hours": 720,
            "no_activity_health": "stale",
            "reason": "Electrical monitors may not emit frequent events, but should check in periodically.",
        },
        "ecobee": {
            "healthy_hours": 48,
            "stale_hours": 168,
            "warning_hours": 336,
            "no_activity_health": "stale",
            "reason": "Thermostat integrations should sync every 1-2 days.",
        },
        "smartthings": {
            "healthy_hours": 72,
            "stale_hours": 168,
            "warning_hours": 336,
            "no_activity_health": "stale",
            "reason": "Hub/device integrations should sync periodically.",
        },
        "home_assistant": {
            "healthy_hours": 72,
            "stale_hours": 168,
            "warning_hours": 336,
            "no_activity_health": "stale",
            "reason": "Local hub integrations should sync periodically.",
        },
        "mock-leak-sensor": {
            "healthy_hours": None,
            "stale_hours": None,
            "warning_hours": None,
            "no_activity_health": "healthy",
            "reason": "Mock sensors are test/manual sources and may not sync on a schedule.",
        },
        "manual_upload": {
            "healthy_hours": None,
            "stale_hours": None,
            "warning_hours": None,
            "no_activity_health": "healthy",
            "reason": "Manual upload sources do not require automated sync.",
        },
        "email_alert": {
            "healthy_hours": None,
            "stale_hours": None,
            "warning_hours": None,
            "no_activity_health": "healthy",
            "reason": "Email alert sources are passive and do not require frequent sync.",
        },
    }

    return policies.get(provider_key, {
        "healthy_hours": 168,
        "stale_hours": 336,
        "warning_hours": 720,
        "no_activity_health": "unknown",
        "reason": "Generic provider policy applied.",
    })


def _hf_connection_calculate_health(connection):
    provider = _hf_connection_allowed_provider(connection.get("provider") or "")
    connection_status = _hf_device_lower(connection.get("connection_status") or "")

    if connection_status in {"disconnected", "disabled", "needs_reauth", "error"}:
        mapped = {
            "disconnected": "stale",
            "disabled": "unknown",
            "needs_reauth": "warning",
            "error": "error",
        }

        return {
            "provider": provider,
            "connection_status": connection_status,
            "current_health_status": connection.get("health_status"),
            "recommended_health_status": mapped.get(connection_status, "warning"),
            "activity_age_hours": _hf_connection_best_activity_age_hours(connection),
            "reason": f"Connection status is {connection_status}.",
        }

    policy = _hf_connection_expected_policy(provider)
    age_hours = _hf_connection_best_activity_age_hours(connection)

    if age_hours is None:
        return {
            "provider": provider,
            "connection_status": connection_status or "connected",
            "current_health_status": connection.get("health_status"),
            "recommended_health_status": policy["no_activity_health"],
            "activity_age_hours": None,
            "reason": policy["reason"] + " No sync or event timestamp is saved yet.",
        }

    healthy_hours = policy.get("healthy_hours")
    stale_hours = policy.get("stale_hours")
    warning_hours = policy.get("warning_hours")

    if healthy_hours is None:
        recommended = "healthy"
    elif age_hours <= healthy_hours:
        recommended = "healthy"
    elif stale_hours is not None and age_hours <= stale_hours:
        recommended = "stale"
    elif warning_hours is not None and age_hours <= warning_hours:
        recommended = "warning"
    else:
        recommended = "error"

    return {
        "provider": provider,
        "connection_status": connection_status or "connected",
        "current_health_status": connection.get("health_status"),
        "recommended_health_status": recommended,
        "activity_age_hours": round(age_hours, 2),
        "reason": policy["reason"],
    }



# Extracted Report Text Normalization Backfill Pass 1
class _HFTextNormalizationBackfillPayload(BaseModel):
    dry_run: bool | None = True
    limit: int | None = 500


def _hf_normalize_extracted_report_text(value: Any) -> str:
    """
    Normalize recurring extraction/OCR mojibake and spacing issues from uploaded
    inspection reports before they appear in monitoring/device insight cards.
    """
    if value is None:
        return ""

    text_value = str(value)

    if not text_value:
        return ""

    replacements = [
        # Main issue seen in production homeowner live monitoring card.
        (r"Main\s+Water\s+Shut[-\s]*O(?:ff|f|i|ì|ﬀ)\s*Valve", "Main Water Shut-Off Valve"),
        (r"Main\s+Water\s+Shut[-\s]*Off\s*Valve", "Main Water Shut-Off Valve"),
        (r"Main\s+Water\s+Shut\s+Off\s*Valve", "Main Water Shut-Off Valve"),
        (r"Main\s+Water\s+Shut-Off\s*Valve", "Main Water Shut-Off Valve"),

        # General shutoff variants.
        (r"Shut[-\s]*O(?:ff|f|i|ì|ﬀ)\s*Valve", "Shut-Off Valve"),
        (r"Shut[-\s]*Off\s*Valve", "Shut-Off Valve"),
        (r"Shut\s+Off\s*Valve", "Shut-Off Valve"),
        (r"Shut-Off\s*Valve", "Shut-Off Valve"),

        # Common OCR ligature / accent issues already seen elsewhere.
        (r"Quali(?:í|ﬁ|i)ed", "Qualified"),
        (r"Gfci", "GFCI"),
        (r"Afci", "AFCI"),
    ]

    cleaned = text_value

    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


def _hf_text_normalization_columns_for_table(table_name: str) -> list[str]:
    """
    Return candidate text columns by table. The backfill checks actual table
    schema before attempting updates, so this list can safely include optional
    columns that may not exist in older environments.
    """
    table_name = str(table_name or "").strip()

    if table_name == "integration_events":
        return [
            "event_title",
            "event_summary",
            "compiled_insight_title",
            "compiled_insight_summary",
            "recommended_homeowner_action",
            "match_reason",
            "system",
            "component",
            "location",
            "device_name",
            "connection_label",
            "provider",
        ]

    if table_name == "monitoring_plans":
        return [
            "plan_title",
            "plan_summary",
            "monitoring_summary",
            "risk_summary",
            "system",
            "component",
            "location",
            "source_title",
        ]

    if table_name == "verified_issues":
        return [
            "title",
            "system",
            "component",
            "location",
            "source_text",
            "source_report_section",
            "standard_system",
            "standard_component",
            "standard_location",
            "homefax_explanation",
            "recommended_action",
            "monitoring_plan",
        ]

    return []


def _hf_existing_columns(table_name: str) -> set[str]:
    rows = _hf_mon_fetch_all(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        (table_name,),
    )

    return {str(row.get("COLUMN_NAME") or row.get("column_name") or "").strip() for row in rows}


def _hf_text_normalization_fetch_rows(table_name: str, record_id: str, columns: list[str], limit: int) -> list[dict]:
    selected_columns = ["id"] + columns
    quoted_columns = ", ".join(f"`{column}`" for column in selected_columns)

    return _hf_mon_fetch_all(
        f"""
        SELECT {quoted_columns}
        FROM `{table_name}`
        WHERE record_id = %s
        ORDER BY id ASC
        LIMIT %s
        """,
        (_hf_mon_one_line(record_id), int(limit or 500)),
    )


def _hf_text_normalization_update_row(table_name: str, row_id: Any, updates: dict[str, str]) -> None:
    if not updates:
        return

    set_clause = ", ".join(f"`{column}` = %s" for column in updates.keys())
    values = list(updates.values()) + [row_id]

    _hf_mon_execute(
        f"""
        UPDATE `{table_name}`
        SET {set_clause}
        WHERE id = %s
        """,
        tuple(values),
    )


@app.post("/admin/text-normalization/{record_id}/backfill")
def admin_text_normalization_backfill(
    record_id: str,
    payload: _HFTextNormalizationBackfillPayload | None = None,
):
    """
    Dry-run or apply extracted report text normalization for a single HomeFax record.

    This fixes stored OCR/report extraction variants such as:
    - Main Water Shut-Oi Valve
    - Main Water Shut-Oì Valve
    - Main Water Shut-OffValve

    Canonical value:
    - Main Water Shut-Off Valve
    """
    _hf_mon_ensure_schema()

    payload = payload or _HFTextNormalizationBackfillPayload()
    dry_run = bool(payload.dry_run if payload.dry_run is not None else True)
    limit = int(payload.limit or 500)

    tables = ["integration_events", "monitoring_plans", "verified_issues"]

    checked_rows = 0
    changed_rows = 0
    changed_fields = 0
    table_results = []

    for table_name in tables:
        existing_columns = _hf_existing_columns(table_name)
        candidate_columns = _hf_text_normalization_columns_for_table(table_name)
        columns = [column for column in candidate_columns if column in existing_columns]

        if "id" not in existing_columns or "record_id" not in existing_columns:
            table_results.append({
                "table": table_name,
                "skipped": True,
                "reason": "id or record_id column missing",
                "columns": columns,
                "checked_rows": 0,
                "changed_rows": 0,
                "changed_fields": 0,
                "samples": [],
            })
            continue

        if not columns:
            table_results.append({
                "table": table_name,
                "skipped": True,
                "reason": "no candidate text columns exist",
                "columns": [],
                "checked_rows": 0,
                "changed_rows": 0,
                "changed_fields": 0,
                "samples": [],
            })
            continue

        rows = _hf_text_normalization_fetch_rows(table_name, record_id, columns, limit)

        table_checked = 0
        table_changed_rows = 0
        table_changed_fields = 0
        samples = []

        for row in rows:
            table_checked += 1
            checked_rows += 1

            row_id = row.get("id")
            updates = {}
            field_changes = []

            for column in columns:
                before = row.get(column)

                if before is None:
                    continue

                after = _hf_normalize_extracted_report_text(before)

                if str(before) != str(after):
                    updates[column] = after
                    field_changes.append({
                        "column": column,
                        "before": str(before),
                        "after": after,
                    })

            if updates:
                table_changed_rows += 1
                changed_rows += 1
                table_changed_fields += len(updates)
                changed_fields += len(updates)

                if len(samples) < 10:
                    samples.append({
                        "id": row_id,
                        "changes": field_changes,
                    })

                if not dry_run:
                    _hf_text_normalization_update_row(table_name, row_id, updates)

        table_results.append({
            "table": table_name,
            "skipped": False,
            "columns": columns,
            "checked_rows": table_checked,
            "changed_rows": table_changed_rows,
            "changed_fields": table_changed_fields,
            "samples": samples,
        })

    return {
        "success": True,
        "record_id": _hf_mon_one_line(record_id),
        "dry_run": dry_run,
        "limit": limit,
        "checked_rows": checked_rows,
        "changed_rows": changed_rows,
        "changed_fields": changed_fields,
        "tables": table_results,
    }




@app.post("/device-connections/{record_id}/health-check")
def health_check_device_connections_for_record(record_id: str, payload: _HFDeviceConnectionHealthCheckPayload = _HFDeviceConnectionHealthCheckPayload()):
    """
    Evaluate device/weather connection health for a record.

    dry_run=true returns recommendations only.
    dry_run=false updates health_status and notes.
    """
    _hf_mon_ensure_schema()
    _hf_device_connection_ensure_schema()

    dry_run = bool(payload.dry_run)

    rows = _hf_mon_fetch_all(
        """
        SELECT *
        FROM device_connections
        WHERE record_id = %s
        ORDER BY provider ASC, id ASC
        """,
        (_hf_mon_one_line(record_id),),
    )

    results = []
    update_count = 0

    conn = None

    try:
        conn = _hf_mon_get_connection()

        for row in rows:
            assessment = _hf_connection_calculate_health(row)

            current_health = _hf_device_lower(row.get("health_status") or "")
            recommended_health = _hf_device_lower(assessment.get("recommended_health_status") or "unknown")
            changed = current_health != recommended_health

            result = {
                "id": row.get("id"),
                "provider": row.get("provider"),
                "connection_label": row.get("connection_label"),
                "connection_status": row.get("connection_status"),
                "current_health_status": row.get("health_status"),
                "recommended_health_status": recommended_health,
                "changed": changed,
                "dry_run": dry_run,
                "last_sync_at": str(row.get("last_sync_at") or ""),
                "last_event_at": str(row.get("last_event_at") or ""),
                "activity_age_hours": assessment.get("activity_age_hours"),
                "reason": assessment.get("reason"),
            }

            results.append(result)

            if dry_run or not changed:
                continue

            note = (
                f"Automated health check set health_status to {recommended_health}. "
                f"{assessment.get('reason') or ''}"
            ).strip()

            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE device_connections
                    SET
                        health_status = %s,
                        notes = %s
                    WHERE id = %s
                    """,
                    (
                        recommended_health,
                        note,
                        row.get("id"),
                    ),
                )

            update_count += 1

        if not dry_run:
            conn.commit()

        return {
            "success": True,
            "record_id": record_id,
            "dry_run": dry_run,
            "count": len(results),
            "update_count": update_count,
            "results": results,
        }

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "device_connection_health_check_failed",
                "message": str(exc),
            },
        )

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@app.get("/monitoring-lifecycle-health")
def monitoring_lifecycle_health():
    table_checks = {}

    for table_name in (
        "monitoring_plans",
        "integration_events",
        "property_device_locations",
        "user_integrations",
    ):
        try:
            row = _hf_mon_fetch_one(f"SELECT COUNT(*) AS count_value FROM {table_name}")
            table_checks[table_name] = {
                "ok": True,
                "count": row.get("count_value") if row else 0,
            }
        except Exception as exc:
            table_checks[table_name] = {
                "ok": False,
                "error": str(exc),
            }

    return {
        "success": True,
        "service": "homefax_monitoring_lifecycle",
        "schema_hint": "call POST /monitoring-lifecycle/init if any table check is false",
        "tables": table_checks,
    }


@app.post("/monitoring-lifecycle/init")
def monitoring_lifecycle_init():
    results = _hf_mon_ensure_schema()
    failed = [item for item in results if not item.get("ok")]

    return {
        "success": len(failed) == 0,
        "message": (
            "Monitoring lifecycle schema initialized."
            if not failed
            else "Monitoring lifecycle schema initialization failed for one or more operations."
        ),
        "failed_count": len(failed),
        "results": results,
    }


@app.post("/monitoring-plans/from-issue/{issue_id}")
def monitoring_plan_from_issue(issue_id: int, request: _HFMonCreatePlanRequest = _HFMonCreatePlanRequest()):
    result = _hf_mon_create_or_update_plan_from_issue(issue_id, force=bool(request.force))

    return {
        "success": True,
        "message": "Monitoring plan created or updated from verified issue.",
        "issue_id": issue_id,
        "monitoring_plan": result["plan"],
        "allowed_capabilities": result["allowed_capabilities"],
    }


@app.get("/monitoring-plans/{record_id}")
def monitoring_plans_for_record(record_id: str):
    _hf_mon_ensure_schema()

    plans = _hf_mon_fetch_all(
        """
        SELECT *
        FROM monitoring_plans
        WHERE record_id = %s
        ORDER BY id ASC
        """,
        (_hf_mon_one_line(record_id),),
    )

    for plan in plans:
        plan["allowed_capabilities"] = _hf_mon_parse_json(plan.get("allowed_capabilities"), [])

    return {
        "success": True,
        "record_id": record_id,
        "count": len(plans),
        "monitoring_plans": plans,
    }


@app.post("/integration-events/mock")
def create_mock_integration_event(request: _HFMonMockEventRequest):
    _hf_mon_ensure_schema()

    plan = None

    if request.monitoring_plan_id:
        plan = _hf_mon_find_plan_by_id(int(request.monitoring_plan_id))

    if not plan and request.source_issue_id:
        plan = _hf_mon_find_plan_by_issue(int(request.source_issue_id))

    record_id = _hf_mon_one_line(request.record_id or "")
    source_issue_id = request.source_issue_id
    monitoring_plan_id = request.monitoring_plan_id

    if plan:
        record_id = record_id or _hf_mon_one_line(plan.get("record_id"))
        source_issue_id = source_issue_id or plan.get("source_issue_id")
        monitoring_plan_id = monitoring_plan_id or plan.get("id")

    event_system = _hf_mon_one_line(request.system or (plan or {}).get("system"))
    event_component = _hf_mon_one_line(request.component or (plan or {}).get("component"))
    event_location = _hf_mon_one_line(request.location or (plan or {}).get("location"))

    raw_payload = request.raw_payload or {
        "source": "manual_mock_event",
        "note": "Created by Monitoring Lifecycle Backend Pass 1 test endpoint.",
    }

    event_id = _hf_mon_execute(
        """
        INSERT INTO integration_events (
          tenant_id,
          property_id,
          record_id,
          user_integration_id,
          monitoring_plan_id,
          source_issue_id,
          source_type,
          provider,
          device_id,
          device_name,
          capability,
          `system`,
          component,
          location,
          title,
          summary,
          severity,
          event_status,
          homeowner_acknowledged,
          homeowner_note,
          raw_payload,
          occurred_at
        )
        VALUES (
          %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s
        )
        """,
        (
            _hf_mon_one_line(request.tenant_id or "lateef-home-inspection"),
            _hf_mon_one_line(request.property_id or (plan or {}).get("property_id") or ""),
            record_id,
            monitoring_plan_id,
            source_issue_id,
            _hf_mon_one_line(request.source_type or "device_event"),
            _hf_mon_one_line(request.provider or "manual"),
            _hf_mon_one_line(request.device_id or ""),
            _hf_mon_one_line(request.device_name or ""),
            _hf_mon_one_line(request.capability or "MANUAL_CHECK"),
            event_system,
            event_component,
            event_location,
            _hf_mon_one_line(request.title or "Manual monitoring event"),
            _hf_mon_safe_text(request.summary or ""),
            _hf_mon_one_line(request.severity or "info"),
            _hf_mon_one_line(request.event_status or "unreviewed"),
            _hf_mon_one_line(request.homeowner_acknowledged or "no"),
            _hf_mon_safe_text(request.homeowner_note or ""),
            _hf_mon_to_json(raw_payload),
            request.occurred_at or _hf_mon_now_string(),
        ),
    )

    event = _hf_mon_fetch_one(
        "SELECT * FROM integration_events WHERE id = %s LIMIT 1",
        (event_id,),
    )

    if event:
        event["raw_payload"] = _hf_mon_parse_json(event.get("raw_payload"), {})

    return {
        "success": True,
        "message": "Mock integration event created.",
        "event": event,
        "linked_monitoring_plan": plan,
    }




@app.patch("/monitoring-event/{event_id}/review")
def review_monitoring_event(event_id: int, payload: _HFMonitoringEventReviewPayload):
    """
    Review a monitoring/integration event.

    Important:
    - This does NOT mutate the locked verified issue baseline.
    - It only updates the event lifecycle state.
    - Locked issues may still receive and review new monitoring events.
    """
    _hf_mon_ensure_schema()
    _hf_mon_ensure_event_review_schema()

    allowed_statuses = {
        "unreviewed",
        "acknowledged",
        "resolved",
        "dismissed",
        "escalated",
        "false_alarm",
    }

    allowed_decisions = {
        "monitor",
        "repair_needed",
        "contractor_needed",
        "resolved",
        "false_alarm",
        "needs_followup",
    }

    event_status = str(payload.event_status or "").strip().lower()
    review_decision = str(payload.review_decision or "").strip().lower()
    review_note = str(payload.review_note or "").strip()
    reviewed_by = str(payload.reviewed_by or "admin").strip() or "admin"

    if not event_status:
        event_status = "acknowledged"

    if event_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "invalid_event_status",
                "message": f"Invalid event_status: {event_status}",
                "allowed_statuses": sorted(allowed_statuses),
            },
        )

    if review_decision and review_decision not in allowed_decisions:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "invalid_review_decision",
                "message": f"Invalid review_decision: {review_decision}",
                "allowed_decisions": sorted(allowed_decisions),
            },
        )

    if not review_decision:
        if event_status == "resolved":
            review_decision = "resolved"
        elif event_status == "false_alarm":
            review_decision = "false_alarm"
        elif event_status == "escalated":
            review_decision = "needs_followup"
        else:
            review_decision = "monitor"

    followup_required = payload.followup_required

    if followup_required is None:
        followup_required = event_status == "escalated" or review_decision in {
            "repair_needed",
            "contractor_needed",
            "needs_followup",
        }

    fields = [
        "event_status = %s",
        "review_decision = %s",
        "review_note = %s",
        "reviewed_by = %s",
        "reviewed_at = NOW()",
        "followup_required = %s",
    ]

    params = [
        event_status,
        review_decision,
        review_note,
        reviewed_by,
        1 if followup_required else 0,
    ]

    if event_status == "resolved":
        fields.append("resolved_at = NOW()")

    if event_status == "escalated":
        fields.append("escalated_at = NOW()")

    params.append(event_id)

    conn = None

    try:
        conn = _hf_mon_get_connection()

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM integration_events
                WHERE id = %s
                LIMIT 1
                """,
                (event_id,),
            )
            existing = cursor.fetchone()

            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "success": False,
                        "error": "monitoring_event_not_found",
                        "message": f"Monitoring event {event_id} was not found.",
                        "event_id": event_id,
                    },
                )

            cursor.execute(
                f"""
                UPDATE integration_events
                SET {", ".join(fields)}
                WHERE id = %s
                """,
                params,
            )

            cursor.execute(
                """
                SELECT *
                FROM integration_events
                WHERE id = %s
                LIMIT 1
                """,
                (event_id,),
            )
            row = cursor.fetchone()

        conn.commit()

        return {
            "success": True,
            "message": "Monitoring event review saved.",
            "event": row,
        }

    except HTTPException:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        raise

    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "monitoring_event_review_failed",
                "message": str(exc),
                "event_id": event_id,
            },
        )

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


# Monitoring Lifecycle Archived Duplicate Filter Pass 1
@app.get("/monitoring-events/{record_id}")
def monitoring_events_for_record(record_id: str):
    _hf_mon_ensure_schema()

    events = _hf_mon_fetch_all(
        """
        SELECT *
        FROM integration_events
        WHERE record_id = %s
          AND COALESCE(event_lifecycle_status, '') <> 'archived_duplicate'
          AND COALESCE(event_status, '') <> 'archived_duplicate'
        ORDER BY COALESCE(occurred_at, created_at) DESC, id DESC
        """,
        (_hf_mon_one_line(record_id),),
    )

    for event in events:
        event["raw_payload"] = _hf_mon_parse_json(event.get("raw_payload"), {})

    return {
        "success": True,
        "record_id": record_id,
        "count": len(events),
        "events": events,
    }


# ============================================================
# HomeFax Monitoring Lifecycle Backend Pass 2
# Final approval route auto-creates monitoring plans for monitored locked issues.
# ============================================================
