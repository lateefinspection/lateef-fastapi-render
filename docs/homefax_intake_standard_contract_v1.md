# HomeFax Intake Standard Contract v1

## Purpose

HomeFax Intake Standard Contract v1 defines the required structure every uploaded inspection must become before it enters the HomeFax platform.

HomeFax is not just a parser. The parser converts messy inspection PDFs into structured findings, but the product workflow is:

Homeowner upload
→ processing / extraction
→ verified issues
→ homeowner decisions
→ admin verification
→ final approval / baseline lock
→ monitoring, recurrence, repairs, and alerts

## Required top-level object

- homefax_intake_standard_version
- record_id
- tenant_id
- source
- property
- homeowner
- inspection
- original_report
- processing
- standard_findings
- audit

## Main rule

Every intake source must preserve:

- record id
- property address
- homeowner email
- original PDF URL
- source item number
- source finding text
- source page
- candidate image URLs
- suggested image URL
- image match status
- HomeFax category/system/component
- homeowner decision
- admin decision
- baseline lock fields
- monitoring fields

## Next pass

HomeFax Intake Standard Mapper Pass 1 will map current verified issue records into this contract shape.
