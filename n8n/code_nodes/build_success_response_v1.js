// HomeFax Intake Standard n8n Code Node Template v1
// Node: Build Success Response
//
// Purpose:
// Build the final successful webhook response after payload validation.
// This version preserves record_id from the original Webhook body.
//
// n8n Code node mode:
// - Run Once for All Items
//
// Required upstream node name:
// - Webhook

function cleanText(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

const validation = $input.first()?.json || {};

let originalPayload = {};

try {
  originalPayload = $("Webhook").first().json.body || {};
} catch (error) {
  originalPayload = {};
}

const recordId = cleanText(
  originalPayload.record_id ||
  originalPayload.recordId ||
  validation.record_id ||
  validation.payload?.record_id ||
  ""
);

const dashboardUrl = `https://homefax-dashboard.onrender.com/?record_id=${encodeURIComponent(recordId)}`;

return [
  {
    json: {
      success: true,
      record_id: recordId,
      dashboard_url: dashboardUrl,
      payload_valid: true,
      errors_count: validation.errors_count || 0,
      warnings_count: validation.warnings_count || 0,
      warnings: validation.warnings || [],
      counts: validation.counts || {},
      next_step: "Open dashboard_url to review verified issues."
    }
  }
];
