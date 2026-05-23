#!/usr/bin/env python3
"""
HomeFax Image Intelligence Pass 1

Purpose:
- Audit image candidates attached to verified issues.
- Detect junk, duplicates, blank images, placeholder-like graphics, and weak candidates.
- Produce a JSON and text report before changing backend/database/dashboard behavior.

This script is read-only. It does not update the database.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from PIL import Image, ImageFilter, ImageStat


DEFAULT_BASE_URL = os.getenv(
    "HOMEFAX_API_BASE_URL",
    "https://lateef-fastapi-docker.onrender.com",
).rstrip("/")


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "record"


def join_url(base_url: str, path_or_url: str) -> str:
    if not path_or_url:
        return ""
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if not path_or_url.startswith("/"):
        path_or_url = "/" + path_or_url
    return base_url.rstrip("/") + path_or_url


def issue_text(issue: Dict[str, Any]) -> str:
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
    return " ".join(safe_text(p) for p in parts).lower()


def classify_issue(issue: Dict[str, Any]) -> str:
    text = issue_text(issue)

    if any(k in text for k in ["gfci", "gfcis", "afci", "electrical", "breaker", "panel", "wiring", "meter", "disconnect"]):
        return "electrical"

    if any(k in text for k in ["plumbing", "leak", "water", "valve", "supply", "shut", "pipe", "drain", "sink", "hot water", "water heater"]):
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


def fetch_json(url: str, timeout: int = 60) -> Dict[str, Any]:
    res = requests.get(url, timeout=timeout)
    try:
        data = res.json()
    except Exception as exc:
        raise RuntimeError(f"Non-JSON response from {url}: status={res.status_code} preview={res.text[:300]!r}") from exc

    if not res.ok:
        raise RuntimeError(f"Request failed: status={res.status_code} data={data}")

    return data


def fetch_image_bytes(url: str, timeout: int = 60) -> bytes:
    res = requests.get(url, timeout=timeout)
    if not res.ok:
        raise RuntimeError(f"Image request failed: status={res.status_code} url={url} preview={res.text[:200]!r}")

    content_type = res.headers.get("content-type", "")
    data = res.content or b""

    if "image" not in content_type.lower():
        raise RuntimeError(f"Not an image: content_type={content_type!r} url={url} bytes={len(data)}")

    return data


def image_ahash(img: Image.Image, size: int = 8) -> str:
    gray = img.convert("L").resize((size, size))
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p >= avg else "0" for p in pixels)
    return f"{int(bits, 2):016x}"


def hamming_hex(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 64


def entropy_from_histogram(hist: List[int], total: int) -> float:
    entropy = 0.0
    if total <= 0:
        return 0.0

    for count in hist:
        if count:
            p = count / total
            entropy -= p * math.log2(p)

    return entropy


@dataclass
class ImageMetrics:
    ok: bool
    url: str
    full_url: str
    bytes_len: int = 0
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0
    mean_brightness: float = 0.0
    stddev_brightness: float = 0.0
    entropy: float = 0.0
    edge_mean: float = 0.0
    black_white_ratio: float = 0.0
    midtone_ratio: float = 0.0
    ahash: str = ""
    sha1: str = ""
    quality_score: int = 0
    reject_reasons: List[str] = None
    warning_reasons: List[str] = None
    error: str = ""

    def __post_init__(self):
        if self.reject_reasons is None:
            self.reject_reasons = []
        if self.warning_reasons is None:
            self.warning_reasons = []


def analyze_image(url: str, full_url: str, data: bytes) -> ImageMetrics:
    metrics = ImageMetrics(
        ok=True,
        url=url,
        full_url=full_url,
        bytes_len=len(data),
        sha1=hashlib.sha1(data).hexdigest(),
        quality_score=100,
    )

    try:
        img = Image.open(BytesIO(data))
        img = img.convert("RGB")
        metrics.width, metrics.height = img.size
        metrics.aspect_ratio = round(metrics.width / metrics.height, 3) if metrics.height else 0.0

        gray = img.convert("L")
        stat = ImageStat.Stat(gray)
        metrics.mean_brightness = round(stat.mean[0], 2)
        metrics.stddev_brightness = round(stat.stddev[0], 2)

        hist = gray.histogram()
        total = metrics.width * metrics.height
        metrics.entropy = round(entropy_from_histogram(hist, total), 3)

        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(edges)
        metrics.edge_mean = round(edge_stat.mean[0], 2)

        pixels = list(gray.resize((128, 128)).getdata())
        total_small = len(pixels)
        black_white = sum(1 for p in pixels if p <= 25 or p >= 230)
        midtone = sum(1 for p in pixels if 40 < p < 215)

        metrics.black_white_ratio = round(black_white / total_small, 3)
        metrics.midtone_ratio = round(midtone / total_small, 3)
        metrics.ahash = image_ahash(img)

        # Hard rejects
        if metrics.bytes_len < 2500:
            metrics.reject_reasons.append("very_small_file")

        if metrics.width < 140 or metrics.height < 100:
            metrics.reject_reasons.append("very_small_dimensions")

        if metrics.stddev_brightness < 6:
            metrics.reject_reasons.append("near_blank_low_detail")

        if metrics.entropy < 2.0:
            metrics.reject_reasons.append("low_entropy_low_information")

        if metrics.black_white_ratio > 0.82 and metrics.midtone_ratio < 0.18:
            metrics.reject_reasons.append("black_white_placeholder_like")

        if metrics.aspect_ratio > 5 or metrics.aspect_ratio < 0.2:
            metrics.reject_reasons.append("extreme_aspect_ratio")

        # Warnings
        if metrics.edge_mean < 4:
            metrics.warning_reasons.append("weak_edges_low_visual_detail")

        if metrics.stddev_brightness < 15:
            metrics.warning_reasons.append("low_contrast")

        if metrics.entropy < 4:
            metrics.warning_reasons.append("low_visual_information")

        # Score penalties
        metrics.quality_score -= 35 * len(metrics.reject_reasons)
        metrics.quality_score -= 10 * len(metrics.warning_reasons)

        # Reward usable size/detail
        if metrics.width >= 300 and metrics.height >= 200:
            metrics.quality_score += 5

        if metrics.entropy >= 5 and metrics.edge_mean >= 8:
            metrics.quality_score += 8

        metrics.quality_score = max(0, min(100, metrics.quality_score))

        if metrics.reject_reasons:
            metrics.ok = False

    except Exception as exc:
        metrics.ok = False
        metrics.error = str(exc)
        metrics.reject_reasons.append("image_decode_error")
        metrics.quality_score = 0

    return metrics


def score_candidates_for_issue(
    issue: Dict[str, Any],
    base_url: str,
    max_download: int,
    sleep_seconds: float,
) -> Dict[str, Any]:
    issue_id = issue.get("id")
    category = classify_issue(issue)

    raw_candidates = []
    if issue.get("image_url"):
        raw_candidates.append(issue.get("image_url"))

    for url in issue.get("candidate_image_urls") or []:
        if url and url not in raw_candidates:
            raw_candidates.append(url)

    candidates = []
    seen_sha1 = {}
    seen_ahash = {}

    for idx, url in enumerate(raw_candidates[:max_download]):
        full_url = join_url(base_url, url)

        try:
            data = fetch_image_bytes(full_url)
            metrics = analyze_image(url, full_url, data)

            duplicate_of = None

            if metrics.sha1 in seen_sha1:
                duplicate_of = seen_sha1[metrics.sha1]
            else:
                seen_sha1[metrics.sha1] = url

            for existing_hash, existing_url in seen_ahash.items():
                if metrics.ahash and hamming_hex(metrics.ahash, existing_hash) <= 4:
                    duplicate_of = duplicate_of or existing_url
                    break

            if metrics.ahash:
                seen_ahash.setdefault(metrics.ahash, url)

            if duplicate_of:
                metrics.ok = False
                metrics.reject_reasons.append("duplicate_or_near_duplicate")
                metrics.quality_score = max(0, metrics.quality_score - 45)

            result = asdict(metrics)
            result["candidate_index"] = idx
            result["duplicate_of"] = duplicate_of
            result["issue_category"] = category

        except Exception as exc:
            result = asdict(ImageMetrics(
                ok=False,
                url=url,
                full_url=full_url,
                error=str(exc),
                quality_score=0,
                reject_reasons=["download_or_analysis_failed"],
                warning_reasons=[],
            ))
            result["candidate_index"] = idx
            result["duplicate_of"] = None
            result["issue_category"] = category

        candidates.append(result)

        if sleep_seconds:
            time.sleep(sleep_seconds)

    usable = [c for c in candidates if c.get("ok")]
    rejected = [c for c in candidates if not c.get("ok")]

    ranked = sorted(
        candidates,
        key=lambda c: (
            1 if c.get("ok") else 0,
            c.get("quality_score", 0),
            -c.get("candidate_index", 999),
        ),
        reverse=True,
    )

    top_usable = [c for c in ranked if c.get("ok")][:5]

    return {
        "id": issue_id,
        "record_id": issue.get("record_id"),
        "title": issue.get("title"),
        "section": issue.get("section"),
        "severity": issue.get("severity"),
        "issue_category": category,
        "image_url": issue.get("image_url"),
        "verified_image_url": issue.get("verified_image_url"),
        "candidate_count_raw": len(raw_candidates),
        "candidate_count_analyzed": len(candidates),
        "usable_count": len(usable),
        "rejected_count": len(rejected),
        "top_usable_urls": [c.get("url") for c in top_usable],
        "candidates": ranked,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--max-issues", type=int, default=0, help="0 means all")
    parser.add_argument("--max-download", type=int, default=12)
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    record_id = args.record_id

    verified_url = f"{base_url}/verified-issues/{record_id}"
    print(f"[INFO] Fetching verified issues: {verified_url}", file=sys.stderr)

    data = fetch_json(verified_url)
    issues = data.get("issues") or []

    if args.max_issues and args.max_issues > 0:
        issues = issues[:args.max_issues]

    print(f"[INFO] Issues to audit: {len(issues)}", file=sys.stderr)

    audited = []

    for i, issue in enumerate(issues, start=1):
        print(
            f"[INFO] [{i}/{len(issues)}] issue_id={issue.get('id')} title={issue.get('title')!r}",
            file=sys.stderr,
        )
        audited.append(
            score_candidates_for_issue(
                issue=issue,
                base_url=base_url,
                max_download=args.max_download,
                sleep_seconds=args.sleep,
            )
        )

    output = {
        "success": True,
        "record_id": record_id,
        "base_url": base_url,
        "issues_count": len(audited),
        "summary": {
            "total_candidates_analyzed": sum(x["candidate_count_analyzed"] for x in audited),
            "total_usable": sum(x["usable_count"] for x in audited),
            "total_rejected": sum(x["rejected_count"] for x in audited),
            "issues_with_no_usable_candidates": sum(1 for x in audited if x["usable_count"] == 0),
        },
        "issues": audited,
    }

    out_base = slug(record_id)
    json_path = Path(f"/tmp/homefax_image_intelligence_audit_{out_base}.json")
    txt_path = Path(f"/tmp/homefax_image_intelligence_summary_{out_base}.txt")

    json_path.write_text(json.dumps(output, indent=2))

    lines = []
    lines.append("HomeFax Image Intelligence Pass 1 Audit")
    lines.append("=" * 48)
    lines.append(f"record_id: {record_id}")
    lines.append(f"issues_count: {len(audited)}")
    lines.append(f"total_candidates_analyzed: {output['summary']['total_candidates_analyzed']}")
    lines.append(f"total_usable: {output['summary']['total_usable']}")
    lines.append(f"total_rejected: {output['summary']['total_rejected']}")
    lines.append(f"issues_with_no_usable_candidates: {output['summary']['issues_with_no_usable_candidates']}")
    lines.append("")

    for issue in audited:
        lines.append(f"Issue #{issue['id']} - {issue.get('title')}")
        lines.append(f"  section: {issue.get('section')}")
        lines.append(f"  category: {issue.get('issue_category')}")
        lines.append(f"  raw candidates: {issue.get('candidate_count_raw')}")
        lines.append(f"  analyzed: {issue.get('candidate_count_analyzed')} usable: {issue.get('usable_count')} rejected: {issue.get('rejected_count')}")
        lines.append("  top usable:")
        for url in issue.get("top_usable_urls") or []:
            lines.append(f"    - {url}")

        worst = [c for c in issue.get("candidates", []) if not c.get("ok")][:3]
        if worst:
            lines.append("  rejected examples:")
            for c in worst:
                lines.append(f"    - index={c.get('candidate_index')} score={c.get('quality_score')} reasons={','.join(c.get('reject_reasons') or [])} url={c.get('url')}")
        lines.append("")

    txt_path.write_text("\n".join(lines))

    print(f"[OK] Wrote JSON: {json_path}")
    print(f"[OK] Wrote summary: {txt_path}")
    print("")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
