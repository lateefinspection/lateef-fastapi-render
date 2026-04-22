import re


def _clean_text(value):
    if not value:
        return ""

    value = str(value)

    replacements = {
        "Õ": "i",
        "Ö": "f",
        "Þ": "ff",
        "\u00a0": " ",
    }

    for bad, good in replacements.items():
        value = value.replace(bad, good)

    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_issue_title(title):
    title = _clean_text(title)

    replacements = {
        "Rooing": "Roofing",
        "Defection": "Deflection",
        "Sofft": "Soffit",
        "amd": "and",
        "gaos": "gas",
    }

    for bad, good in replacements.items():
        title = title.replace(bad, good)

    return title.strip()


def normalize_summary(summary):
    return _clean_text(summary)


def normalize_system(raw_system):
    s = normalize_issue_title(raw_system).lower()

    if not s:
        return "General"

    if "gutter" in s or "downspout" in s or "drainage" in s:
        return "Gutters & Drainage"

    if "chimney" in s:
        return "Chimney"

    if "roof" in s or "shingle" in s or "flashing" in s:
        return "Roofing"

    if "attic" in s or "insulation" in s or "ventilation" in s:
        return "Insulation & Ventilation"

    if "hvac" in s or "heating" in s or "cooling" in s or "air filter" in s or "condensate" in s:
        return "Heating & Cooling"

    if "plumb" in s or "water heater" in s or "hose bib" in s or "distribution piping" in s:
        return "Plumbing"

    if "electrical" in s or "panel" in s or "gfci" in s or "afci" in s or "receptacle" in s or "outlet" in s:
        return "Electrical"

    if "bath" in s or "toilet" in s or "sink" in s or "tub" in s or "shower" in s:
        return "Bathrooms"

    if "kitchen" in s or "dishwasher" in s or "garbage disposal" in s or "range" in s or "cooktop" in s:
        return "Kitchen & Appliances"

    if "garage" in s or "carport" in s:
        return "Garage"

    if "crawlspace" in s or "foundation" in s or "vapor barrier" in s or "wdo" in s:
        return "Foundation & Crawlspace"

    if "interior" in s or "fireplace" in s or "doors" in s or "closets" in s:
        return "Interior"

    if "exterior" in s or "siding" in s or "trim" in s or "window" in s or "door" in s or "grading" in s or "vegetation" in s:
        return "Exterior"

    if "structure" in s or "framing" in s or "beam" in s:
        return "Structure"

    return "General"


def normalize_component(raw_component, issue_title="", normalized_system="General"):
    component = normalize_issue_title(raw_component)

    if component and component.lower() not in {"unknown", "general"}:
        return component

    t = normalize_issue_title(issue_title).lower()

    if normalized_system == "Roofing":
        if "nail" in t:
            return "Fasteners"
        if "shingle" in t:
            return "Shingles"
        if "penetration" in t:
            return "Roof Penetrations"
        if "moss" in t or "lichen" in t or "organic growth" in t:
            return "Roof Coverings"
        return "Roof Coverings"

    if normalized_system == "Gutters & Drainage":
        if "downspout" in t:
            return "Downspouts"
        if "gutter" in t or "debris" in t:
            return "Gutters"
        if "grading" in t or "pooling" in t:
            return "Drainage"
        return "Drainage"

    if normalized_system == "Chimney":
        return "Chimney Crown"

    if normalized_system == "Insulation & Ventilation":
        if "attic access" in t:
            return "Attic Access"
        if "insulation" in t:
            return "Insulation"
        if "mold" in t or "staining" in t:
            return "Sheathing / Moisture"
        return "Attic"

    if normalized_system == "Plumbing":
        if "water heater" in t or "scalding" in t:
            return "Water Heater"
        if "distribution" in t or "pipe" in t:
            return "Supply Piping"
        if "drain" in t or "vent" in t:
            return "Drain / Waste / Vent"
        if "hose bib" in t:
            return "Hose Bib"
        if "faucet" in t:
            return "Faucet"
        return "Plumbing"

    if normalized_system == "Electrical":
        if "panel" in t or "breaker" in t or "double tap" in t:
            return "Panels & Breakers"
        if "gfci" in t or "afci" in t:
            return "GFCI / AFCI"
        if "outlet" in t or "receptacle" in t or "cover" in t:
            return "Receptacles & Switches"
        if "smoke" in t or "co detector" in t:
            return "Life Safety Devices"
        return "Electrical"

    if normalized_system == "Heating & Cooling":
        if "cooling" in t or "ac" in t:
            return "Cooling System"
        if "heating" in t or "heat" in t:
            return "Heating System"
        if "air filter" in t:
            return "Air Filter"
        if "duct" in t or "distribution" in t:
            return "Ductwork"
        if "condensate" in t:
            return "Condensate Drainage"
        return "HVAC"

    if normalized_system == "Bathrooms":
        if "toilet" in t:
            return "Toilets"
        if "sink" in t or "faucet" in t:
            return "Sinks & Faucets"
        if "tub" in t or "shower" in t or "grout" in t:
            return "Tubs & Showers"
        if "vent" in t:
            return "Exhaust Ventilation"
        return "Bathrooms"

    if normalized_system == "Kitchen & Appliances":
        if "dishwasher" in t:
            return "Dishwasher"
        if "garbage disposal" in t:
            return "Garbage Disposal"
        if "range" in t or "cooktop" in t or "anti-tip" in t:
            return "Range / Cooktop"
        if "dryer vent" in t:
            return "Dryer Vent"
        if "countertop" in t or "cabinet" in t:
            return "Countertops & Cabinets"
        return "Kitchen & Appliances"

    if normalized_system == "Garage":
        if "sensor" in t or "garage door" in t:
            return "Garage Door Safety"
        if "floor" in t:
            return "Garage Floor"
        if "wall" in t or "ceiling" in t:
            return "Walls & Ceiling"
        return "Garage"

    if normalized_system == "Foundation & Crawlspace":
        if "vapor barrier" in t:
            return "Vapor Barrier"
        if "wdo" in t or "termite" in t or "mold" in t or "microbial" in t:
            return "WDO / Moisture"
        if "foundation wall" in t or "skirt wall" in t:
            return "Foundation Wall"
        return "Crawlspace"

    if normalized_system == "Exterior":
        if "trim" in t or "fascia" in t or "soffit" in t:
            return "Trim / Fascia / Soffit"
        if "window" in t:
            return "Windows"
        if "door" in t:
            return "Exterior Doors"
        if "vegetation" in t:
            return "Vegetation"
        if "grading" in t:
            return "Grading"
        if "driveway" in t or "walkway" in t:
            return "Driveways & Walkways"
        if "stair" in t or "rail" in t or "handrail" in t:
            return "Stairs & Railings"
        return "Exterior"

    if normalized_system == "Interior":
        if "door" in t or "closet" in t:
            return "Doors & Closets"
        if "fireplace" in t or "flue" in t:
            return "Fireplace"
        if "stair" in t or "handrail" in t:
            return "Stairs & Railings"
        if "floor" in t:
            return "Floors"
        if "wall" in t or "ceiling" in t:
            return "Walls & Ceilings"
        return "Interior"

    if normalized_system == "Structure":
        if "framing" in t or "beam" in t:
            return "Framing / Beams"
        if "insulation" in t:
            return "Insulation"
        return "Structure"

    return "Unknown"


def normalize_severity(raw_severity, issue_title=""):
    raw = _clean_text(raw_severity).lower()
    title = normalize_issue_title(issue_title).lower()

    if raw in {"high", "critical", "major"}:
        return "high"
    if raw in {"low", "minor"}:
        return "low"
    if raw == "medium":
        return "medium"

    # infer from title if AI returns vague or empty severity
    high_keywords = [
        "safety concern",
        "unsafe",
        "hazard",
        "double taps",
        "scalding",
        "wdo",
        "mold",
        "microbial",
        "no handrail",
        "exposed wiring",
        "missing cover",
        "anti-tip",
    ]
    medium_keywords = [
        "damage",
        "damaged",
        "crack",
        "leak",
        "corrosion",
        "missing",
        "disconnected",
        "settling",
        "staining",
        "repair",
        "replace",
    ]
    low_keywords = [
        "maintenance",
        "improve",
        "upgrade",
        "clean",
        "monitor",
    ]

    if any(k in title for k in high_keywords):
        return "high"
    if any(k in title for k in medium_keywords):
        return "medium"
    if any(k in title for k in low_keywords):
        return "low"

    return "medium"


def map_priority(severity):
    if severity == "high":
        return "high"
    if severity == "low":
        return "low"
    return "medium"


def default_next_action(system, issue_title):
    system = (system or "").lower()
    title = normalize_issue_title(issue_title).lower()

    if system in {"roofing", "gutters & drainage"}:
        return "Contact a qualified roofing contractor."
    if system == "chimney":
        return "Contact a qualified chimney contractor."
    if system in {"plumbing", "bathrooms"}:
        return "Contact a qualified plumbing contractor."
    if system == "electrical":
        return "Contact a qualified electrician."
    if system == "heating & cooling":
        return "Contact a qualified HVAC professional."
    if system in {"foundation & crawlspace", "structure"}:
        return "Contact a qualified contractor for further evaluation and repair."
    if "mold" in title or "microbial" in title or "wdo" in title:
        return "Contact a qualified pest/moisture or remediation specialist."
    return "Review and repair as needed."


def default_why_it_matters(system, issue_title, severity):
    system = (system or "").lower()
    title = normalize_issue_title(issue_title).lower()

    if severity == "high":
        return "May create a safety hazard or lead to significant damage if not addressed."

    if system in {"roofing", "gutters & drainage", "chimney"}:
        return "May allow moisture intrusion, shorten material life, or lead to hidden structural damage."

    if system in {"plumbing", "bathrooms"}:
        return "May cause leaks, water damage, mold growth, or reduced fixture performance."

    if system == "electrical":
        return "May increase electrical safety risk, shock hazard, or fire risk."

    if system == "heating & cooling":
        return "May reduce comfort, efficiency, or equipment life and can lead to more expensive repairs."

    if system in {"foundation & crawlspace", "structure"}:
        return "May indicate moisture, pest, or structural concerns that can worsen over time."

    if "mold" in title or "microbial" in title:
        return "May indicate a moisture problem and can affect materials and indoor air quality."

    return "May lead to damage, safety issues, or reduced performance if not addressed."
