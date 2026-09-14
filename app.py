from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import sqlite3
import secrets
from datetime import datetime
import hashlib
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

app = Flask(__name__)

DATABASE = "parking.db"

# Secret key for session cookies
app.secret_key = secrets.token_hex(32)

# Simple admin credentials (change the password later if you like)
ADMIN_USERNAME = "admin"
# This is a hash of the password "admin123"
ADMIN_PASSWORD_HASH = hashlib.sha256("admin123".encode("utf-8")).hexdigest()

# Mail Configuration - Strictly using Environment Variables (No exposed secrets)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

# --------------------------------------------------
# PARKING BUILDING LOCATION
# --------------------------------------------------
BUILDING = {
    "name": "Smart Parking Building",
    "lat": 17.385044,
    "lon": 78.486671
}

ARRIVE_RADIUS_METRES = 40

# --------------------------------------------------
# DATABASE CONNECTION
# --------------------------------------------------
def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection

# --------------------------------------------------
# EMAIL DISPATCH HELPER
# --------------------------------------------------
def send_parking_ticket_email(user_email, slot_id, floor, session_id):
    host_url = request.host_url.rstrip('/')
    parking_url = f"{host_url}/parking/{session_id}"

    subject = f"Smart Parking Ticket & Route Map — Slot {slot_id}"
    
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #0f172a; color: #ffffff; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #1e293b; padding: 20px; border-radius: 8px;">
            <h2 style="color: #38bdf8; text-align: center;">Parking Ticket & Exit Map</h2>
            <p><strong>Assigned Slot:</strong> {slot_id}</p>
            <p><strong>Floor Level:</strong> Floor {floor}</p>
            <hr style="border-color: #334155;" />
            
            <h3 style="color: #4ade80;">Visual Exit Route</h3>
            <div style="text-align: center; margin: 20px 0;">
                <svg width="400" height="150" style="background:#0f172a; border-radius:8px;">
                    <rect x="20" y="20" width="80" height="40" fill="#3b82f6" rx="5"/>
                    <text x="60" y="45" fill="white" font-size="12" text-anchor="middle">Slot {slot_id}</text>
                    
                    <path d="M 60 70 L 60 110 L 320 110 L 320 130" stroke="#f97316" stroke-width="4" fill="none" stroke-dasharray="5,5"/>
                    <polygon points="320,135 315,125 325,125" fill="#f97316"/>
                    
                    <rect x="270" y="105" width="100" height="35" fill="#22c55e" rx="5"/>
                    <text x="320" y="127" fill="white" font-size="12" text-anchor="middle">EXIT GATE</text>
                </svg>
            </div>
            
            <div style="text-align: center; margin-top: 25px;">
                <a href="{parking_url}" style="background-color: #38bdf8; color: #0f172a; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">
                    VIEW LIVE PARKING DASHBOARD
                </a>
            </div>
        </div>
    </body>
    </html>
    """

    try:
        mail_username = app.config['MAIL_USERNAME']
        mail_password = app.config['MAIL_PASSWORD']

        if not mail_username or not mail_password:
            print("Error: Missing MAIL_USERNAME or MAIL_PASSWORD environment variables.")
            return False

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = mail_username
        msg['To'] = user_email
        msg.attach(MIMEText(html_content, 'html'))

        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        if app.config['MAIL_USE_TLS']:
            server.starttls()
        server.login(mail_username, mail_password.replace(" ", ""))
        server.send_message(msg)
        server.quit()

        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

# --------------------------------------------------
# SHARED HELPERS FOR THE MAP TEMPLATES
# --------------------------------------------------
def floors_snapshot(connection):
    snapshot = {}
    for floor_number in range(1, 4):
        rows = connection.execute("""
            SELECT id, status
            FROM parking_slots
            WHERE floor = ?
            ORDER BY id ASC
        """, (floor_number,)).fetchall()
        snapshot[str(floor_number)] = [dict(row) for row in rows]
    return snapshot

def occupancy_stats(connection):
    total = connection.execute("""
        SELECT COUNT(*) AS count FROM parking_slots
    """).fetchone()["count"]

    available = connection.execute("""
        SELECT COUNT(*) AS count
        FROM parking_slots
        WHERE status = 'AVAILABLE'
    """).fetchone()["count"]

    return {
        "total": total,
        "available": available,
        "occupied": total - available
    }

def readable_duration(start_text, end_text):
    try:
        start = datetime.fromisoformat(start_text)
        end = datetime.fromisoformat(end_text)
    except (TypeError, ValueError):
        return "not available"

    minutes = int((end - start).total_seconds() // 60)

    if minutes < 1:
        return "less than a minute"

    hours, minutes = divmod(minutes, 60)

    if hours and minutes:
        return f"{hours} h {minutes} min"

    if hours:
        return f"{hours} h"

    return f"{minutes} min"

# --------------------------------------------------
# ADMIN HELPERS
# --------------------------------------------------
def is_admin_logged_in():
    return session.get("admin_logged_in") is True

def require_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin_logged_in():
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

def get_db_dict():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# --------------------------------------------------
# CREATE DATABASE
# --------------------------------------------------
def initialize_database():
    connection = get_db()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parking_slots (
            id TEXT PRIMARY KEY,
            floor INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'AVAILABLE'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parking_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            slot_id TEXT NOT NULL,
            floor INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            exit_time TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE'
        )
    """)

    for floor in range(1, 4):
        for number in range(1, 21):
            slot_id = f"F{floor}-{number:02d}"
            cursor.execute("""
                INSERT OR IGNORE INTO parking_slots
                (id, floor, status)
                VALUES (?, ?, 'AVAILABLE')
            """, (slot_id, floor))

    connection.commit()
    connection.close()

# --------------------------------------------------
# HOME PAGE
# --------------------------------------------------
@app.route("/")
def home():
    connection = get_db()
    floors = []

    for floor_number in range(1, 4):
        total = connection.execute("""
            SELECT COUNT(*) AS count
            FROM parking_slots
            WHERE floor = ?
        """, (floor_number,)).fetchone()["count"]

        available = connection.execute("""
            SELECT COUNT(*) AS count
            FROM parking_slots
            WHERE floor = ?
            AND status = 'AVAILABLE'
        """, (floor_number,)).fetchone()["count"]

        floors.append({
            "floor": floor_number,
            "total": total,
            "available": available
        })

    connection.close()

    return render_template(
        "home.html",
        floors=floors
    )

# --------------------------------------------------
# AUTOMATICALLY ALLOCATE PARKING SLOT
# --------------------------------------------------
@app.route("/allocate", methods=["POST"])
def allocate():
    connection = get_db()

    slot = connection.execute("""
        SELECT *
        FROM parking_slots
        WHERE status = 'AVAILABLE'
        ORDER BY floor ASC, id ASC
        LIMIT 1
    """).fetchone()

    if slot is None:
        connection.close()
        return render_template("full.html")

    floors_data = floors_snapshot(connection)
    connection.close()

    return render_template(
        "allocated.html",
        slot=slot,
        floors_data=floors_data,
        target=slot["id"],
        target_floor=slot["floor"]
    )

# --------------------------------------------------
# TOP-VIEW PARKING MAP (ALL THREE FLOORS)
# --------------------------------------------------
@app.route("/map")
@app.route("/map/<slot_id>")
def parking_map(slot_id=None):
    connection = get_db()
    target = None
    target_floor = None

    if slot_id is not None:
        slot = connection.execute("""
            SELECT *
            FROM parking_slots
            WHERE id = ?
        """, (slot_id,)).fetchone()

        if slot is None:
            connection.close()
            return "Parking slot not found", 404

        target = slot["id"]
        target_floor = slot["floor"]

    floors_data = floors_snapshot(connection)
    connection.close()

    return render_template(
        "floormap.html",
        floors_data=floors_data,
        target=target,
        target_floor=target_floor
    )

# --------------------------------------------------
# LIVE LOCATION TRACKING / NAVIGATION
# --------------------------------------------------
@app.route("/navigate/<slot_id>")
def navigate(slot_id):
    connection = get_db()
    slot = connection.execute("""
        SELECT *
        FROM parking_slots
        WHERE id = ?
    """, (slot_id,)).fetchone()

    if slot is None:
        connection.close()
        return "Parking slot not found", 404

    floors_data = floors_snapshot(connection)
    connection.close()

    return render_template(
        "navigate.html",
        slot=slot,
        floors_data=floors_data,
        target=slot["id"],
        target_floor=slot["floor"],
        building=BUILDING,
        arrive_radius=ARRIVE_RADIUS_METRES
    )

# --------------------------------------------------
# USER REACHED PARKING SLOT
# --------------------------------------------------
@app.route("/reach/<slot_id>", methods=["POST"])
def reach(slot_id):
    connection = get_db()
    slot = connection.execute("""
        SELECT *
        FROM parking_slots
        WHERE id = ?
    """, (slot_id,)).fetchone()

    connection.close()

    if slot is None:
        return "Parking slot not found", 404

    return render_template(
        "email.html",
        slot_id=slot_id
    )

# --------------------------------------------------
# CREATE PARKING SESSION
# --------------------------------------------------
@app.route("/create-session/<slot_id>", methods=["POST"])
def create_session(slot_id):
    email = request.form.get("email", "").strip()

    if not email:
        return "Email address is required", 400

    connection = get_db()

    slot = connection.execute("""
        SELECT *
        FROM parking_slots
        WHERE id = ?
    """, (slot_id,)).fetchone()

    if slot is None:
        connection.close()
        return "Parking slot not found", 404

    token = secrets.token_urlsafe(32)
    start_time = datetime.now().isoformat(timespec="seconds")

    cursor = connection.cursor()

    cursor.execute("""
        UPDATE parking_slots
        SET status = 'OCCUPIED'
        WHERE id = ?
        AND status = 'AVAILABLE'
    """, (slot_id,))

    if cursor.rowcount != 1:
        connection.close()
        return render_template("full.html")

    cursor.execute("""
        INSERT INTO parking_sessions
        (token, email, slot_id, floor, start_time, status)
        VALUES (?, ?, ?, ?, ?, 'ACTIVE')
    """, (token, email, slot_id, slot["floor"], start_time))

    connection.commit()
    session_id = cursor.lastrowid

    parking = connection.execute("""
        SELECT *
        FROM parking_sessions
        WHERE id = ?
    """, (session_id,)).fetchone()

    connection.close()

    # Dispatch email ticket with route map
    send_parking_ticket_email(email, slot_id, slot["floor"], session_id)

    return render_template(
        "success.html",
        parking=parking
    )

# --------------------------------------------------
# PARKING PAGE
# --------------------------------------------------
@app.route("/parking/<session_id>")
def parking(session_id):
    connection = get_db()

    parking_session = connection.execute("""
        SELECT *
        FROM parking_sessions
        WHERE id = ?
    """, (session_id,)).fetchone()

    if parking_session is None:
        connection.close()
        return "Parking session not found", 404

    floors_data = floors_snapshot(connection)
    stats = occupancy_stats(connection)
    connection.close()

    return render_template(
        "parking.html",
        parking=parking_session,
        stats=stats,
        floors_data=floors_data,
        target=parking_session["slot_id"],
        target_floor=parking_session["floor"]
    )

# --------------------------------------------------
# VEHICLE FOUND
# --------------------------------------------------
@app.route("/found/<session_id>", methods=["POST"])
def found(session_id):
    connection = get_db()

    parking_session = connection.execute("""
        SELECT *
        FROM parking_sessions
        WHERE id = ?
        AND status = 'ACTIVE'
    """, (session_id,)).fetchone()

    if parking_session is None:
        connection.close()
        return "Parking session not found", 404

    floors_data = floors_snapshot(connection)
    connection.close()

    return render_template(
        "exit.html",
        parking=parking_session,
        floors_data=floors_data,
        target=parking_session["slot_id"],
        target_floor=parking_session["floor"]
    )

# --------------------------------------------------
# CONFIRM VEHICLE HAS EXITED
# --------------------------------------------------
@app.route("/exit/<session_id>", methods=["POST"])
def exit_parking(session_id):
    connection = get_db()

    parking_session = connection.execute("""
        SELECT *
        FROM parking_sessions
        WHERE id = ?
        AND status = 'ACTIVE'
    """, (session_id,)).fetchone()

    if parking_session is None:
        connection.close()
        return "Parking session not found", 404

    exit_time = datetime.now().isoformat(timespec="seconds")

    connection.execute("""
        UPDATE parking_sessions
        SET exit_time = ?, status = 'COMPLETED'
        WHERE id = ?
    """, (exit_time, session_id))

    connection.execute("""
        UPDATE parking_slots
        SET status = 'AVAILABLE'
        WHERE id = ?
    """, (parking_session["slot_id"],))

    connection.commit()
    connection.close()

    return render_template(
        "cleared.html",
        parking=parking_session,
        exit_time=exit_time,
        duration=readable_duration(
            parking_session["start_time"],
            exit_time
        )
    )

# --------------------------------------------------
# ADMIN LOGIN
# --------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()

        if username == ADMIN_USERNAME and password_hash == ADMIN_PASSWORD_HASH:
            session["admin_logged_in"] = True
            session["admin_username"] = username
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid username or password", "error")

    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    session.pop("admin_username", None)
    return redirect(url_for("admin_login"))

# --------------------------------------------------
# ADMIN DASHBOARD
# --------------------------------------------------
@app.route("/admin")
@require_admin
def admin_dashboard():
    conn = get_db_dict()

    total = conn.execute("SELECT COUNT(*) AS c FROM parking_slots").fetchone()["c"]
    available = conn.execute(
        "SELECT COUNT(*) AS c FROM parking_slots WHERE status = 'AVAILABLE'"
    ).fetchone()["c"]
    occupied = total - available

    active_sessions = conn.execute("""
        SELECT *
        FROM parking_sessions
        WHERE status = 'ACTIVE'
        ORDER BY start_time DESC
    """).fetchall()

    all_slots = conn.execute("""
        SELECT *
        FROM parking_slots
        ORDER BY floor, id
    """).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        total=total,
        available=available,
        occupied=occupied,
        active_sessions=active_sessions,
        all_slots=all_slots,
    )

# --------------------------------------------------
# ADMIN SLOT ACTIONS
# --------------------------------------------------
@app.route("/admin/slot/<slot_id>/set_status", methods=["POST"])
@require_admin
def admin_set_slot_status(slot_id):
    status = request.form.get("status", "").upper()
    if status not in ("AVAILABLE", "OCCUPIED"):
        flash("Invalid status", "error")
        return redirect(url_for("admin_dashboard"))

    conn = get_db_dict()
    conn.execute(
        "UPDATE parking_slots SET status = ? WHERE id = ?",
        (status, slot_id),
    )
    conn.commit()
    conn.close()

    flash(f"Slot {slot_id} marked as {status}", "success")
    return redirect(url_for("admin_dashboard"))

# --------------------------------------------------
# ADMIN BOOKING ACTIONS
# --------------------------------------------------
@app.route("/admin/session/<int:session_id>/cancel", methods=["POST"])
@require_admin
def admin_cancel_session(session_id):
    conn = get_db_dict()

    sess = conn.execute(
        "SELECT * FROM parking_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()

    if sess is None:
        flash("Session not found", "error")
        conn.close()
        return redirect(url_for("admin_dashboard"))

    if sess["status"] == "ACTIVE":
        conn.execute(
            "UPDATE parking_slots SET status = 'AVAILABLE' WHERE id = ?",
            (sess["slot_id"],),
        )

    conn.execute(
        "UPDATE parking_sessions SET status = 'CANCELLED' WHERE id = ?",
        (session_id,),
    )

    conn.commit()
    conn.close()

    flash(f"Booking {session_id} cancelled", "success")
    return redirect(url_for("admin_dashboard"))

# --------------------------------------------------
# ADMIN SLOT MANAGEMENT
# --------------------------------------------------
@app.route("/admin/slot/add", methods=["POST"])
@require_admin
def admin_add_slot():
    slot_id = request.form.get("slot_id", "").strip()
    floor = request.form.get("floor", "").strip()
    status = request.form.get("status", "AVAILABLE").upper()

    if not slot_id or not floor:
        flash("Slot ID and floor are required", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        floor_int = int(floor)
    except ValueError:
        flash("Floor must be a number", "error")
        return redirect(url_for("admin_dashboard"))

    if status not in ("AVAILABLE", "OCCUPIED"):
        status = "AVAILABLE"

    conn = get_db_dict()
    try:
        conn.execute(
            "INSERT INTO parking_slots (id, floor, status) VALUES (?, ?, ?)",
            (slot_id, floor_int, status),
        )
        conn.commit()
        flash(f"Slot {slot_id} added", "success")
    except sqlite3.IntegrityError:
        flash("Slot ID already exists", "error")
    finally:
        conn.close()

    return redirect(url_for("admin_dashboard"))

@app.route("/admin/slot/<slot_id>/delete", methods=["POST"])
@require_admin
def admin_delete_slot(slot_id):
    conn = get_db_dict()

    row = conn.execute(
        "SELECT status FROM parking_slots WHERE id = ?",
        (slot_id,),
    ).fetchone()

    if row is None:
        flash("Slot not found", "error")
        conn.close()
        return redirect(url_for("admin_dashboard"))

    if row["status"] == "OCCUPIED":
        flash("Cannot delete an occupied slot", "error")
        conn.close()
        return redirect(url_for("admin_dashboard"))

    conn.execute("DELETE FROM parking_slots WHERE id = ?", (slot_id,))
    conn.commit()
    conn.close()

    flash(f"Slot {slot_id} deleted", "success")
    return redirect(url_for("admin_dashboard"))

# --------------------------------------------------
# START APPLICATION
# --------------------------------------------------
if __name__ == "__main__":
    initialize_database()
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )
