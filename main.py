from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List
import pymysql
import os
import secrets
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import jwt

from twilio.rest import Client
import smtplib
from email.mime.text import MIMEText

load_dotenv()

app = FastAPI(title="HomeFax AI SaaS Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

# =========================
# CONFIG
# =========================

JWT_SECRET = os.getenv("JWT_SECRET", "dev-change-this-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 60 * 24 * 7

# Database config
# IMPORTANT:
# On Render, these values must come from Render Environment Variables.
# Locally, defaults let Raspberry Pi continue using local MariaDB.
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "homefax_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "StrongPassword123!")
DB_NAME = os.getenv("DB_NAME", "homefax")

# Twilio config
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID") or os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN") or os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE") or os.getenv("TWILIO_PHONE_NUMBER")

FALLBACK_USER_PHONE = os.getenv("USER_PHONE") or os.getenv("USER_PHONE_NUMBER")

# Email config
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS") or os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") or os.getenv("EMAIL_PASS")
FALLBACK_EMAIL_TO = os.getenv("EMAIL_TO") or EMAIL_ADDRESS


# =========================
# DATABASE
# =========================

def get_db_connection():
    """
    Creates a MySQL connection.

    Local development:
      DB_HOST defaults to 127.0.0.1

    Render production:
      Set DB_HOST to your AWS RDS endpoint:
      homefax-db.cypms2wauq5r.us-east-1.rds.amazonaws.com
    """
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# =========================
# AUTH HELPERS
# =========================

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        120000
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest = stored_hash.split("$", 1)
        check = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt.encode(),
            120000
        ).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False


def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)

    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": expire,
    }

    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM]
        )
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM saas_users WHERE id=%s", (user_id,))
        user = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if not user or not user.get("is_active", 1):
        raise HTTPException(status_code=401, detail="User inactive")

    return user


# =========================
# NOTIFICATIONS
# =========================

def send_sms(to_phone, message):
    try:
        if not to_phone:
            print("SMS skipped: no phone number")
            return False

        if not TWILIO_SID or not TWILIO_AUTH or not TWILIO_PHONE:
            print("SMS skipped: missing Twilio config")
            return False

        client = Client(TWILIO_SID, TWILIO_AUTH)

        client.messages.create(
            body=message,
            from_=TWILIO_PHONE,
            to=to_phone
        )

        return True

    except Exception as e:
        print("SMS ERROR:", e)
        return False


def send_email(to_email, subject, body):
    try:
        if not to_email:
            print("EMAIL skipped: no email destination")
            return False

        if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            print("EMAIL skipped: missing email config")
            return False

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)

        return True

    except Exception as e:
        print("EMAIL ERROR:", e)
        return False


def notify_record_owner(record_id, subject, message):
    """
    Temporary notification helper.

    Later, this should look up the real homeowner/admin notification settings
    by record_id. For now it uses fallback environment variables.
    """
    send_sms(FALLBACK_USER_PHONE, message)
    send_email(FALLBACK_EMAIL_TO, subject, message)


# =========================
# REQUEST MODELS
# =========================

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class DeviceCreate(BaseModel):
    device_name: str
    capabilities: List[str]


class ReadingCreate(BaseModel):
    device_id: int
    capability_key: str
    reading_type: str
    numeric_value: Optional[float] = None


class Finding(BaseModel):
    type: Optional[str] = "unknown"
    severity: Optional[str] = "low"
    location: Optional[str] = "unknown"
    notes: Optional[str] = ""


class InspectionProcessRequest(BaseModel):
    record_id: str
    findings: List[Finding]


# =========================
# ROOT / HEALTH
# =========================

@app.get("/")
def root():
    return {
        "status": "running",
        "service": "HomeFax AI SaaS Backend",
        "db_host": DB_HOST,
        "db_name": DB_NAME,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "HomeFax AI SaaS Backend"
    }


@app.get("/db-health")
def db_health():
    """
    Quick database connectivity check.
    Use this after setting Render environment variables.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT 1 AS ok")
        result = cursor.fetchone()

        cursor.close()
        conn.close()

        return {
            "status": "ok",
            "db_connected": True,
            "db_host": DB_HOST,
            "db_name": DB_NAME,
            "result": result,
        }

    except Exception as e:
        print("DB HEALTH ERROR:", e)
        raise HTTPException(
            status_code=500,
            detail=f"Database connection failed: {str(e)}"
        )


# =========================
# AUTH
# =========================

@app.post("/auth/register")
def register(payload: RegisterRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT id FROM saas_users WHERE email=%s",
            (payload.email,)
        )
        existing = cursor.fetchone()

        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        password_hash = hash_password(payload.password)

        cursor.execute(
            """
            INSERT INTO saas_users (email, password_hash)
            VALUES (%s, %s)
            """,
            (payload.email, password_hash)
        )

        conn.commit()
        user_id = cursor.lastrowid

        token = create_access_token(user_id, payload.email)

        return {
            "status": "registered",
            "user_id": user_id,
            "token": token,
            "token_type": "bearer",
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("REGISTER ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


@app.post("/auth/login")
def login(payload: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT * FROM saas_users WHERE email=%s",
            (payload.email,)
        )
        user = cursor.fetchone()

        if not user or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid login")

        token = create_access_token(user["id"], user["email"])

        return {
            "token": token,
            "token_type": "bearer",
        }

    except HTTPException:
        raise

    except Exception as e:
        print("LOGIN ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# =========================
# AUTOMATION ENGINE
# =========================

@app.post("/inspections/{record_id}/automation/run")
def run_automation(record_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    alerts = 0
    tasks = 0

    try:
        cursor.execute(
            "SELECT * FROM device_readings WHERE record_id=%s",
            (record_id,)
        )
        readings = cursor.fetchall()

        for r in readings:
            numeric_value = float(r.get("numeric_value") or 0)
            capability_key = r.get("capability_key")
            device_id = r.get("device_id") or 0

            if capability_key == "weather_monitoring" and numeric_value >= 0.2:
                alert_key = f"{record_id}:{device_id}:rain_detected"

                cursor.execute(
                    """
                    INSERT IGNORE INTO alerts
                    (record_id, device_id, capability_key, alert_type, severity, message, dedupe_key, status)
                    VALUES (%s, %s, %s, 'rain_detected', 'high', %s, %s, 'active')
                    """,
                    (
                        record_id,
                        device_id,
                        capability_key,
                        "Rain detected — potential roof vulnerability",
                        alert_key,
                    )
                )

                if cursor.rowcount > 0:
                    alerts += 1
                    notify_record_owner(
                        record_id,
                        "HomeFax Rain Alert",
                        "Rain detected — potential roof vulnerability"
                    )

                task_key = f"{record_id}:{device_id}:rain_task"

                cursor.execute(
                    """
                    INSERT IGNORE INTO automation_tasks
                    (record_id, task_type, priority, title, description, recommended_trade, status, source, dedupe_key)
                    VALUES (%s, 'inspection', 'medium', %s, %s, %s, 'open', 'automation_engine', %s)
                    """,
                    (
                        record_id,
                        "Rain inspection",
                        "Inspect property due to rain conditions",
                        "roofer",
                        task_key,
                    )
                )

                if cursor.rowcount > 0:
                    tasks += 1

        conn.commit()

        return {
            "success": True,
            "record_id": record_id,
            "alerts": alerts,
            "tasks": tasks,
        }

    except Exception as e:
        conn.rollback()
        print("AUTOMATION ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# =========================
# AI INGESTION — JSON FROM N8N
# =========================

@app.post("/process-inspection")
def process_inspection(data: InspectionProcessRequest):
    """
    Receives normalized findings from n8n.

    Expected JSON:
    {
      "record_id": "test-2",
      "findings": [
        {
          "type": "water_leak",
          "severity": "high",
          "location": "basement",
          "notes": "Active leak"
        }
      ]
    }
    """

    conn = get_db_connection()
    cursor = conn.cursor()

    record_id = data.record_id
    findings = data.findings or []

    alerts_created = 0
    tasks_created = 0
    processed_findings = []

    try:
        if not record_id:
            raise HTTPException(status_code=400, detail="Missing record_id")

        for f in findings:
            finding_type = (f.type or "unknown").lower().strip()
            severity = (f.severity or "low").lower().strip()
            location = (f.location or "unknown").lower().strip()
            notes = f.notes or ""

            if severity not in ["low", "medium", "high", "critical"]:
                severity = "low"

            alert_key = f"{record_id}:{finding_type}:{location}".lower()

            # Insert alert.
            # device_id is set to 0 because this alert comes from AI ingestion,
            # not a physical sensor device.
            cursor.execute(
                """
                INSERT IGNORE INTO alerts
                (record_id, device_id, capability_key, alert_type, severity, message, dedupe_key, status)
                VALUES (%s, 0, %s, %s, %s, %s, %s, 'active')
                """,
                (
                    record_id,
                    "ai_ingestion",
                    finding_type,
                    severity,
                    notes,
                    alert_key,
                )
            )

            if cursor.rowcount > 0:
                alerts_created += 1

                if severity in ["high", "critical"]:
                    notify_record_owner(
                        record_id,
                        f"HomeFax Alert: {finding_type}",
                        f"{finding_type} detected at {location}. {notes}"
                    )

            task_key = f"{alert_key}:task"

            cursor.execute(
                """
                INSERT IGNORE INTO automation_tasks
                (record_id, task_type, priority, title, description, recommended_trade, status, source, dedupe_key)
                VALUES (%s, %s, %s, %s, %s, %s, 'open', 'ai_ingestion', %s)
                """,
                (
                    record_id,
                    finding_type,
                    severity,
                    f"{finding_type} issue",
                    notes,
                    "general_home_service",
                    task_key,
                )
            )

            if cursor.rowcount > 0:
                tasks_created += 1

            processed_findings.append({
                "type": finding_type,
                "severity": severity,
                "location": location,
                "notes": notes,
                "alert_key": alert_key,
                "task_key": task_key,
            })

        conn.commit()

        return {
            "success": True,
            "record_id": record_id,
            "findings_count": len(findings),
            "alerts_created": alerts_created,
            "tasks_created": tasks_created,
            "processed_findings": processed_findings,
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception as e:
        conn.rollback()
        print("ERROR IN /process-inspection:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()


# =========================
# PDF UPLOAD ENDPOINT — ADAPTER PATH
# =========================

@app.post("/analyze-report/")
async def analyze_report(file: UploadFile = File(...)):
    """
    Keeps the existing file-upload parser route alive.

    Later, your adapter pipeline can plug in here:
    PDF → classify_report → adapter → extract_findings → normalize → image_matcher
    """

    try:
        content = await file.read()

        return {
            "success": True,
            "filename": file.filename,
            "message": "File received by HomeFax parser endpoint",
            "size_bytes": len(content),
            "findings": [],
        }

    except Exception as e:
        print("ANALYZE REPORT ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))
