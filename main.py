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
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, UploadFile, File
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
        next_issue["verified_image_url"] = make_public_image_url(best_path)
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
        "verified_image_url": issue.get("verified_image_url") or issue.get("image_url") or "",
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
                    "recommendation": "Review this report manually or improve the adapter parser.",
                    "image_url": "",
                    "image_match_status": "none",
                    "image_match_confidence": "no_candidate_found",
                    "needs_image_review": "yes",
                }
            ]

            extracted_issues = findings

        record_id = make_pdf_record_id(filename)

        return {
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
            "issuesWithImagesCount": sum(1 for item in findings if item.get("image_url")),
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
            },
        }

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
        record_id = clean_text(data.record_id)

        if not record_id:
            raise HTTPException(status_code=400, detail="record_id is required")

        findings = [model_to_dict(finding) for finding in (data.findings or [])]

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
        "admin_review_status": row.get("admin_review_status"),
        "admin_note": row.get("admin_note") or "",
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


@app.patch("/verified-issue/{issue_id}/image-verification")
def update_issue_image_verification(issue_id: int, update: ImageVerificationUpdate):
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
