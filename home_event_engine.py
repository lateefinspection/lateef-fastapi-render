def calculate_event_trust(event, evidence_count):
    """
    Trust score answers:
    How believable is this repair/upgrade/update event?
    """

    score = 0

    performed_by = (event.performed_by or "unknown").lower()
    submitted_by = (event.submitted_by or "homeowner").lower()
    event_type = (event.event_type or "").lower()

    # Who performed the work?
    if performed_by in ["licensed_pro", "contractor", "professional"]:
        score += 40
    elif performed_by in ["homeowner", "owner"]:
        score += 25
    elif performed_by == "unknown":
        score += 5

    # Who submitted it?
    if submitted_by == "admin":
        score += 20
    elif submitted_by == "homeowner":
        score += 10

    # Evidence strength
    if evidence_count >= 3:
        score += 30
    elif evidence_count == 2:
        score += 20
    elif evidence_count == 1:
        score += 10

    # Event type strength
    if event_type in ["repair", "upgrade", "replacement"]:
        score += 10
    elif event_type == "maintenance":
        score += 5

    return max(0, min(score, 100))


def calculate_event_impact(event, trust_score):
    """
    Impact answers:
    How much should this event reduce or affect risk?
    """

    event_type = (event.event_type or "").lower()

    if event_type in ["repair", "replacement", "upgrade"]:
        if trust_score >= 80:
            return -45
        if trust_score >= 60:
            return -30
        if trust_score >= 40:
            return -15
        return -5

    if event_type == "maintenance":
        if trust_score >= 70:
            return -15
        if trust_score >= 40:
            return -8
        return -3

    if event_type == "inspection_update":
        return -5

    if event_type == "new_issue":
        return 20

    return 0


def determine_issue_status_after_event(event, trust_score):
    """
    Determines how an issue should change after an approved event.
    """

    event_type = (event.event_type or "").lower()

    if event_type in ["repair", "replacement", "upgrade"]:
        if trust_score >= 70:
            return "resolved"
        if trust_score >= 40:
            return "improved"
        return "verification_pending"

    if event_type == "maintenance":
        if trust_score >= 70:
            return "improved"
        return "verification_pending"

    if event_type == "new_issue":
        return "open"

    return "verification_pending"
