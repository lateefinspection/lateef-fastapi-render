// HomeFax Intake Standard n8n Code Node Template v1
// Node: Build Validation Error Response
//
// Purpose:
// Build a clear failed webhook response when validation fails.
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
  ""
);

const response = {
  success: false,
  record_id: recordId,
  payload_valid: false,
  errors_count: item.errors_count || 0,
  warnings_count: item.warnings_count || 0,
  errors: item.errors || [],
  warnings: item.warnings || [],
  next_step: "Fix payload mapping before sending this inspection into review."
};

return [
  {
    json: response
  }
];
