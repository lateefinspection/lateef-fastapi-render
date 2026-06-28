// HomeFax Intake Standard n8n Code Node Template v1
// Node: Build Validation Error Response
//
// Purpose:
// Build a clear failed webhook response when validation fails.
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

return [
  {
    json: {
      success: false,
      record_id: recordId,
      payload_valid: false,
      errors_count: validation.errors_count || 0,
      warnings_count: validation.warnings_count || 0,
      errors: validation.errors || [],
      warnings: validation.warnings || [],
      counts: validation.counts || {},
      next_step: "Fix payload mapping before sending this inspection into review."
    }
  }
];
