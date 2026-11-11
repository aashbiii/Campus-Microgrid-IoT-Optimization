"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Optimizer Module: Real-time 3-factor idle detection, countdown tracking, closed-loop actuation, and M&V savings.
"""

import time
import sqlite3
import datetime
import random
import paho.mqtt.client as mqtt
import config

def get_db():
    conn = sqlite3.connect(config.DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def is_inside_schedule(lab_cfg: dict, dt: datetime.datetime) -> bool:
    """Verifies whether the current time falls inside scheduled lab hours."""
    if config.DEMO_MODE:
        return True  # Relaxed in Demo Mode for rapid testing

    weekday = dt.weekday()
    if weekday >= 5 and not lab_cfg["weekend_active"]:
        return False

    hour = dt.hour + (dt.minute / 60.0)
    start_h, end_h = lab_cfg["active_hours"]
    return start_h <= hour < end_h

def get_expected_power_for_hour(lab_id: str, hour: int) -> float:
    """Calculates historical average expected power for a lab during a specific hour."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT AVG(power_w) FROM readings
        WHERE lab_id = ? AND CAST(strftime('%H', ts) AS INTEGER) = ? AND source = 'backfill'
    """, (lab_id, hour))
    res = cur.fetchone()[0]
    conn.close()

    if res is not None and res > 0:
        return float(res)
    return config.LABS.get(lab_id, {}).get("base_load_w", 1500.0) * 0.85

def send_mqtt_command(lab_id: str, command: str):
    """Utility to publish a command string to a specific lab's command topic."""
    client_id = f"opt_cmd_sender_{random.randint(1000, 9999)}"
    client = config.create_mqtt_client(client_id)
    try:
        client.connect(config.MQTT_HOST, config.MQTT_PORT, 10)
        topic = f"{config.TOPIC_PREFIX}/{lab_id}/cmd"
        client.publish(topic, command, qos=1, retain=True)
        client.disconnect()
        print(f"[optimizer] Dispatched retained '{command}' -> {topic}")
    except Exception as e:
        print(f"[optimizer] Failed to send command to {lab_id}: {e}")

def compute_idle_status(lab_id: str, conn=None):
    """
    Evaluates whether a lab is in an idle streak, how long it has lasted,
    and how many seconds remain before automatic shutdown.
    Returns: (is_idle, elapsed_seconds, remaining_seconds, current_power, current_relay)
    """
    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True

    cur = conn.cursor()
    cur.execute("""
        SELECT id, power_w, relay_state, ts FROM readings
        WHERE lab_id = ?
        ORDER BY id DESC LIMIT 25
    """, (lab_id,))
    rows = cur.fetchall()

    if should_close:
        conn.close()

    if not rows:
        return False, 0, config.IDLE_DURATION_S, 0.0, "UNKNOWN"

    latest = rows[0]
    cur_p = float(latest[1])
    cur_relay = str(latest[2]).upper()
    latest_ts_str = str(latest[3])

    if cur_relay != "ON" or cur_p >= config.IDLE_POWER_W:
        return False, 0, config.IDLE_DURATION_S, cur_p, cur_relay

    # Lab is ON and below idle threshold: calculate continuous streak backwards
    streak_earliest_ts = latest_ts_str
    count = 0
    for r in rows:
        p, relay, ts = float(r[1]), str(r[2]).upper(), str(r[3])
        if relay == "ON" and p < config.IDLE_POWER_W:
            streak_earliest_ts = ts
            count += 1
        else:
            break

    now_dt = datetime.datetime.now()
    try:
        t_start = datetime.datetime.strptime(streak_earliest_ts, "%Y-%m-%dT%H:%M:%S")
        t_end = datetime.datetime.strptime(latest_ts_str, "%Y-%m-%dT%H:%M:%S")
        if (now_dt - t_end).total_seconds() <= 45:
            elapsed_sec = max(count * 5, int((now_dt - t_start).total_seconds()))
        else:
            elapsed_sec = count * 5
    except Exception:
        elapsed_sec = count * 5

    rem = max(0, config.IDLE_DURATION_S - elapsed_sec)
    return True, elapsed_sec, rem, cur_p, cur_relay

class OptimizerEngine:
    def __init__(self):
        self.client_id = f"cm_eos_optimizer_{random.randint(1000, 9999)}"
        self.client = config.create_mqtt_client(self.client_id)
        self.running = True

    def start_mqtt(self):
        try:
            self.client.connect(config.MQTT_HOST, config.MQTT_PORT, 60)
            self.client.loop_start()
            print(f"[optimizer] Connected to broker for command dispatching.")
        except Exception as e:
            print(f"[optimizer] MQTT connection error: {e}")

    def stop_mqtt(self):
        self.client.loop_stop()
        self.client.disconnect()

    def publish_cmd(self, lab_id: str, cmd: str):
        topic = f"{config.TOPIC_PREFIX}/{lab_id}/cmd"
        self.client.publish(topic, cmd)
        print(f"[optimizer] Command sent: {cmd} to {topic}")

    def evaluate_labs(self):
        """Audits all labs for idle conditions and verifies pending shutdowns."""
        now = datetime.datetime.now()
        conn = get_db()
        cur = conn.cursor()

        for lab_id, cfg in config.LABS.items():
            is_idle, elapsed, rem, cur_p, cur_relay = compute_idle_status(lab_id, conn)

            if is_idle and is_inside_schedule(cfg, now):
                if rem > 0:
                    print(f"[optimizer] {cfg['name']} IDLE WARNING: {cur_p:.1f}W (< {config.IDLE_POWER_W}W) | Auto-shutdown in {rem}s...")
                else:
                    # Time has elapsed! Check if already dispatched in the last 15 seconds
                    cur.execute("""
                        SELECT id, ts_flagged FROM shutdown_log
                        WHERE lab_id = ? AND ts_restored IS NULL
                        ORDER BY id DESC LIMIT 1
                    """, (lab_id,))
                    pending = cur.fetchone()

                    recently_dispatched = False
                    if pending:
                        try:
                            t_flag = datetime.datetime.strptime(pending[1], "%Y-%m-%dT%H:%M:%S")
                            if (now - t_flag).total_seconds() < 15:
                                recently_dispatched = True
                        except Exception:
                            pass

                    if not recently_dispatched:
                        now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
                        print(f"[optimizer] >>> AUTO-SHUTDOWN TRIGGERED for {cfg['name']} ({cur_p:.1f}W for {elapsed}s)! Sending SHUTDOWN to MQTT...")
                        cur.execute("""
                            INSERT INTO shutdown_log (lab_id, ts_flagged, avg_power_w, reason, confirmed)
                            VALUES (?, ?, ?, 'Autonomous Idle Timeout', 0)
                        """, (lab_id, now_str, cur_p))
                        conn.commit()

                        self.publish_cmd(lab_id, "SHUTDOWN")

            # ---------------------------------------------------------
            # Verification: Check unconfirmed shutdowns
            # ---------------------------------------------------------
            cur.execute("""
                SELECT id, ts_flagged FROM shutdown_log
                WHERE lab_id = ? AND confirmed = 0
            """, (lab_id,))
            unconfirmed = cur.fetchall()

            for log_id, t_flag in unconfirmed:
                cur.execute("""
                    SELECT relay_state, power_w FROM readings
                    WHERE lab_id = ? AND ts >= ? AND relay_state = 'OFF'
                    ORDER BY ts ASC LIMIT 1
                """, (lab_id, t_flag))
                confirm_reading = cur.fetchone()

                if confirm_reading:
                    cur.execute("UPDATE shutdown_log SET confirmed = 1 WHERE id = ?", (log_id,))
                    conn.commit()
                    print(f"[optimizer] Closed-loop verified: {cfg['name']} successfully confirmed shut down.")

        conn.close()

    def restore_lab(self, lab_id: str):
        """Restores a shut down lab, computes energy saved, and sends RESTORE command."""
        now = datetime.datetime.now()
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, ts_flagged FROM shutdown_log
            WHERE lab_id = ? AND ts_restored IS NULL
            ORDER BY id DESC LIMIT 1
        """, (lab_id,))
        open_shutdown = cur.fetchone()

        if open_shutdown:
            log_id, ts_flagged = open_shutdown
            t_flag = datetime.datetime.strptime(ts_flagged, "%Y-%m-%dT%H:%M:%S")
            duration_s = max(1.0, (now - t_flag).total_seconds())

            expected_w = get_expected_power_for_hour(lab_id, now.hour)
            standby_w = config.LABS.get(lab_id, {}).get("standby_w", 150.0)
            saved_kwh = ((expected_w - standby_w) * (duration_s / 3600.0)) / 1000.0
            saved_kwh = round(max(0.0, saved_kwh), 4)

            now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
            cur.execute("""
                UPDATE shutdown_log
                SET ts_restored = ?, kwh_saved_est = ?
                WHERE id = ?
            """, (now_str, saved_kwh, log_id))
            conn.commit()
            print(f"[optimizer] Restored {lab_id}. Duration: {duration_s:.1f}s | Energy Saved: {saved_kwh:.4f} kWh")

        conn.close()
        self.publish_cmd(lab_id, "RESTORE")

def run_optimizer():
    engine = OptimizerEngine()
    engine.start_mqtt()
    print("[optimizer] Autonomous optimizer active. Auditing every 3 seconds...")
    try:
        while True:
            try:
                engine.evaluate_labs()
            except Exception as e:
                print(f"[optimizer] Audit iteration error: {e}")
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[optimizer] Optimizer terminated cleanly.")
        engine.stop_mqtt()

if __name__ == "__main__":
    run_optimizer()
