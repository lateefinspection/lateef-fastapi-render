import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


S3_BUCKET = os.getenv(
    "S3_BUCKET_NAME",
    "home-inspection-reports-598120811152-us-east-2-an",
)
S3_REGION = os.getenv("AWS_REGION", "us-east-2")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_IMAGES_DIR = OUTPUT_DIR / "images"


# =========================
# LEGACY COMPATIBILITY
# =========================

def match_image(section: str, record_id: str):
    """
    Legacy placeholder kept for older callers.
    """
    image_id = str(uuid.uuid4())

    return (
        f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/"
        f"approved-images/{record_id}/{image_id}.png"
    )


# =========================
# PATH / URL HELPERS
# =========================

def image_path_to_url(image_path: Optional[str]) -> str:
    """
    Converts local image path into a browser-safe FastAPI URL path.

    Example:
      output/images/page_4_img_1_abc.jpeg
      -> /inspection-images/page_4_img_1_abc.jpeg
    """
    if not image_path:
        return ""

    value = str(image_path).strip()

    if not value:
        return ""

    if value.startswith("http://") or value.startswith("https://"):
        return value

    filename = Path(value).name

    if not filename:
        return ""

    return f"/inspection-images/{filename}"


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None

        return int(value)
    except Exception:
        return None


def _extract_page_from_filename(path: str) -> Optional[int]:
    match = re.search(r"page[_-](\d+)[_-]img", str(path), re.IGNORECASE)

    if not match:
        return None

    return _safe_int(match.group(1))


def _normalize_image_path(value: Any) -> Optional[str]:
    if not value:
        return None

    raw = str(value).strip()

    if not raw:
        return None

    path = Path(raw)

    if path.exists():
        return str(path)

    possible = BASE_DIR / raw

    if possible.exists():
        return str(possible)

    by_name = OUTPUT_IMAGES_DIR / Path(raw).name

    if by_name.exists():
        return str(by_name)

    # Return likely output/images path even if existence check misses.
    return str(by_name)


def _dedupe_list(values: List[Any]) -> List[Any]:
    output = []
    seen = set()

    for value in values or []:
        if value in [None, ""]:
            continue

        key = str(value)

        if key in seen:
            continue

        seen.add(key)
        output.append(value)

    return output


# =========================
# IMAGE COLLECTION
# =========================

def collect_images_by_page(extracted: Optional[Dict[str, Any]] = None) -> Dict[int, List[str]]:
    """
    Collects all extracted PDF images grouped by page.

    Supports both:
    1. Old extracted.json-style metadata.
    2. Current output/images filename scanning.
    """
    images_by_page: Dict[int, List[str]] = {}
    extracted = extracted or {}

    # Shape A: extracted["images"]
    for image in extracted.get("images", []) or []:
        if not isinstance(image, dict):
            continue

        page = _safe_int(
            image.get("page_number")
            or image.get("page")
            or image.get("pageIndex")
            or image.get("page_index")
        )

        image_path = _normalize_image_path(
            image.get("path")
            or image.get("image_path")
            or image.get("file_path")
            or image.get("filename")
            or image.get("name")
        )

        if page is not None and image_path:
            images_by_page.setdefault(page, []).append(image_path)

    # Shape B: extracted["pages"][].images
    for page_obj in extracted.get("pages", []) or []:
        if not isinstance(page_obj, dict):
            continue

        page = _safe_int(
            page_obj.get("page_number")
            or page_obj.get("page")
            or page_obj.get("pageIndex")
            or page_obj.get("page_index")
        )

        if page is None:
            continue

        for image in page_obj.get("images", []) or []:
            if isinstance(image, dict):
                image_path = _normalize_image_path(
                    image.get("path")
                    or image.get("image_path")
                    or image.get("file_path")
                    or image.get("filename")
                    or image.get("name")
                )
            else:
                image_path = _normalize_image_path(image)

            if image_path:
                images_by_page.setdefault(page, []).append(image_path)

    # Fallback: scan output/images.
    if OUTPUT_IMAGES_DIR.exists():
        image_files = sorted(
            list(OUTPUT_IMAGES_DIR.glob("*.png"))
            + list(OUTPUT_IMAGES_DIR.glob("*.jpg"))
            + list(OUTPUT_IMAGES_DIR.glob("*.jpeg"))
            + list(OUTPUT_IMAGES_DIR.glob("*.webp"))
        )

        for path in image_files:
            page = _extract_page_from_filename(path.name)

            if page is not None:
                images_by_page.setdefault(page, []).append(str(path))

    deduped: Dict[int, List[str]] = {}

    for page, paths in images_by_page.items():
        deduped[page] = _dedupe_list(paths)

    return deduped


# =========================
# MATCHING / SCORING
# =========================

def _score_image_for_issue(image_path: str, issue_title: str = "", issue_text: str = "") -> int:
    """
    Scores image candidates.

    This is still not final vision verification.
    It is a smarter candidate ranking layer for homeowner/admin review.
    """
    filename = Path(image_path).name.lower()
    source = f"{issue_title or ''} {issue_text or ''}".lower()

    score = 10

    # Penalize repeated logo/generic placeholder hash seen in previous reports.
    if "f98855376075" in filename:
        score -= 8

    # Prefer photos over PNG icons/logos when tied.
    if filename.endswith((".jpg", ".jpeg")):
        score += 3

    if filename.endswith(".png"):
        score -= 1

    topic_groups = {
        "roof": ["roof", "shingle", "flashing", "chimney", "gutter", "downspout"],
        "plumbing": ["water", "plumbing", "valve", "pipe", "drain", "leak", "toilet", "faucet"],
        "electrical": ["electric", "gfci", "breaker", "panel", "wiring", "receptacle", "outlet"],
        "exterior": ["exterior", "siding", "wall", "covering", "trim", "window", "door"],
        "structure": ["foundation", "crawlspace", "basement", "joist", "beam", "structure"],
        "hvac": ["hvac", "furnace", "cooling", "heating", "air conditioner", "duct"],
    }

    for words in topic_groups.values():
        if any(word in source for word in words):
            score += 3
            break

    return score


def match_images_for_issue(
    issue_title: str,
    summary_page: Any,
    images_by_page: Dict[int, List[str]],
    detail_page: Any = None,
    issue_text: str = "",
) -> Tuple[List[str], List[str], Optional[str], str]:
    """
    Returns:
      candidate_image_paths
      all_page_image_paths
      suggested_image_path
      image_match_confidence

    Important:
      suggested_image_path is NOT admin verified.
    """
    summary_page = _safe_int(summary_page)
    detail_page = _safe_int(detail_page)

    if not images_by_page:
        return [], [], None, "no_images_available"

    candidate_pages: List[int] = []

    if detail_page is not None:
        candidate_pages.extend([detail_page, detail_page + 1, detail_page - 1])

    if summary_page is not None:
        candidate_pages.extend(
            [
                summary_page,
                summary_page + 1,
                summary_page - 1,
                summary_page + 2,
                summary_page - 2,
            ]
        )

    if not candidate_pages:
        candidate_pages = sorted(images_by_page.keys())[:5]

    ordered_pages = []

    for page in candidate_pages:
        if page is None:
            continue

        if page not in ordered_pages:
            ordered_pages.append(page)

    candidate_image_paths: List[str] = []

    for page in ordered_pages:
        for image_path in images_by_page.get(page, []):
            if image_path not in candidate_image_paths:
                candidate_image_paths.append(image_path)

    primary_page = detail_page if detail_page is not None else summary_page
    all_page_image_paths = images_by_page.get(primary_page, []) if primary_page is not None else []

    if not candidate_image_paths:
        return [], all_page_image_paths, None, "no_candidate_found"

    ranked = sorted(
        candidate_image_paths,
        key=lambda path: _score_image_for_issue(path, issue_title, issue_text),
        reverse=True,
    )

    suggested_image_path = ranked[0] if ranked else None

    if detail_page is not None:
        confidence = "detail_page_candidate"
    elif summary_page is not None:
        confidence = "summary_page_candidate"
    else:
        confidence = "nearby_page_candidate"

    return ranked[:10], all_page_image_paths, suggested_image_path, confidence


# =========================
# HYBRID ATTACHMENT
# =========================

def attach_images_to_issues(
    issues: List[Dict[str, Any]],
    extracted: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Hybrid Extraction + Restored Image Matching Pass 1.

    Input:
      complete current parser issues, usually 32-44 findings

    Output:
      same issues enriched with:
        image_url
        suggested_image_url
        candidate_image_paths
        candidate_image_urls
        all_page_image_paths
        all_page_image_urls
        image_match_status
        image_match_confidence
        needs_image_review

    Product rule:
      image_url is suggested evidence.
      verified_image_url remains empty until admin approves.
    """
    images_by_page = collect_images_by_page(extracted)
    enriched_issues: List[Dict[str, Any]] = []

    for issue in issues or []:
        if not isinstance(issue, dict):
            continue

        issue_title = (
            issue.get("issueTitle")
            or issue.get("issue_title")
            or issue.get("title")
            or issue.get("findingTitle")
            or issue.get("type")
            or ""
        )

        issue_text = " ".join(
            str(issue.get(key) or "")
            for key in [
                "description",
                "summary",
                "notes",
                "system",
                "component",
                "location",
                "recommendation",
            ]
        )

        summary_page = (
            issue.get("summary_page")
            or issue.get("page")
            or issue.get("page_number")
        )

        detail_page = (
            issue.get("detail_page")
            or issue.get("photo_page")
            or issue.get("image_page")
        )

        (
            candidate_image_paths,
            all_page_image_paths,
            suggested_image_path,
            confidence,
        ) = match_images_for_issue(
            issue_title=issue_title,
            summary_page=summary_page,
            detail_page=detail_page,
            images_by_page=images_by_page,
            issue_text=issue_text,
        )

        candidate_image_paths = _dedupe_list(candidate_image_paths)
        all_page_image_paths = _dedupe_list(all_page_image_paths)

        candidate_image_urls = _dedupe_list(
            [
                image_path_to_url(path)
                for path in candidate_image_paths
                if image_path_to_url(path)
            ]
        )

        all_page_image_urls = _dedupe_list(
            [
                image_path_to_url(path)
                for path in all_page_image_paths
                if image_path_to_url(path)
            ]
        )

        suggested_image_url = image_path_to_url(suggested_image_path)

        enriched = dict(issue)

        enriched["candidate_image_paths"] = candidate_image_paths
        enriched["candidate_image_urls"] = candidate_image_urls
        enriched["all_page_image_paths"] = all_page_image_paths
        enriched["all_page_image_urls"] = all_page_image_urls

        enriched["suggested_image_path"] = suggested_image_path or ""
        enriched["suggested_image_url"] = suggested_image_url

        # Keep compatibility with existing dashboard/API:
        # image_url is the suggested display image.
        enriched["image_url"] = suggested_image_url

        # Do not call it verified until admin approves.
        enriched["verified_image_path"] = ""
        enriched["verified_image_url"] = ""

        if suggested_image_url:
            enriched["image_match_status"] = "suggested"
            enriched["image_match_confidence"] = confidence
            enriched["needs_image_review"] = "yes"
        else:
            enriched["image_match_status"] = "none"
            enriched["image_match_confidence"] = confidence
            enriched["needs_image_review"] = "yes"

        enriched_issues.append(enriched)

    return enriched_issues
