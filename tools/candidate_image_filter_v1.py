"""
HomeFax Candidate Image Filter v1

Purpose:
Remove obvious decorative/report assets from candidate image lists while preserving
real inspection evidence candidates.

Important product rules:
- Do not delete S3 files.
- Do not set verified_image_url.
- Do not mark images verified.
- Keep candidate_image_urls as an array.
- Preserve at least one candidate if filtering would empty the list.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple


DECORATIVE_EXTENSIONS = {".png"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".webp"}

# Known repeated report-artifact hashes seen in the Big Ben / InterNACHI sample.
# These are commonly logo/checkmark/template assets, not inspection evidence photos.
KNOWN_DECORATIVE_HASH_FRAGMENTS = {
    "9c7e25779a00",
    "105e4b1fa173",
    "f644af53afa7",
    "e3f2521b5547",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def image_basename(url: str) -> str:
    value = clean_text(url)
    if not value:
        return ""
    return value.split("?")[0].rstrip("/").split("/")[-1]


def image_extension(url: str) -> str:
    base = image_basename(url).lower()
    _, ext = os.path.splitext(base)
    return ext


def extract_image_hash(url: str) -> str:
    """
    Expected patterns:
      page_24_img_3_105e4b1fa173.png
      page_25_img_1_c47a7e12690a.jpeg
    """
    base = image_basename(url)
    stem, _ = os.path.splitext(base)

    if "_" not in stem:
        return ""

    return stem.split("_")[-1].lower()


def looks_like_decorative_image(
    url: str,
    repeated_hashes: Iterable[str] | None = None,
) -> Tuple[bool, str]:
    """
    Conservative heuristic.

    Decorative when:
    - URL is blank
    - image hash is a known decorative/report asset
    - image is PNG and the same hash repeats in the candidate set

    JPEG/JPG images are preferred as inspection evidence.
    """
    value = clean_text(url)
    if not value:
        return True, "blank_url"

    ext = image_extension(value)
    img_hash = extract_image_hash(value)
    repeated = set(repeated_hashes or [])

    if img_hash in KNOWN_DECORATIVE_HASH_FRAGMENTS:
        return True, "known_decorative_hash"

    if ext in DECORATIVE_EXTENSIONS and img_hash in repeated:
        return True, "repeated_png_hash"

    return False, "kept"


def filter_candidate_image_urls(candidate_urls: Any, keep_minimum: int = 1) -> List[str]:
    """
    Filters a candidate_image_urls value safely.

    Input can be:
    - list[str]
    - None
    - accidental string

    Output is always:
    - list[str]
    """
    if candidate_urls is None:
        return []

    if isinstance(candidate_urls, str):
        urls = [candidate_urls]
    elif isinstance(candidate_urls, list):
        urls = [clean_text(item) for item in candidate_urls if clean_text(item)]
    else:
        urls = []

    if not urls:
        return []

    hashes = [extract_image_hash(url) for url in urls if extract_image_hash(url)]
    hash_counts = Counter(hashes)

    repeated_hashes = {
        img_hash
        for img_hash, count in hash_counts.items()
        if count >= 2
    }

    kept: List[str] = []

    for url in urls:
        is_decorative, _reason = looks_like_decorative_image(
            url,
            repeated_hashes=repeated_hashes,
        )

        if not is_decorative:
            kept.append(url)

    # Safety: never erase all candidates if there were candidates.
    if not kept and keep_minimum > 0:
        photo_candidates = [
            url for url in urls
            if image_extension(url).lower() in PHOTO_EXTENSIONS
        ]

        if photo_candidates:
            kept = photo_candidates[:keep_minimum]
        else:
            kept = urls[:keep_minimum]

    return kept


def summarize_candidate_image_filter(candidate_urls: Any) -> Dict[str, Any]:
    """
    Gives a preview/audit summary without modifying the database.
    """
    if candidate_urls is None:
        original = []
    elif isinstance(candidate_urls, str):
        original = [candidate_urls]
    elif isinstance(candidate_urls, list):
        original = [clean_text(item) for item in candidate_urls if clean_text(item)]
    else:
        original = []

    filtered = filter_candidate_image_urls(original)
    removed = [url for url in original if url not in filtered]

    return {
        "original_count": len(original),
        "filtered_count": len(filtered),
        "removed_count": len(removed),
        "removed_urls": removed,
        "filtered_urls": filtered,
    }


def clean_issue_candidate_images(issue: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns a copy of an issue with cleaned candidate image arrays.

    Does not verify images.
    Does not lock baseline.
    """
    cleaned = dict(issue)

    candidate_urls = cleaned.get("candidate_image_urls") or cleaned.get("candidate_images") or []
    filtered_candidates = filter_candidate_image_urls(candidate_urls)

    cleaned["candidate_image_urls"] = filtered_candidates

    all_page_urls = cleaned.get("all_page_image_urls")
    if isinstance(all_page_urls, list):
        cleaned["all_page_image_urls"] = filter_candidate_image_urls(
            all_page_urls,
            keep_minimum=0,
        )

    current_image = clean_text(cleaned.get("image_url"))
    if current_image:
        is_decorative, _reason = looks_like_decorative_image(current_image)

        if is_decorative and filtered_candidates:
            cleaned["image_url"] = filtered_candidates[0]

    return cleaned
