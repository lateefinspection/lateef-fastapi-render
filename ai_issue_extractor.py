from openai import OpenAI
import os
import json
import re
from dotenv import load_dotenv


# =========================
# ENV / CLIENT
# =========================

# Important:
# override=True makes the .env key replace any old OPENAI_API_KEY
# already exported in your terminal session.
load_dotenv(dotenv_path=".env", override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# JSON HELPERS
# =========================

def _safe_json_loads(content: str):
    """
    Safely parse model JSON.

    Handles:
    - clean JSON
    - ```json fenced JSON
    - extra text around JSON
    """
    if not content:
        return None

    text = str(content).strip()

    # Remove markdown fences if present.
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except Exception:
        pass

    # Try array first because our preferred output is an array.
    array_match = re.search(r"(\[.*\])", text, flags=re.DOTALL)
    if array_match:
        try:
            return json.loads(array_match.group(1))
        except Exception:
            pass

    # Then try object.
    object_match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
    if object_match:
        try:
            return json.loads(object_match.group(1))
        except Exception:
            pass

    return None


def _normalize_severity(value: str) -> str:
    severity = str(value or "medium").strip().lower()

    if severity in ["low", "medium", "high", "critical"]:
        return severity

    if any(word in severity for word in ["urgent", "unsafe", "hazard", "danger", "fire", "shock"]):
        return "high"

    return "medium"


def _guess_system(text: str) -> str:
    value = str(text or "").lower()

    if any(word in value for word in ["roof", "shingle", "flashing", "gutter", "downspout"]):
        return "Roof"

    if any(word in value for word in ["plumbing", "pipe", "valve", "water", "drain", "leak", "faucet"]):
        return "Plumbing"

    if any(word in value for word in ["electrical", "breaker", "panel", "gfci", "wiring", "receptacle", "outlet"]):
        return "Electrical"

    if any(word in value for word in ["hvac", "furnace", "cooling", "heating", "air conditioner", "duct"]):
        return "HVAC"

    if any(word in value for word in ["foundation", "basement", "crawlspace", "structure", "joist", "beam"]):
        return "Foundation"

    if any(word in value for word in ["exterior", "siding", "wall-covering", "wall covering", "trim", "door", "window"]):
        return "Exterior"

    if any(word in value for word in ["interior", "ceiling", "floor", "wall", "stair", "handrail"]):
        return "Interior"

    return "General"


def _extract_source_number(text: str):
    match = re.search(r"\b(\d+(?:\.\d+){1,3})\b", str(text or ""))

    if match:
        return match.group(1)

    return None


def _extract_page_number(text: str):
    """
    Looks for markers created by build_issue_records.py:
      === PAGE 4 ===
    """
    match = re.search(r"===\s*PAGE\s+(\d+)\s*===", str(text or ""), flags=re.IGNORECASE)

    if match:
        try:
            return int(match.group(1))
        except Exception:
            return None

    return None


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _issue_title_from_text(text: str) -> str:
    cleaned = _clean_text(text)

    # Pattern:
    # 8.1.1 Plumbing - Main Water Shut-Off Valve: Active Water Leak at Valve
    match = re.match(
        r"^\s*\d+(?:\.\d+){1,3}\s+.+?:\s*(.+?)\s*$",
        cleaned,
    )

    if match:
        return match.group(1).strip()[:180]

    if len(cleaned) > 180:
        return cleaned[:180].strip()

    return cleaned or "Inspection issue"


# =========================
# LOCAL FALLBACK EXTRACTOR
# =========================

def _fallback_numbered_issue_extract(full_text: str):
    """
    Deterministic fallback used when OpenAI is unavailable or returns invalid output.

    Returns the list shape expected by build_issue_records.py:
      issue_title
      system
      component
      severity
      summary
      page_number
      issue_code
    """
    issues = []
    current_page = None
    seen = set()

    for raw_line in str(full_text or "").splitlines():
        line = _clean_text(raw_line)

        if not line:
            continue

        page_match = re.match(r"===\s*PAGE\s+(\d+)\s*===", line, flags=re.IGNORECASE)
        if page_match:
            try:
                current_page = int(page_match.group(1))
            except Exception:
                current_page = None
            continue

        # Example:
        # 8.1.1 Plumbing - Main Water Shut-Off Valve: Active Water Leak at Valve
        match = re.match(
            r"^(?P<number>\d+(?:\.\d+){1,3})\s+"
            r"(?P<section>[A-Za-z][A-Za-z &/+-]{2,120})"
            r"(?:\s+-\s+(?P<component>[^:]{2,180}))?"
            r"\s*:\s*(?P<title>.+?)\s*$",
            line,
        )

        if not match:
            continue

        number = match.group("number").strip()
        section = match.group("section").strip()
        component = (match.group("component") or section).strip()
        title = match.group("title").strip()

        if not title or len(title) < 3:
            continue

        lower_line = line.lower()

        # Skip obvious non-defect/noise language.
        if any(
            noise in lower_line
            for noise in [
                "table of contents",
                "inspection details",
                "prepared for",
                "prepared by",
                "standards of practice",
                "internachi",
                "satisfactory",
                "inspected",
                "not inspected",
                "not present",
            ]
        ):
            continue

        key = f"{number}:{section}:{component}:{title}".lower()

        if key in seen:
            continue

        seen.add(key)

        combined = f"{section} {component} {title}".lower()

        severity = "medium"

        if any(
            word in combined
            for word in [
                "active leak",
                "leak",
                "unsafe",
                "hazard",
                "fire",
                "shock",
                "missing gfci",
                "open breaker",
                "double tap",
                "water intrusion",
            ]
        ):
            severity = "high"

        issues.append(
            {
                "issue_title": title,
                "system": section,
                "component": component,
                "severity": severity,
                "summary": line,
                "page_number": current_page,
                "issue_code": number,
            }
        )

    return issues


# =========================
# OUTPUT NORMALIZER
# =========================

def _normalize_ai_output_to_issue_list(ai_data, full_text: str):
    """
    Converts either:
    - list of issue objects
    - {"issues": [...]}
    - old category dict shape: roof/plumbing/electrical...
    into the list expected by build_issue_records.py.
    """
    if isinstance(ai_data, list):
        normalized = []

        for item in ai_data:
            if not isinstance(item, dict):
                continue

            title = (
                item.get("issue_title")
                or item.get("issueTitle")
                or item.get("title")
                or item.get("issue")
                or item.get("summary")
                or ""
            )

            if not title:
                continue

            if str(title).lower().strip() == "no issues found":
                continue

            summary = item.get("summary") or item.get("description") or item.get("issue") or title
            system = item.get("system") or _guess_system(f"{title} {summary}")
            component = item.get("component") or system
            severity = _normalize_severity(item.get("severity"))

            normalized.append(
                {
                    "issue_title": _clean_text(title),
                    "system": _clean_text(system),
                    "component": _clean_text(component),
                    "severity": severity,
                    "summary": _clean_text(summary),
                    "page_number": item.get("page_number") or item.get("page") or _extract_page_number(str(summary)),
                    "issue_code": item.get("issue_code") or item.get("source_number") or _extract_source_number(str(summary)),
                }
            )

        return normalized

    if isinstance(ai_data, dict):
        # Preferred wrapped shape.
        if isinstance(ai_data.get("issues"), list):
            return _normalize_ai_output_to_issue_list(ai_data.get("issues"), full_text)

        normalized = []

        # Old category shape:
        # {
        #   "roof": {"issue": "...", "severity": "..."},
        #   "plumbing": {"issue": "...", "severity": "..."}
        # }
        for system_key in [
            "roof",
            "plumbing",
            "electrical",
            "hvac",
            "foundation",
            "exterior",
            "interior",
            "safety",
        ]:
            item = ai_data.get(system_key)

            if not isinstance(item, dict):
                continue

            issue_text = _clean_text(item.get("issue") or "")

            if not issue_text or issue_text.lower() == "no issues found":
                continue

            normalized.append(
                {
                    "issue_title": _issue_title_from_text(issue_text),
                    "system": system_key.title(),
                    "component": system_key.title(),
                    "severity": _normalize_severity(item.get("severity")),
                    "summary": issue_text,
                    "page_number": _extract_page_number(issue_text),
                    "issue_code": _extract_source_number(issue_text),
                }
            )

        return normalized

    return []


# =========================
# DEBUG HELPER
# =========================

def _print_key_debug():
    """
    Prints safe key diagnostics without exposing the full secret.
    """
    key = os.getenv("OPENAI_API_KEY", "").strip()

    print("OPENAI_API_KEY loaded:", bool(key))
    print("OPENAI_API_KEY starts with:", key[:10] if key else None)
    print("OPENAI_API_KEY ends with:", key[-4:] if key else None)
    print("OPENAI_API_KEY length:", len(key))


# =========================
# CURRENT FUNCTION
# =========================

def extract_issues_from_text(text: str):
    """
    Current AI extractor.

    Returns a list of issue dictionaries compatible with build_issue_records.py.

    If OpenAI fails, falls back to deterministic numbered-line parsing.
    """
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY missing. Using fallback numbered issue extraction.")
        return _fallback_numbered_issue_extract(text)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": f"""
You are a professional home inspector AI.

Analyze the following home inspection report text and extract REAL inspection issues.

Return ONLY valid JSON as an array.

Required output shape:

[
  {{
    "issue_title": "Active Water Leak at Valve",
    "system": "Plumbing",
    "component": "Main Water Shut-Off Valve",
    "severity": "low|medium|high|critical",
    "summary": "Short explanation of the issue.",
    "page_number": 8,
    "issue_code": "8.1.1"
  }}
]

Rules:
- Extract only real defects, safety hazards, material concerns, missing components, active leaks, electrical hazards, structural concerns, or items needing repair.
- Do not include cover page, table of contents, report disclaimers, weather, client info, inspector info, standards of practice, or generic inspection language.
- If no issue exists, return [].
- Do not hallucinate.
- Prefer exact report item numbers like 8.1.1 when present.
- Prefer page markers like === PAGE 8 === when present.
- Keep each issue separate.
- Do not combine multiple numbered findings into one issue.
- Use "high" for active leaks, unsafe electrical conditions, fire/shock hazards, missing GFCI protection, or urgent water damage.
- Use "medium" for repair defects that are not immediately unsafe.
- Use "low" for minor maintenance issues.

TEXT:
{text[:12000]}
"""
                }
            ],
            temperature=0.2,
        )

        content = response.choices[0].message.content
        parsed = _safe_json_loads(content)

        if parsed is None:
            print("JSON parse failed. Using fallback numbered issue extraction.")
            return _fallback_numbered_issue_extract(text)

        normalized = _normalize_ai_output_to_issue_list(parsed, text)

        if normalized:
            return normalized

        print("AI returned no normalized issues. Using fallback numbered issue extraction.")
        return _fallback_numbered_issue_extract(text)

    except Exception as e:
        print("OpenAI ERROR:", str(e))
        print("Using fallback numbered issue extraction.")
        _print_key_debug()
        return _fallback_numbered_issue_extract(text)


# =========================
# COMPATIBILITY WRAPPER
# =========================

def extract_issues_with_ai(full_text):
    """
    Backward-compatible wrapper expected by build_issue_records.py.

    build_issue_records.py imports:
        from ai_issue_extractor import extract_issues_with_ai

    Current extractor:
        extract_issues_from_text(text)
    """
    return extract_issues_from_text(full_text)
