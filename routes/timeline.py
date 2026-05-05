from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models import Issue, Event

router = APIRouter(
    prefix="/inspections",
    tags=["Timeline"]
)


@router.get("/{record_id}/timeline")
def get_timeline(record_id: str, db: Session = Depends(get_db)):
    """
    Returns a human-readable timeline of the home:
    - Baseline inspection
    - Events (repairs, updates)
    """

    issues = db.query(Issue).filter(Issue.record_id == record_id).all()
    events = db.query(Event).filter(Event.record_id == record_id).all()

    timeline = []

    # -------------------------
    # BASELINE ENTRY
    # -------------------------
    if issues:
        earliest = min(i.created_at for i in issues)
        max_risk = max(i.risk_score or 0 for i in issues)

        timeline.append({
            "type": "baseline",
            "timestamp": earliest,
            "title": "Inspection Completed",
            "description": f"{len(issues)} issues identified",
            "risk_score": max_risk
        })

    # -------------------------
    # EVENT ENTRIES
    # -------------------------
    for e in events:
        timeline.append({
            "type": "event",
            "timestamp": e.created_at,
            "title": f"{(e.component or '').replace('_', ' ').title()} {e.event_type}",
            "description": e.description,
            "verification_status": e.verification_status,
            "impact": e.impact_on_risk,
            "evidence_count": len(e.evidence) if e.evidence else 0
        })

    # -------------------------
    # SORT TIMELINE
    # -------------------------
    timeline.sort(key=lambda x: x["timestamp"])

    return timeline
