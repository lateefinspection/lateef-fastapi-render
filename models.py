from sqlalchemy import Column, Integer, String, DateTime, Text
from database import Base
import datetime

from pydantic import BaseModel
from typing import List, Optional, Literal


# =========================
# DATABASE MODELS (SQLAlchemy)
# =========================

class Inspection(Base):
    __tablename__ = "inspections"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(String, unique=True, index=True)

    homeowner_email = Column(String, default="test@homefax.ai")
    property_address = Column(String, default="123 Test St")

    file_name = Column(String, default="")
    pages = Column(Integer, default=0)

    status = Column(String, default="processed")
    pipeline_stage = Column(String, default="ai_analyzed")
    issue_count = Column(Integer, default=0)

    inspection_type = Column(String, default="unknown")

    baseline_status = Column(String, default="unlocked")
    baseline_locked_at = Column(DateTime, nullable=True)
    baseline_note = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow
    )


class VerifiedIssue(Base):
    __tablename__ = "verified_issues"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(String, index=True)

    section = Column(String, default="")
    title = Column(String, default="")
    summary = Column(Text, default="")
    image_url = Column(String, default="")

    severity = Column(String, default="unknown")
    status = Column(String, default="new")

    homeowner_decision = Column(String, default="unreviewed")
    homeowner_note = Column(Text, default="")

    admin_review_status = Column(String, default="pending")
    admin_note = Column(Text, default="")

    baseline_locked = Column(String, default="no")
    baseline_locked_at = Column(DateTime, nullable=True)

    current_status = Column(String, default="open")
    resolved_by_event_id = Column(Integer, nullable=True)

    risk_score = Column(Integer, default=0)
    risk_level = Column(String, default="LOW")
    priority = Column(String, default="monitor")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow
    )


class HomeEvent(Base):
    __tablename__ = "home_events"

    id = Column(Integer, primary_key=True, index=True)

    record_id = Column(String, index=True)
    issue_id = Column(Integer, nullable=True)

    event_type = Column(String, default="repair")
    system = Column(String, default="")
    component = Column(String, default="")
    description = Column(Text, default="")

    submitted_by = Column(String, default="homeowner")
    performed_by = Column(String, default="unknown")

    verification_status = Column(String, default="pending")
    verifier_note = Column(Text, default="")

    evidence_count = Column(Integer, default=0)
    trust_score = Column(Integer, default=0)
    impact_on_risk = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    verified_at = Column(DateTime, nullable=True)


class EventEvidence(Base):
    __tablename__ = "event_evidence"

    id = Column(Integer, primary_key=True, index=True)

    event_id = Column(Integer, index=True)
    record_id = Column(String, index=True)
    issue_id = Column(Integer, nullable=True)

    evidence_type = Column(String, default="photo")
    image_url = Column(Text, default="")
    description = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# =========================
# API REQUEST MODELS (Pydantic)
# =========================

class Finding(BaseModel):
    type: Optional[str] = "unknown"
    severity: Optional[Literal["low", "medium", "high", "critical"]] = "low"
    location: Optional[str] = "unknown"
    notes: Optional[str] = ""


class InspectionProcessRequest(BaseModel):
    record_id: str
    findings: List[Finding]
