# HomeFax Intake Standard n8n Workflow Node Plan v1

## Purpose

This document defines the n8n workflow node plan for HomeFax Intake Standard v1.

n8n receives intake data, calls FastAPI, validates the HomeFax Intake Standard payload, branches success or failure, and returns either a dashboard URL or validation errors.

n8n must not final approve findings, baseline lock findings, verify images, or overwrite homeowner/admin review state.

---

## Workflow name

homefax-intake-standard-v1

---

## High-level flow

Webhook Intake
→ Normalize Intake Metadata
→ Ensure Record ID
→ Call FastAPI Analyze Report
→ Call FastAPI Process Inspection
→ Call Intake Standard Preview
→ Extract Payload
→ Validate Payload
→ IF Payload Valid
→ Return Dashboard URL
→ Trigger Async S3 Finalization

If invalid:
→ Return Validation Errors
→ Log Failed Intake

---

## Node 1 — Webhook Intake

Type: Webhook  
Method: POST  
Path: /homefax-intake-standard-v1

Purpose: Receive homeowner upload metadata or test payload metadata from Zite, Fillout, manual admin upload, or test tools.

---

## Node 2 — Normalize Intake Metadata

Type: Code

Purpose: Create a consistent metadata object before calling FastAPI.

Must output:

- record_id
- tenant_id
- source_system
- source_workflow
- property.address_full
- homeowner.email
- original_report.file_name
- original_report.file_url

---

## Node 3 — Ensure Record ID

Type: Code

Purpose: Guarantee all later nodes use the same stable record_id.

Rules:

- Do not regenerate record_id after this node.
- Pass the same record_id to FastAPI and dashboard URL.

---

## Node 4 — Call FastAPI Analyze Report

Type: HTTP Request  
Method: POST  
Endpoint: /analyze-report/

Purpose: Send the PDF to FastAPI for extraction, adapter detection, issue parsing, and image candidate matching.

---

## Node 5 — Call FastAPI Process Inspection

Type: HTTP Request  
Method: POST  
Endpoint: /process-inspection

Purpose: Store extracted findings as verified issues / HomeFax standard findings.

Rules:

- Preserve candidate_image_urls as an array.
- Do not set verified_image_url.
- Do not baseline lock.
- Do not final approve.

---

## Node 6 — Call Intake Standard Preview

Type: HTTP Request  
Method: GET  
Endpoint: /records/{{record_id}}/homefax-intake-standard-preview-v1?limit=100

Purpose: Ask FastAPI to return the official HomeFax Intake Standard v1 payload.

---

## Node 7 — Extract Payload

Type: Code

Purpose: Extract response.payload from the preview response.

Rules:

- If success is not true, stop and return error.
- If payload is missing, stop and return error.
- Pass only the payload object into validation.

---

## Node 8 — Validate Payload

Type: HTTP Request  
Method: POST  
Endpoint: /homefax-intake-standard/validate-payload

Purpose: Validate the HomeFax Intake Standard payload.

---

## Node 9 — IF Payload Valid

Type: IF

Condition:

- payload_valid equals true
- errors_count equals 0

Valid path:

- return dashboard URL
- trigger async S3 finalization

Invalid path:

- return validation errors
- mark workflow failed

---

## Node 10 — Return Dashboard URL

Type: Respond to Webhook

Purpose: Return a fast successful response with record_id and dashboard_url.

---

## Node 11 — Return Validation Errors

Type: Respond to Webhook

Purpose: Return a clear failure response if validation fails.

---

## Node 12 — Trigger Async S3 Finalization

Type: HTTP Request  
Method: POST  
Endpoint: /records/{{record_id}}/finalize-s3-images

Purpose: Finalize image storage after the dashboard URL is already returned.

---

## Critical n8n guardrails

n8n must not:

- final approve issues
- baseline lock issues
- verify images
- set verified_image_url for suggested images
- convert candidate_image_urls into a string
- remove source finding text
- overwrite homeowner decisions
- overwrite admin decisions
