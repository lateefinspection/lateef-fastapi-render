from fastapi import FastAPI
from pydantic import BaseModel
import mysql.connector
from typing import Optional, List

app = FastAPI()

# =========================
# DATABASE CONNECTION
# =========================
def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="homefax_user",
        password="Begreat78.",
        database="homefax"
    )

# =========================
# HELPERS
# =========================
def normalize(text):
    return (text or "").lower().strip()

def create_alert(conn, record_id, alert_type, severity, title):
    cursor = conn.cursor()

    dedupe_key = f"{record_id}:{alert_type}"

    cursor.execute("SELECT id FROM home_alerts WHERE dedupe_key=%s", (dedupe_key,))
    if cursor.fetchone():
        return False

    cursor.execute("""
        INSERT INTO home_alerts (record_id, alert_type, severity, title, status, dedupe_key)
        VALUES (%s,%s,%s,%s,'active',%s)
    """, (record_id, alert_type, severity, title, dedupe_key))

    conn.commit()
    return True


# =========================
# MODELS
# =========================
class DeviceCreate(BaseModel):
    device_name: Optional[str] = ""
    brand: Optional[str] = ""
    model: Optional[str] = ""
    location: Optional[str] = ""
    protocol: Optional[str] = "unknown"
    capabilities: Optional[List[str]] = []


class DeviceReading(BaseModel):
    device_id: int
    capability_key: str
    reading_type: str
    reading_value: Optional[str] = None
    numeric_value: Optional[float] = None
    unit: Optional[str] = ""


class TaskUpdate(BaseModel):
    status: str
    note: Optional[str] = None


# =========================
# DEVICES
# =========================
@app.post("/inspections/{record_id}/devices")
def add_device(record_id: str, device: DeviceCreate):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO devices (record_id, device_name, brand, model, location, protocol)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (
        record_id,
        device.device_name,
        device.brand,
        device.model,
        device.location,
        device.protocol
    ))

    device_id = cursor.lastrowid

    capabilities = device.capabilities or ["unknown_device"]

    for cap in capabilities:
        cursor.execute("""
            INSERT INTO device_capabilities (record_id, device_id, capability_key)
            VALUES (%s,%s,%s)
        """, (record_id, device_id, cap))

    conn.commit()
    cursor.close()
    conn.close()

    return {"status": "device_added", "device_id": device_id}


@app.get("/inspections/{record_id}/devices")
def get_devices(record_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM devices WHERE record_id=%s", (record_id,))
    devices = cursor.fetchall()

    for d in devices:
        cursor.execute("""
            SELECT capability_key FROM device_capabilities WHERE device_id=%s
        """, (d["id"],))
        d["capabilities"] = [c["capability_key"] for c in cursor.fetchall()]

    cursor.close()
    conn.close()
    return devices


# =========================
# DEVICE READINGS + ALERT ENGINE
# =========================
@app.post("/inspections/{record_id}/device-readings")
def add_reading(record_id: str, reading: DeviceReading):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Store reading
    cursor.execute("""
        INSERT INTO device_readings
        (record_id, device_id, capability_key, reading_type, reading_value, numeric_value, unit)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (
        record_id,
        reading.device_id,
        reading.capability_key,
        reading.reading_type,
        reading.reading_value,
        reading.numeric_value,
        reading.unit
    ))

    # Get issues (context)
    cursor.execute("SELECT * FROM issues WHERE record_id=%s", (record_id,))
    issues = cursor.fetchall()

    issue_titles = [normalize(i["title"]) for i in issues]
    has_roof_issue = any("roof" in t or "flashing" in t for t in issue_titles)

    alerts_triggered = []

    # =========================
    # RULE 1: WATER LEAK
    # =========================
    if reading.capability_key == "water_leak_detection":
        if reading.reading_value == "detected":
            if create_alert(conn, record_id, "water_leak", "critical", "Water leak detected"):
                alerts_triggered.append("water_leak")

    # =========================
    # RULE 2: HUMIDITY + ROOF
    # =========================
    if reading.capability_key == "temperature_humidity_monitoring":
        if reading.numeric_value and reading.numeric_value > 70 and has_roof_issue:
            if create_alert(conn, record_id, "moisture_risk", "high", "High humidity with roof risk"):
                alerts_triggered.append("moisture_risk")

    # =========================
    # RULE 3: RAIN + ROOF DAMAGE
    # =========================
    if reading.capability_key == "weather_monitoring":
        if reading.reading_type == "rainfall" and reading.numeric_value:
            if reading.numeric_value > 0.2 and has_roof_issue:
                if create_alert(conn, record_id, "roof_risk_escalation", "critical", "Rain detected with roof vulnerability"):
                    alerts_triggered.append("roof_risk_escalation")

    cursor.close()
    conn.close()

    return {"alerts_triggered": alerts_triggered}


@app.get("/inspections/{record_id}/alerts")
def get_alerts(record_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM home_alerts WHERE record_id=%s
    """, (record_id,))
    alerts = cursor.fetchall()

    cursor.close()
    conn.close()
    return alerts


# =========================
# AUTOMATION ENGINE
# =========================
def trade_for_alert(alert_type):
    if "roof" in alert_type:
        return "roofer"
    if "water" in alert_type:
        return "plumber"
    return "general_contractor"


def create_task(conn, alert):
    cursor = conn.cursor()

    dedupe = f"{alert['record_id']}:{alert['id']}"

    cursor.execute("SELECT id FROM automation_tasks WHERE dedupe_key=%s", (dedupe,))
    if cursor.fetchone():
        return False

    cursor.execute("""
        INSERT INTO automation_tasks
        (record_id, alert_id, task_type, priority, title, recommended_trade, dedupe_key)
        VALUES (%s,%s,%s,'urgent',%s,%s,%s)
    """, (
        alert["record_id"],
        alert["id"],
        alert["alert_type"],
        f"Respond to {alert['alert_type']}",
        trade_for_alert(alert["alert_type"]),
        dedupe
    ))

    conn.commit()
    return True


@app.post("/inspections/{record_id}/automation/run")
def run_automation(record_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM home_alerts WHERE record_id=%s AND status='active'
    """, (record_id,))
    alerts = cursor.fetchall()

    created = 0

    for alert in alerts:
        if create_task(conn, alert):
            created += 1

    cursor.close()
    conn.close()

    return {
        "alerts_found": len(alerts),
        "tasks_created": created
    }


@app.get("/inspections/{record_id}/automation/tasks")
def get_tasks(record_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM automation_tasks WHERE record_id=%s
    """, (record_id,))
    tasks = cursor.fetchall()

    cursor.close()
    conn.close()
    return tasks


@app.post("/automation/tasks/{task_id}/status")
def update_task(task_id: int, update: TaskUpdate):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE automation_tasks SET status=%s WHERE id=%s
    """, (update.status, task_id))

    conn.commit()
    cursor.close()
    conn.close()

    return {"status": "updated"}
