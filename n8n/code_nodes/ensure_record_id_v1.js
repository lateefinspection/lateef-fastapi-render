// HomeFax Intake Standard n8n Code Node Template v1
// Node: Ensure Record ID
//
// Purpose:
// Guarantee one stable record_id exists and is preserved
// for every downstream FastAPI and dashboard call.
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

const item = $input.first()?.json || {};

let recordId = cleanText(item.record_id || item.recordId);

if (!recordId) {
  const address = cleanText(item.property?.address_full || item.property_address || "");
  const fileName = cleanText(item.original_report?.file_name || item.file_name || "inspection-report.pdf");
  recordId = `pdf-${slugify(address || fileName || "inspection")}-${Date.now()}`;
}

item.record_id = recordId;

if (!item.tenant_id) {
  item.tenant_id = "lateef-home-inspection";
}

item.dashboard_url = `https://homefax-dashboard.onrender.com/?record_id=${encodeURIComponent(recordId)}`;

return [
  {
    json: item
  }
];
