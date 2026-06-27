// HomeFax Intake Standard n8n Code Node Template v1
// Node: Build Success Response
//
// Purpose:
// Build the final successful webhook response after payload validation.
//
// n8n Code node mode:
// - Run Once for All Items

function cleanText(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

const item = $input.first()?.json || {};

const recordId = cleanText(
  item.record_id ||
  item.recordId ||
  item.payload?.record_id ||
  item.validation?.record_id
);

const dashboardUrl = cleanText(
  item.dashboard_url ||
  `https://homefax-dashboard.onrender.com/?record_id=${encodeURIComponent(recordId)}`
);

const response = {
  success: true,
  record_id: recordId,
  dashboard_url: dashboardUrl,
  payload_valid: true,
  errors_count: 0,
  warnings: item.warnings || [],
  next_step: "Open dashboard_url to review verified issues. Images may finalize shortly."
};

return [
  {
    json: response
  }
];
