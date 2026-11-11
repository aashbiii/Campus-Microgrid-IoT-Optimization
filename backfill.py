"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Backfill Module: Generates 30 days of high-fidelity synthetic 5-minute telemetry.
"""

import sqlite3
import random
import datetime
import numpy as np
import config

def init_database():
    """Initializes tables and WAL mode in SQLite."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lab_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            voltage REAL NOT NULL,
            current REAL NOT NULL,
            power_w REAL NOT NULL,
            relay_state TEXT NOT NULL CHECK(relay_state IN ('ON', 'OFF')),
            source TEXT NOT NULL CHECK(source IN ('backfill', 'live'))
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_readings_lab_ts ON readings(lab_id, ts);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS shutdown_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lab_id TEXT NOT NULL,
            ts_flagged TEXT NOT NULL,
            ts_restored TEXT,
            avg_power_w REAL NOT NULL,
            reason TEXT NOT NULL,
            kwh_saved_est REAL DEFAULT 0.0,
            confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0, 1))
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_shutdown_lab_ts ON shutdown_log(lab_id, ts_flagged);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lab_id TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            target_ts TEXT NOT NULL,
            predicted_w REAL NOT NULL
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_forecasts_lab_target ON forecasts(lab_id, target_ts);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS model_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trained_at TEXT NOT NULL,
            scope TEXT NOT NULL,
            mae REAL NOT NULL,
            r2 REAL NOT NULL,
            baseline_mae REAL NOT NULL
        );
    """)
    conn.commit()
    conn.close()

def compute_synthetic_power(lab_cfg, dt: datetime.datetime, is_exam_week: bool, inject_idle: bool) -> float:
    """Computes realistic power based on lab profile, schedule, and stochastic factors."""
    hour = dt.hour + (dt.minute / 60.0)
    weekday = dt.weekday()
    is_weekend = weekday >= 5

    base = lab_cfg["base_load_w"]
    standby = lab_cfg["standby_w"]
    start_h, end_h = lab_cfg["active_hours"]

    # 1. Weekend Logic
    if is_weekend and not lab_cfg["weekend_active"]:
        return standby + abs(random.gauss(0, 4.0))

    # 2. Idle anomaly injection (e.g. cancelled class during active hours)
    if inject_idle and start_h <= hour < end_h:
        return standby + abs(random.gauss(0, 10.0))

    # 3. Schedule curve factor
    if start_h <= hour < end_h:
        if lab_cfg["lunch_hour"] and int(hour) == lab_cfg["lunch_hour"]:
            schedule_factor = 0.38  # Lunch dip
        else:
            schedule_factor = 0.92  # Active class block
    elif (start_h - 1) <= hour < start_h:
        schedule_factor = 0.25  # Pre-class arrival
    elif end_h <= hour < (end_h + 1):
        schedule_factor = 0.15  # Post-class winding down
    else:
        schedule_factor = 0.02  # Night off-hours

    # 4. Student PC utilization distribution
    pc_usage = random.betavariate(5, 2)

    # 5. Exam week multiplier (increases load during active hours)
    exam_multiplier = 1.25 if (is_exam_week and start_h <= hour < end_h) else 1.0

    # 6. Bounded electrical noise
    noise = random.gauss(0, base * 0.025)

    power = standby + ((base - standby) * schedule_factor * pc_usage * exam_multiplier) + noise
    return max(standby, round(power, 2))

def run_backfill(days=30, force=False):
    """Fills the database with 30 days of 5-minute interval historical readings."""
    init_database()
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM readings WHERE source = 'backfill'")
    count = cur.fetchone()[0]
    if count > 0 and not force:
        print(f"[backfill] Historical data already populated ({count} readings). Skipping.")
        conn.close()
        return

    if force and count > 0:
        print("[backfill] Force flag enabled: clearing previous backfill records...")
        cur.execute("DELETE FROM readings WHERE source = 'backfill'")
        conn.commit()

    print(f"[backfill] Generating {days} days of historical 5-minute data across {len(config.LABS)} labs...")
    
    end_time = datetime.datetime.now().replace(microsecond=0)
    # Align to clean 5-minute boundary
    end_time = end_time - datetime.timedelta(minutes=end_time.minute % 5, seconds=end_time.second)
    start_time = end_time - datetime.timedelta(days=days)
    curr_time = start_time

    batch = []
    step = datetime.timedelta(minutes=5)

    # Pre-select idle anomaly windows (approx 1 per week per lab)
    total_slots = int((end_time - start_time).total_seconds() / 300)
    anomaly_indices = set(random.sample(range(total_slots), k=min(total_slots, 40)))

    slot_counter = 0
    total_inserted = 0

    while curr_time <= end_time:
        day_offset = (curr_time - start_time).days
        is_exam = 20 <= day_offset <= 27  # Exam week bump
        is_anomaly = slot_counter in anomaly_indices

        ts_str = curr_time.strftime("%Y-%m-%dT%H:%M:%S")

        for lab_id, lab_cfg in config.LABS.items():
            power = compute_synthetic_power(lab_cfg, curr_time, is_exam, is_anomaly)
            voltage = round(random.gauss(230.0, 1.3), 2)
            current = round(power / voltage, 2)

            batch.append((
                lab_id,
                ts_str,
                voltage,
                current,
                power,
                "ON",
                "backfill"
            ))

        slot_counter += 1

        if len(batch) >= 6000:
            cur.executemany("""
                INSERT INTO readings (lab_id, ts, voltage, current, power_w, relay_state, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, batch)
            conn.commit()
            total_inserted += len(batch)
            batch = []

        curr_time += step

    if batch:
        cur.executemany("""
            INSERT INTO readings (lab_id, ts, voltage, current, power_w, relay_state, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, batch)
        conn.commit()
        total_inserted += len(batch)

    conn.close()
    print(f"[backfill] Finished successfully! Inserted {total_inserted} historical readings.")

if __name__ == "__main__":
    run_backfill(days=30)
