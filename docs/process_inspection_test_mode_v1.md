# HomeFax Process Inspection Test Mode v1

## Purpose

/process-inspection stores extracted inspection findings as HomeFax verified issues.

In normal production mode, high and critical findings can trigger owner/admin notification behavior.

For development, QA, n8n smoke tests, Render deployment tests, and parser validation, HomeFax uses a safe test mode that creates records without sending homeowner/admin notification behavior.

## Test Mode Contract

A test-mode request must include these fields:

processing_mode: test
skip_notifications: true

## Backend Behavior

When processing_mode is test, the backend forces skip_notifications to true.

This allows the full storage pipeline to run while suppressing notification behavior.

## What Test Mode Still Does

- creates verified issue rows
- creates alert rows
- creates automation task rows
- updates tenant/property metadata
- rewrites S3 image URLs
- runs candidate image cleanup
- supports dashboard review and QA

## What Test Mode Suppresses

- notify_record_owner(...)
- homeowner/admin notification behavior for high and critical findings

Expected server logs:

NOTIFICATION INFO: notifications suppressed for record_id=<record_id>
NOTIFICATION SUPPRESSED record_id=<record_id> severity=high title=<title>

The logs should not show NOTIFY_RECORD_OWNER for a test-mode record.

## Request Fields

InspectionProcessRequest includes:

processing_mode: Optional[str] = ""
skip_notifications: bool = False

## Local Proof

Local record:
test-mode-notification-suppression-local-001

Local /process-inspection returned:
HTTP/1.1 200 OK
success: true
findings_count: 44
alerts_created: 41
tasks_created: 41
verified_issues_created: 44
verified_issues_existing: 0

Local logs confirmed notification suppression.

## Production Proof

Production record:
test-mode-notification-suppression-prod-001

Production /process-inspection returned:
HTTP/2 200
success: true
record_id: test-mode-notification-suppression-prod-001
findings_count: 44

First production creation pass returned:
alerts_created: 41
tasks_created: 41
verified_issues_created: 44
verified_issues_existing: 0

A repeat call against the same record correctly returned:
alerts_created: 0
tasks_created: 0
verified_issues_created: 0
verified_issues_existing: 44

Stored production record check:
success: True
record_id: test-mode-notification-suppression-prod-001
issues_count: 44
issues_with_candidates: 44
candidate_urls_total: 270
issues_with_verified_image: 0
baseline_locked: 0

## Safety Rules

Test mode must not:

- set verified_image_url
- mark image_match_status as verified
- baseline-lock findings
- final-approve findings
- delete S3 files
- hide records from review queue automatically

Test mode may:

- create verified issue rows
- create alert rows
- create automation task rows
- rewrite S3 image URLs
- clean decorative candidate images
- support dashboard QA

## n8n Usage

For n8n smoke tests or deployment tests, include:

processing_mode: test
skip_notifications: true

For real homeowner production intake, omit both fields or use:

processing_mode: production
skip_notifications: false

## Related Records

Local: test-mode-notification-suppression-local-001
Production: test-mode-notification-suppression-prod-001
Source: pdf-url-path-prod-smoke-001

## Related Commit

b0548a0 add process inspection test notification suppression

## Milestones Locked

HomeFax Test Mode / Notification Suppression Pass 1
HomeFax Test Mode / Notification Suppression Deployment Pass 1
