def calculate_risk(issue):
    """
    Calculates risk score, risk level, and priority for a VerifiedIssue.
    This now considers current_status and resolved_by_event_id so home events can reduce risk.
    """

    score = 0

    section_weights = {
        "roof": 25,
        "foundation": 30,
        "electrical": 35,
        "plumbing": 20,
        "hvac": 15,
        "water_heater": 20,
    }

    severity_weights = {
        "unknown": 0,
        "low": 5,
        "medium": 15,
        "high": 30,
        "critical": 40,
    }

    section = (issue.section or "").lower()
    severity = (issue.severity or "unknown").lower()
    decision = issue.homeowner_decision or ""
    summary = (issue.summary or "").lower()

    score += section_weights.get(section, 10)
    score += severity_weights.get(severity, 0)

    if decision == "needs_repair":
        score += 20
    elif decision == "monitor":
        score += 5
    elif decision == "dispute":
        score += 10
    elif decision == "fixed":
        score -= 15
    elif decision == "ignore":
        score -= 10

    high_risk_keywords = [
        "unsafe",
        "hazard",
        "fire",
        "shock",
        "active leak",
        "structural",
        "foundation crack",
        "mold",
        "carbon monoxide",
        "missing panel cover",
        "unapproved breaker",
    ]

    medium_risk_keywords = [
        "leak",
        "water",
        "missing",
        "improper",
        "not level",
        "unapproved",
        "incomplete",
        "damaged",
        "corrosion",
        "downspout",
        "flashing",
    ]

    for keyword in high_risk_keywords:
        if keyword in summary:
            score += 25

    for keyword in medium_risk_keywords:
        if keyword in summary:
            score += 10

    # Home event impact
    if issue.current_status == "resolved":
        score -= 45
    elif issue.current_status == "improved":
        score -= 25
    elif issue.current_status == "verification_pending":
        score -= 5

    if issue.resolved_by_event_id:
        score -= 10

    score = max(0, min(score, 100))

    if score >= 80:
        risk_level = "CRITICAL"
        priority = "urgent"
    elif score >= 65:
        risk_level = "HIGH"
        priority = "urgent"
    elif score >= 35:
        risk_level = "MEDIUM"
        priority = "plan_repair"
    else:
        risk_level = "LOW"
        priority = "monitor"

    return {
        "risk_score": score,
        "risk_level": risk_level,
        "priority": priority,
    }
