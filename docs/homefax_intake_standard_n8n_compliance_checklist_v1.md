# HomeFax Intake Standard n8n Compliance Checklist v1

## Purpose

This checklist defines what n8n must preserve and validate when coordinating HomeFax inspection intake.

n8n is an orchestration layer. It may receive uploads, call FastAPI, format payloads, call validation, trigger async finalization, and return dashboard URLs.

n8n must not act as the final reviewer, final approver, baseline locker, or verified image authority.

---

## Primary n8n responsibilities

n8n may:

- receive homeowner upload metadata
- receive original PDF URL or binary handoff
- call FastAPI `/analyze-report/`
- call FastAPI `/process-inspection`
- call FastAPI `/records/{record_id}/homefax-intake-standard-preview-v1`
- call FastAPI `/homefax-intake-standard/validate-payload`
- trigger async S3 image finalization
- return `dashboard_url`
- store workflow execution status
- retry failed processing safely

---

## n8n must preserve

- `record_id`
- `tenant_id`
- `property.address_full`
- `homeowner.email`
- `original_report.file_name`
- `original_report.file_url`
- `standard_findings`
- `source.source_item_number`
- `source.source_finding_text`
- `source.source_page`
- `evidence.candidate_image_urls`
- `evidence.image_match_status`
- `review_state`
- `admin_state`
- `monitoring`

---

## Critical image rules

n8n must keep this image contract:

```text
image_url / primary_image_url = suggested evidence
candidate_image_urls = possible evidence images
verified_image_url = blank until admin approval
image_match_status = suggested until admin image verification
