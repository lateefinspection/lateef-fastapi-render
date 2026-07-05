#!/usr/bin/env python3

"""
Audit candidate image cleanup against a live HomeFax record.

This does not write to the database.
It only reports what would be removed.
"""

import json
import sys
import urllib.request

from tools.candidate_image_filter_v1 import summarize_candidate_image_filter


API_BASE = "https://lateef-fastapi-docker.onrender.com"


def fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    record_id = sys.argv[1] if len(sys.argv) > 1 else "pdf-url-path-prod-smoke-001"

    url = f"{API_BASE}/verified-issues/{record_id}"
    data = fetch_json(url)

    if isinstance(data, dict):
        issues = data.get("issues") or []
    elif isinstance(data, list):
        issues = data
    else:
        issues = []

    print("record_id:", record_id)
    print("issues_count:", len(issues))

    total_original = 0
    total_filtered = 0
    total_removed = 0

    examples = []

    for issue in issues:
        candidate_urls = issue.get("candidate_image_urls") or []
        summary = summarize_candidate_image_filter(candidate_urls)

        total_original += summary["original_count"]
        total_filtered += summary["filtered_count"]
        total_removed += summary["removed_count"]

        if summary["removed_count"] and len(examples) < 10:
            examples.append(
                {
                    "id": issue.get("id"),
                    "title": issue.get("title"),
                    "original_count": summary["original_count"],
                    "filtered_count": summary["filtered_count"],
                    "removed_count": summary["removed_count"],
                    "removed_urls": summary["removed_urls"][:5],
                    "filtered_urls": summary["filtered_urls"][:5],
                }
            )

    print("candidate_urls_original_total:", total_original)
    print("candidate_urls_filtered_total:", total_filtered)
    print("candidate_urls_removed_total:", total_removed)

    print()
    print("examples:")
    print(json.dumps(examples, indent=2))


if __name__ == "__main__":
    main()
