// HomeFax Intake Standard n8n Code Node Template v1
// Node: Normalize Intake Metadata
//
// Purpose:
// Normalize incoming Zite / Fillout / manual test payload fields
// into one predictable metadata object for the HomeFax intake workflow.
//
// n8n Code node mode:
// - Run Once for All Items

function cleanText(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function slugify(value) {
  return cleanText(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

const input = $input.first()?.json || {};

const sourceSystem = cleanText(
  input.source_system ||
  input.sourceSystem ||
  input.source ||
  "n8n"
);

const propertyAddress = cleanText(
  input.property?.address_full ||
  input.property_address ||
  input.propertyAddress ||
  input.address ||
  input.address_full
);

const homeownerEmail = cleanText(
  input.homeowner?.email ||
  input.homeowner_email ||
  input.email ||
  input.submittedBy
);

const homeownerName = cleanText(
  input.homeowner?.name ||
  input.homeowner_name ||
  input.clientName ||
  input.name
);

const fileUrl = cleanText(
  input.original_report?.file_url ||
  input.report_pdf_url ||
  input.reportPdfUrl ||
  input.pdf_url ||
  input.file_url ||
  input.url
);

const fileName = cleanText(
  input.original_report?.file_name ||
  input.file_name ||
  input.filename ||
  input.reportFileName ||
  "inspection-report.pdf"
);

const incomingRecordId = cleanText(
  input.record_id ||
  input.recordId ||
  input.inspection_id ||
  input.inspectionId
);

const fallbackRecordId = `pdf-${slugify(propertyAddress || fileName || "inspection")}-${Date.now()}`;

const recordId = incomingRecordId || fallbackRecordId;

const normalized = {
  record_id: recordId,
  tenant_id: cleanText(input.tenant_id || input.tenantId || "lateef-home-inspection"),
  source: {
    source_system: sourceSystem,
    source_workflow: "homefax-intake-standard-v1",
    source_record_id: cleanText(input.source_record_id || input.sourceRecordId || recordId),
    source_submission_id: cleanText(input.submission_id || input.submissionId || input.recordId || ""),
    received_at: new Date().toISOString()
  },
  property: {
    property_id: cleanText(input.property?.property_id || input.property_id || input.propertyId || ""),
    address_full: propertyAddress,
    street: cleanText(input.property?.street || input.street || ""),
    city: cleanText(input.property?.city || input.city || ""),
    state: cleanText(input.property?.state || input.state || ""),
    postal_code: cleanText(input.property?.postal_code || input.postal_code || input.zip || ""),
    country: cleanText(input.property?.country || input.country || "US")
  },
  homeowner: {
    homeowner_user_id: cleanText(input.homeowner?.homeowner_user_id || input.homeowner_user_id || ""),
    name: homeownerName,
    email: homeownerEmail,
    phone: cleanText(input.homeowner?.phone || input.phone || input.homeowner_phone || "")
  },
  inspection: {
    inspection_id: cleanText(input.inspection?.inspection_id || input.inspection_id || recordId),
    inspection_date: cleanText(input.inspection?.inspection_date || input.inspection_date || input.inspectionDate || ""),
    inspection_company: cleanText(input.inspection?.inspection_company || input.inspection_company || ""),
    inspector_name: cleanText(input.inspection?.inspector_name || input.inspector_name || "")
  },
  original_report: {
    file_name: fileName,
    file_url: fileUrl,
    stored_pdf_url: cleanText(input.original_report?.stored_pdf_url || input.stored_pdf_url || ""),
    storage_status: cleanText(input.original_report?.storage_status || input.storage_status || "pending")
  },
  raw_input: input
};

return [
  {
    json: normalized
  }
];
