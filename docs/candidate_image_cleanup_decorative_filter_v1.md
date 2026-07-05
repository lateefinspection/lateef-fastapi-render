# HomeFax Candidate Image Cleanup / Decorative Image Filter v1

## Purpose

HomeFax inspection PDFs often contain both real inspection photos and decorative/report assets such as logos, checkmarks, repeated icons, or report template graphics.

This filter removes obvious decorative/report assets from `candidate_image_urls` while preserving real inspection evidence candidates.

## Product Safety Rules

This filter must never:

- delete S3 files
- set `verified_image_url`
- mark an image as verified
- baseline-lock a finding
- remove all candidates if candidates existed
- change homeowner/admin review state

This filter may:

- reduce `candidate_image_urls`
- reduce `all_page_image_urls`
- replace `image_url` only when the current suggested image is obviously decorative and a cleaner candidate exists

## Files Added

```text
tools/candidate_image_filter_v1.py
scripts/audit_candidate_image_filter_v1.py
scripts/__init__.py
