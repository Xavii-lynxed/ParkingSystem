"""
Modern Parking System
Multimedia University of Kenya - Data Structures and Algorithm, Task One

Implements Modules 1-7 as documented in Documentation/algorithms.txt,
backed by the SQLite schema in Documentation/database_design.txt.

Run with:  python parking_system.py
Then open: http://127.0.0.1:5000
"""

import sqlite3
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)
DB_NAME = "parking.db"

# Number of slots to seed the lot with on first run.
TOTAL_SLOTS = 10

# In-memory waiting queue (Module 7). Deliberately NOT persisted to the
# database -- see database_design.txt for the reasoning: a vehicle that
# never receives a slot has no transaction to record.
waiting_queue = []  # each entry: {"reg_number": str, "vehicle_type": str, "requested_time": str}


# ---------------------------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------------------------

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist, and seed the Slots table."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS Slots (
            slot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_number TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('Available', 'Occupied'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS Vehicles (
            registration_number TEXT PRIMARY KEY,
            vehicle_type TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS Transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_number TEXT NOT NULL,
            slot_id INTEGER NOT NULL,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            amount_paid REAL,
            status TEXT NOT NULL CHECK(status IN ('Parked', 'Exited')),
            FOREIGN KEY (registration_number) REFERENCES Vehicles(registration_number),
            FOREIGN KEY (slot_id) REFERENCES Slots(slot_id)
        )
    """)

    # Seed slots only if the table is empty (first run).
    cur.execute("SELECT COUNT(*) AS c FROM Slots")
    if cur.fetchone()["c"] == 0:
        for i in range(1, TOTAL_SLOTS + 1):
            cur.execute(
                "INSERT INTO Slots (slot_number, status) VALUES (?, ?)",
                (f"A{i}", "Available")
            )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# MODULE 3: Parking Record Store (Persistence Layer)
# All other modules read/write through these functions rather than
# touching the database directly.
# ---------------------------------------------------------------------------

def m3_create_transaction(reg_number, vehicle_type, slot_id, entry_time):
    conn = get_connection()
    cur = conn.cursor()

    # Ensure the vehicle exists in Vehicles (insert if new).
    cur.execute(
        "INSERT OR IGNORE INTO Vehicles (registration_number, vehicle_type) VALUES (?, ?)",
        (reg_number, vehicle_type)
    )

    cur.execute(
        """INSERT INTO Transactions
           (registration_number, slot_id, entry_time, exit_time, amount_paid, status)
           VALUES (?, ?, ?, NULL, NULL, 'Parked')""",
        (reg_number, slot_id, entry_time)
    )
    conn.commit()
    conn.close()


def m3_find_active_transaction(reg_number):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM Transactions WHERE registration_number = ? AND status = 'Parked'",
        (reg_number,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def m3_update_transaction(transaction_id, exit_time, amount_paid, status):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """UPDATE Transactions
           SET exit_time = ?, amount_paid = ?, status = ?
           WHERE transaction_id = ?""",
        (exit_time, amount_paid, status, transaction_id)
    )
    conn.commit()
    conn.close()


def m3_get_all_slots():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM Slots ORDER BY slot_id")
    rows = cur.fetchall()
    conn.close()
    return rows


def m3_get_active_transactions_with_details():
    """For the admin dashboard: occupied slots joined with vehicle + entry info."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.slot_id, t.registration_number, v.vehicle_type, t.entry_time
        FROM Transactions t
        JOIN Vehicles v ON v.registration_number = t.registration_number
        WHERE t.status = 'Parked'
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def m3_set_slot_status(slot_id, status):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Slots SET status = ? WHERE slot_id = ?", (status, slot_id))
    conn.commit()
    conn.close()


def m3_get_revenue_today():
    conn = get_connection()
    cur = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute(
        "SELECT COALESCE(SUM(amount_paid), 0) AS total FROM Transactions "
        "WHERE status = 'Exited' AND exit_time LIKE ?",
        (f"{today}%",)
    )
    total = cur.fetchone()["total"]
    conn.close()
    return total


# ---------------------------------------------------------------------------
# MODULE 1: Locate Available Slot (helper subroutine)
# ---------------------------------------------------------------------------

def m1_locate_available_slot():
    for slot in m3_get_all_slots():
        if slot["status"] == "Available":
            return slot
    return None


# ---------------------------------------------------------------------------
# MODULE 7: Waiting Queue Management
# ---------------------------------------------------------------------------

def m7_enqueue(reg_number, vehicle_type):
    entry = {
        "reg_number": reg_number,
        "vehicle_type": vehicle_type,
        "requested_time": datetime.now().isoformat(timespec="seconds"),
    }
    waiting_queue.append(entry)
    return len(waiting_queue)  # position in queue


def m7_dequeue():
    if not waiting_queue:
        return None
    return waiting_queue.pop(0)


def m7_position(reg_number):
    for i, entry in enumerate(waiting_queue):
        if entry["reg_number"] == reg_number:
            return i + 1
    return None


# ---------------------------------------------------------------------------
# MODULE 2: Vehicle Entry
# ---------------------------------------------------------------------------

def m2_vehicle_entry(reg_number, vehicle_type):
    reg_number = reg_number.strip().upper()

    # Step 4-5: reject if this vehicle already has an active transaction.
    if m3_find_active_transaction(reg_number) is not None:
        return {"status": "error", "message": f"{reg_number} is already parked."}

    # Step 6: try to find a slot.
    slot = m1_locate_available_slot()

    if slot is None:
        # Step 7: lot full -> enqueue instead of rejecting outright.
        position = m7_enqueue(reg_number, vehicle_type)
        return {
            "status": "queued",
            "message": f"Parking full. {reg_number} is position {position} in the queue.",
        }

    # Steps 8-11: assign slot, create transaction, mark slot occupied.
    entry_time = datetime.now().isoformat(timespec="seconds")
    m3_create_transaction(reg_number, vehicle_type, slot["slot_id"], entry_time)
    m3_set_slot_status(slot["slot_id"], "Occupied")

    return {
        "status": "success",
        "message": f"{reg_number} assigned to slot {slot['slot_number']}.",
    }


# ---------------------------------------------------------------------------
# MODULE 4: Vehicle Exit & Fee Calculation
# ---------------------------------------------------------------------------

def m4_calculate_fee(duration_minutes):
    if duration_minutes <= 30:
        return 0
    elif duration_minutes <= 120:
        return 50
    elif duration_minutes <= 240:
        return 100
    elif duration_minutes <= 360:
        return 300
    else:
        return 500


def m4_vehicle_exit(reg_number, payment_successful=True):
    reg_number = reg_number.strip().upper()

    # Step 3-4: find the active transaction.
    txn = m3_find_active_transaction(reg_number)
    if txn is None:
        return {"status": "error", "message": f"{reg_number} not found in active records."}

    # Steps 5-7: duration calculation.
    entry_time = datetime.fromisoformat(txn["entry_time"])
    exit_time_dt = datetime.now()
    duration_minutes = (exit_time_dt - entry_time).total_seconds() / 60

    # Step 8: fee determination.
    fee = m4_calculate_fee(duration_minutes)

    if not payment_successful:
        # Step 12: payment failed -- caller is responsible for retry/cap logic
        # in the web layer (see the /exit route below).
        return {
            "status": "payment_failed",
            "message": "Payment unsuccessful. Please try again.",
            "fee": fee,
            "duration_minutes": round(duration_minutes, 1),
        }

    # Step 11: payment succeeded.
    exit_time_str = exit_time_dt.isoformat(timespec="seconds")
    m3_update_transaction(txn["transaction_id"], exit_time_str, fee, "Exited")
    m3_set_slot_status(txn["slot_id"], "Available")

    # MODULE 5: Barrier Control (simulated -- no physical hardware here).
    barrier_event = m5_open_barrier(reg_number)

    # Step 11d: offer the freed slot to the next vehicle in the queue.
    promoted_message = None
    next_in_queue = m7_dequeue()
    if next_in_queue is not None:
        result = m2_vehicle_entry(next_in_queue["reg_number"], next_in_queue["vehicle_type"])
        promoted_message = f"Queue: {result['message']}"

    return {
        "status": "success",
        "message": f"{reg_number} exited. Duration: {round(duration_minutes, 1)} min. Fee: Ksh {fee}.",
        "fee": fee,
        "duration_minutes": round(duration_minutes, 1),
        "barrier": barrier_event,
        "promoted_message": promoted_message,
    }


# ---------------------------------------------------------------------------
# MODULE 5: Barrier Control (simulated)
# ---------------------------------------------------------------------------

def m5_open_barrier(reg_number):
    # No physical barrier available in this environment, so this simulates
    # the open/close cycle and logs the event. In a real deployment this
    # would send a signal to hardware (e.g. via GPIO or a controller API).
    return {
        "event": "barrier_opened",
        "reg_number": reg_number,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# MODULE 6a / 6b: Display data (used by the Flask routes below)
# ---------------------------------------------------------------------------

def m6a_public_summary():
    slots = m3_get_all_slots()
    total = len(slots)
    occupied = sum(1 for s in slots if s["status"] == "Occupied")
    available = total - occupied
    return {"total": total, "occupied": occupied, "available": available,
            "queue_length": len(waiting_queue)}


def m6b_admin_data():
    slots = m3_get_all_slots()
    active = {row["slot_id"]: row for row in m3_get_active_transactions_with_details()}
    slot_rows = []
    for slot in slots:
        if slot["slot_id"] in active:
            txn = active[slot["slot_id"]]
            slot_rows.append({
                "slot_number": slot["slot_number"],
                "status": "Occupied",
                "reg_number": txn["registration_number"],
                "vehicle_type": txn["vehicle_type"],
                "entry_time": txn["entry_time"],
            })
        else:
            slot_rows.append({
                "slot_number": slot["slot_number"],
                "status": "Available",
                "reg_number": "", "vehicle_type": "", "entry_time": "",
            })

    summary = m6a_public_summary()
    return {
        "slots": slot_rows,
        "summary": summary,
        "revenue_today": m3_get_revenue_today(),
        "queue": waiting_queue,
    }


# ---------------------------------------------------------------------------
# FLASK ROUTES (web layer)
# ---------------------------------------------------------------------------

BASE_STYLE = """
<style>
  body { font-family: Arial, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; }
  h1 { color: #1a4d2e; }
  nav a { margin-right: 16px; }
  table { border-collapse: collapse; width: 100%; margin-top: 12px; }
  th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
  th { background: #f0f0f0; }
  .available { color: green; font-weight: bold; }
  .occupied { color: #b00; font-weight: bold; }
  .flash { padding: 10px; margin: 12px 0; border-radius: 4px; }
  .flash-success { background: #d9f7d9; }
  .flash-error { background: #f7d9d9; }
  .flash-queued { background: #f7f0d9; }
  form { margin: 16px 0; }
  input { padding: 6px; margin-right: 8px; }
  button { padding: 6px 14px; }
</style>
"""

NAV = """
<nav>
  <a href="/">Home (Public Display)</a>
  <a href="/entry">Vehicle Entry</a>
  <a href="/exit">Vehicle Exit</a>
  <a href="/dashboard">Admin Dashboard</a>
</nav>
<hr>
"""


@app.route("/")
def index():
    # MODULE 6a: Public Slot Display
    summary = m6a_public_summary()
    html = BASE_STYLE + NAV + """
    <h1>Modern Parking System - Availability</h1>
    <p>Total slots: {{ s.total }}</p>
    <p class="available">Available: {{ s.available }}</p>
    <p class="occupied">Occupied: {{ s.occupied }}</p>
    <p>Vehicles waiting: {{ s.queue_length }}</p>
    """
    return render_template_string(html, s=summary)


@app.route("/entry", methods=["GET", "POST"])
def entry():
    result = None
    if request.method == "POST":
        reg_number = request.form.get("reg_number", "")
        vehicle_type = request.form.get("vehicle_type", "")
        result = m2_vehicle_entry(reg_number, vehicle_type)

    html = BASE_STYLE + NAV + """
    <h1>Vehicle Entry</h1>
    {% if result %}
      <div class="flash flash-{{ result.status }}">{{ result.message }}</div>
    {% endif %}
    <form method="post">
      <input name="reg_number" placeholder="Registration number" required>
      <input name="vehicle_type" placeholder="Vehicle type (car, motorbike...)" required>
      <button type="submit">Check In</button>
    </form>
    """
    return render_template_string(html, result=result)


@app.route("/exit", methods=["GET", "POST"])
def exit_route():
    result = None
    if request.method == "POST":
        reg_number = request.form.get("reg_number", "")
        payment_ok = request.form.get("payment_successful") == "yes"
        result = m4_vehicle_exit(reg_number, payment_successful=payment_ok)

    html = BASE_STYLE + NAV + """
    <h1>Vehicle Exit</h1>
    {% if result %}
      <div class="flash flash-{{ 'error' if result.status != 'success' else 'success' }}">
        {{ result.message }}
      </div>
      {% if result.promoted_message %}
        <div class="flash flash-success">{{ result.promoted_message }}</div>
      {% endif %}
    {% endif %}
    <form method="post">
      <input name="reg_number" placeholder="Registration number" required>
      <label>
        <input type="checkbox" name="payment_successful" value="yes" checked>
        Simulate successful payment
      </label>
      <button type="submit">Check Out</button>
    </form>
    <p style="color:#666">Uncheck the box to simulate a failed payment (barrier stays closed).</p>
    """
    return render_template_string(html, result=result)


@app.route("/dashboard")
def dashboard():
    # MODULE 6b: Admin Dashboard
    data = m6b_admin_data()
    html = BASE_STYLE + NAV + """
    <h1>Admin Dashboard</h1>
    <p>Available: {{ data.summary.available }} / {{ data.summary.total }}
       | Revenue today: Ksh {{ data.revenue_today }}
       | Queue length: {{ data.queue|length }}</p>
    <table>
      <tr><th>Slot</th><th>Status</th><th>Reg. Number</th><th>Type</th><th>Entry Time</th></tr>
      {% for row in data.slots %}
      <tr>
        <td>{{ row.slot_number }}</td>
        <td class="{{ 'available' if row.status == 'Available' else 'occupied' }}">{{ row.status }}</td>
        <td>{{ row.reg_number }}</td>
        <td>{{ row.vehicle_type }}</td>
        <td>{{ row.entry_time }}</td>
      </tr>
      {% endfor %}
    </table>

    <h2>Waiting Queue</h2>
    {% if data.queue %}
    <table>
      <tr><th>#</th><th>Reg. Number</th><th>Type</th><th>Requested At</th></tr>
      {% for q in data.queue %}
      <tr>
        <td>{{ loop.index }}</td>
        <td>{{ q.reg_number }}</td>
        <td>{{ q.vehicle_type }}</td>
        <td>{{ q.requested_time }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
      <p>No vehicles waiting.</p>
    {% endif %}
    """
    return render_template_string(html, data=data)


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True)