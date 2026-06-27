// HomeFax Intake Standard n8n Code Node Template v1
// Node: Extract Payload
//
// Purpose:
// Extract payload from FastAPI preview response before validation.
//
// Expected previous HTTP response shape:
// {
//   success: true,
//   payload: {...}
// }
//
// n8n Code node mode:
// - Run Once for All Items

const response = $input.first()?.json || {};

if (response.success !== true) {
  return [
    {
      json: {
        success: false,
        payload_ready: false,
        error_stage: "extract_payload",
        errors: [
          "FastAPI intake standard preview did not return success=true"
        ],
        raw_response: response
      }
    }
  ];
}

if (!response.payload || typeof response.payload !== "object" || Array.isArray(response.payload)) {
  return [
    {
      json: {
        success: false,
        payload_ready: false,
        error_stage: "extract_payload",
        errors: [
          "FastAPI intake standard preview response is missing payload object"
        ],
        raw_response: response
      }
    }
  ];
}

return [
  {
    json: response.payload
  }
];
