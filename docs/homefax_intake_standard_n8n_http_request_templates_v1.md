# HomeFax Intake Standard n8n HTTP Request Templates v1

## Purpose

This document defines the n8n HTTP Request node settings for HomeFax Intake Standard v1.

These templates support the workflow:

Webhook Intake
→ Normalize Intake Metadata
→ Ensure Record ID
→ Call FastAPI Analyze Report
→ Call FastAPI Process Inspection
→ Call Intake Standard Preview
→ Extract Payload
→ Validate Payload
→ Return Dashboard URL or Validation Errors
→ Trigger Async S3 Finalization

---

## Shared FastAPI base URL

Local development:

```text
http://127.0.0.1:8000

