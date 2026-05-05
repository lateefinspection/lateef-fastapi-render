from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
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

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID") or os.getenv("TWILIO_SID")
TWILIO_AUTH = os.getenv("TWILIO_AUTH_TOKEN") or os.getenv("TWILIO_AUTH")
TWILIO_PHONE = os.getenv("TWILIO_PHONE") or os.getenv("TWILIO_PHONE_NUMBER")

FALLBACK_USER_PHONE = os.getenv("USER_PHONE") or os.getenv("USER_PHONE_NUMBER")

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS") or os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") or os.getenv("EMAIL_PASS")
FALLBACK_EMAIL_TO = os.getenv("EMAIL_TO") or EMAIL_ADDRESS

# =========================
# DATABASE
# =========================
def get_db_connection():
    return pymysql.connect(
        host="127.0.0.1",
        user="homefax_user",
        password="StrongPassword123!",
        database="homefax",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )

# =========================
# AUTH HELPERS
# =========================
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return f"{salt}${digest}"

def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest = stored_hash.split("$", 1)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
        return hmac.compare_digest(check, digest)
    except:
        return False

def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload["sub"])
    except:
        raise HTTPException(401, "Invalid token")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM saas_users WHERE id=%s", (user_id,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user or not user["is_active"]:
        raise HTTPException(401, "User inactive")

    return user

# =========================
# NOTIFICATIONS
# =========================
def send_sms(to_phone, message):
    try:
        if not to_phone:
            return
        client = Client(TWILIO_SID, TWILIO_AUTH)
        client.messages.create(body=message, from_=TWILIO_PHONE, to=to_phone)
    except Exception as e:
        print("SMS ERROR:", e)

def send_email(to_email, subject, body):
    try:
        if not to_email:
            return
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        print("EMAIL ERROR:", e)

def notify_record_owner(record_id, subject, message):
    send_sms(FALLBACK_USER_PHONE, message)
    send_email(FALLBACK_EMAIL_TO, subject, message)

# =========================
# MODELS
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

# =========================
# ROOT
# =========================
@app.get("/")
def root():
    return {"status": "running"}

# =========================
# AUTH
# =========================
@app.post("/auth/register")
def register(payload: RegisterRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    password_hash = hash_password(payload.password)

    cursor.execute("""
        INSERT INTO saas_users (email, password_hash)
        VALUES (%s,%s)
    """, (payload.email, password_hash))

    conn.commit()
    user_id = cursor.lastrowid

    token = create_access_token(user_id, payload.email)

    cursor.close()
    conn.close()

    return {"token": token}

@app.post("/auth/login")
def login(payload: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM saas_users WHERE email=%s", (payload.email,))
    user = cursor.fetchone()

    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Invalid login")

    token = create_access_token(user["id"], user["email"])

    return {"token": token}

# =========================
# AUTOMATION ENGINE
# =========================
@app.post("/inspections/{record_id}/automation/run")
def run_automation(record_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM device_readings WHERE record_id=%s", (record_id,))
    readings = cursor.fetchall()

    alerts = 0
    tasks = 0

    for r in readings:
        if r["capability_key"] == "weather_monitoring" and float(r["numeric_value"] or 0) >= 0.2:
            key = f"{record_id}:rain"

            cursor.execute("""
                INSERT IGNORE INTO alerts
                (record_id, alert_type, severity, dedupe_key, status)
                VALUES (%s,'rain','medium',%s,'active')
            """, (record_id, key))

            if cursor.rowcount > 0:
                alerts += 1
                notify_record_owner(record_id, "Rain Alert", "Rain detected")

            task_key = f"{key}:task"

            cursor.execute("""
                INSERT IGNORE INTO automation_tasks
                (record_id, task_type, priority, title, description, recommended_trade, status, dedupe_key)
                VALUES (%s,'inspection','medium','Rain inspection','Check property','roofer','open',%s)
            """, (record_id, task_key))

            if cursor.rowcount > 0:
                tasks += 1

    conn.commit()
    cursor.close()
    conn.close()

    return {"alerts": alerts, "tasks": tasks}

# =========================
# AI INGESTION
# =========================
@app.post("/process-inspection")
def process_inspection(payload: dict):

    record_id = payload.get("record_id")
    findings = payload.get("findings", [])

    if not record_id:
        raise HTTPException(400, "Missing record_id")

    conn = get_db_connection()
    cursor = conn.cursor()

    alerts_created = 0
    tasks_created = 0

    for f in findings:
        finding_type = f.get("type", "unknown")
        severity = f.get("severity", "low")
        location = f.get("location", "unknown")
        notes = f.get("notes", "")

        alert_key = f"{record_id}:{finding_type}:{location}".lower()

        cursor.execute("""
            INSERT IGNORE INTO alerts
            (record_id, alert_type, severity, message, dedupe_key, status)
            VALUES (%s,%s,%s,%s,%s,'active')
        """, (record_id, finding_type, severity, notes, alert_key))

        if cursor.rowcount > 0:
            alerts_created += 1
            notify_record_owner(record_id, f"{finding_type} detected", notes)

        task_key = f"{alert_key}:task"

        cursor.execute("""
            INSERT IGNORE INTO automation_tasks
            (record_id, task_type, priority, title, description, recommended_trade, status, dedupe_key)
            VALUES (%s,%s,%s,%s,%s,%s,'open',%s)
        """, (
            record_id,
            finding_type,
            severity,
            f"{finding_type} issue",
            notes,
            "general_home_service",
            task_key
        ))

        if cursor.rowcount > 0:
            tasks_created += 1

    conn.commit()
    cursor.close()
    conn.close()

    return {
        "alerts_created": alerts_created,
        "tasks_created": tasks_created
    }
