# HomeFax Intake Standard n8n Code Node Templates v1

## Purpose

This document indexes the n8n Code node templates for HomeFax Intake Standard v1.

These templates support the workflow:

Webhook Intake
→ Normalize Intake Metadata
→ Ensure Record ID
→ FastAPI Analyze Report
→ FastAPI Process Inspection
→ Intake Standard Preview
→ Extract Payload
→ Validate Payload
→ Return Dashboard URL or Validation Errors

## Confirmed workflow sync note

During HomeFax Intake Standard n8n Workflow Build Pass 1, the live n8n workflow proved that response nodes must preserve `record_id` from the original Webhook body.

The working Code node pattern is:

```javascript
$("Webhook").first().json.body

---

## Template files

| Node | File |
|---|---|
| Normalize Intake Metadata | `n8n/code_nodes/normalize_intake_metadata_v1.js` |
| Ensure Record ID | `n8n/code_nodes/ensure_record_id_v1.js` |
| Extract Payload | `n8n/code_nodes/extract_payload_v1.js` |
| Build Success Response | `n8n/code_nodes/build_success_response_v1.js` |
| Build Validation Error Response | `n8n/code_nodes/build_validation_error_response_v1.js` |

---

## n8n guardrails

n8n must not:

- final approve issues
- baseline lock issues
- verify images
- set `verified_image_url` for suggested images
- convert `candidate_image_urls` into a string
- remove source finding text
- overwrite homeowner decisions
- overwrite admin decisions

---

## Usage

Open each `.js` file, copy the contents, and paste it into the matching n8n Code node.

Recommended n8n Code node mode:

`Run Once for All Items`

---

## Success criteria

This pass is complete when:

- all five code node template files exist
- this index document exists
- JavaScript syntax checks pass
- files are committed
