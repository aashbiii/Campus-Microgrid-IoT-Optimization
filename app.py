"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Institution: Rajagiri College of Social Sciences (RCSS), Kalamassery
Engineering Team: Team PIXEL

Production Flask Real-Time Microgrid Application:
- Multi-threaded MQTT Ingestion (HiveMQ -> SQLite WAL)
- In-memory Sub-Second Live State & Dynamic Countdown Engine
- Machine Learning Demand Forecasting (GBR)
- Closed-Loop Autonomous Actuation (SHUTDOWN / RESTORE)
- Real-Time REST API & High-Performance Web Dashboard
"""

import os
import sys
import json
import time
import random
import sqlite3
import datetime
import threading
from flask import Flask, render_template, jsonify, request

import config
import optimizer
from virtual_node import VirtualLabNode

app = Flask(__name__)

# -------------------------------------------------------------
# Global In-Memory State & Thread Synchronization
# -------------------------------------------------------------
state_lock = threading.Lock()

# Live cache: lab_id -> dict with latest reading
live_telemetry = {}

# Idle tracking: lab_id -> float (timestamp when power first dropped below idle threshold)
idle_start_times = {}

# MQTT Client instances
mqtt_sub_client = None
mqtt_pub_client = None

def get_db():
    conn = sqlite3.connect(config.DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

# -------------------------------------------------------------
# MQTT Background Subscriber
# -------------------------------------------------------------
def on_mqtt_connect(client, userdata, flags, rc):
    topic = f"{config.TOPIC_PREFIX}/+/telemetry"
    if rc == 0:
        client.subscribe(topic)
        print(f"[flask:mqtt] Connected to {config.MQTT_HOST}. Subscribed to {topic}")
    else:
        print(f"[flask:mqtt] Connection failed with code {rc}")

def on_mqtt_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        lab_id = str(payload.get("lab_id"))
        if lab_id not in config.LABS:
            return

        voltage = float(payload.get("voltage", 230.0))
        current = float(payload.get("current", 0.0))
        power_w = float(payload.get("power_w", 0.0))
        relay_state = str(payload.get("relay_state", "ON")).upper()
        if relay_state not in ("ON", "OFF"):
            relay_state = "ON"

        now_dt = datetime.datetime.now()
        ts_str = now_dt.strftime("%Y-%m-%dT%H:%M:%S")

        # Update In-Memory Cache
        with state_lock:
            live_telemetry[lab_id] = {
                "lab_id": lab_id,
                "power_w": round(power_w, 1),
                "voltage": round(voltage, 1),
                "current": round(current, 2),
                "relay_state": relay_state,
                "timestamp": ts_str,
                "last_seen": time.time()
            }

            # Manage Idle Countdown Tracking
            if relay_state == "ON" and power_w < config.IDLE_POWER_W:
                if lab_id not in idle_start_times:
                    idle_start_times[lab_id] = time.time()
            else:
                if lab_id in idle_start_times:
                    del idle_start_times[lab_id]

        # Persist to SQLite
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO readings (lab_id, ts, voltage, current, power_w, relay_state, source)
            VALUES (?, ?, ?, ?, ?, ?, 'live')
        """, (lab_id, ts_str, voltage, current, power_w, relay_state))

        # If lab restored to ON, close any pending shutdown record & compute M&V savings
        if relay_state == "ON":
            cur.execute("""
                SELECT id, ts_flagged FROM shutdown_log
                WHERE lab_id = ? AND ts_restored IS NULL
                ORDER BY id DESC LIMIT 1
            """, (lab_id,))
            open_row = cur.fetchone()
            if open_row:
                log_id, ts_flag = open_row
                try:
                    t_flagged = datetime.datetime.strptime(ts_flag, "%Y-%m-%dT%H:%M:%S")
                    duration_s = max(1.0, (now_dt - t_flagged).total_seconds())
                    exp_w = optimizer.get_expected_power_for_hour(lab_id, now_dt.hour)
                    standby_w = config.LABS.get(lab_id, {}).get("standby_w", 150.0)
                    saved_kwh = round(max(0.0, ((exp_w - standby_w) * (duration_s / 3600.0)) / 1000.0), 4)
                    cur.execute("""
                        UPDATE shutdown_log
                        SET ts_restored = ?, kwh_saved_est = ?
                        WHERE id = ?
                    """, (ts_str, saved_kwh, log_id))
                except Exception as ex:
                    print(f"[flask:mqtt] Savings computation error: {ex}")

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"[flask:mqtt] Error processing telemetry packet: {e}")

def run_mqtt_subscriber():
    global mqtt_sub_client
    client_id = f"flask_sub_{random.randint(1000, 9999)}"
    mqtt_sub_client = config.create_mqtt_client(client_id)
    mqtt_sub_client.on_connect = on_mqtt_connect
    mqtt_sub_client.on_message = on_mqtt_message

    while True:
        try:
            mqtt_sub_client.connect(config.MQTT_HOST, config.MQTT_PORT, 60)
            mqtt_sub_client.loop_forever()
        except Exception as e:
            print(f"[flask:mqtt] Subscriber reconnecting after error: {e}")
            time.sleep(3)

# -------------------------------------------------------------
# Autonomous Decision & Optimization Worker
# -------------------------------------------------------------
def run_autonomous_optimizer():
    global mqtt_pub_client
    pub_id = f"flask_opt_{random.randint(1000, 9999)}"
    mqtt_pub_client = config.create_mqtt_client(pub_id)
    try:
        mqtt_pub_client.connect(config.MQTT_HOST, config.MQTT_PORT, 60)
        mqtt_pub_client.loop_start()
    except Exception as e:
        print(f"[flask:opt] Publisher connect error: {e}")

    print("[flask:opt] Autonomous optimizer worker active.")

    while True:
        try:
            now_t = time.time()
            now_dt = datetime.datetime.now()

            for lab_id, cfg in config.LABS.items():
                with state_lock:
                    telemetry = live_telemetry.get(lab_id)
                    idle_start = idle_start_times.get(lab_id)

                if not telemetry:
                    continue

                power = telemetry["power_w"]
                relay = telemetry["relay_state"]

                if relay == "ON" and power < config.IDLE_POWER_W and idle_start is not None:
                    elapsed = now_t - idle_start
                    remaining = max(0, config.IDLE_DURATION_S - int(elapsed))

                    if remaining <= 0:
                        # Auto-shutdown trigger condition met!
                        print(f"[flask:opt] >>> IDLE TIMEOUT REACHED for {cfg['name']} ({power:.1f} W for {elapsed:.1f}s)! Triggering SHUTDOWN...")
                        
                        # Dispatch over MQTT
                        cmd_topic = f"{config.TOPIC_PREFIX}/{lab_id}/cmd"
                        if mqtt_pub_client:
                            mqtt_pub_client.publish(cmd_topic, "SHUTDOWN", qos=1, retain=True)
                            print(f"[flask:opt] Published retained SHUTDOWN to {cmd_topic}")

                        # Record in database
                        conn = get_db()
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT id FROM shutdown_log
                            WHERE lab_id = ? AND ts_restored IS NULL
                            ORDER BY id DESC LIMIT 1
                        """, (lab_id,))
                        if not cur.fetchone():
                            cur.execute("""
                                INSERT INTO shutdown_log (lab_id, ts_flagged, avg_power_w, reason, confirmed)
                                VALUES (?, ?, ?, 'Autonomous Idle Timeout', 0)
                            """, (lab_id, now_dt.strftime("%Y-%m-%dT%H:%M:%S"), power))
                            conn.commit()
                        conn.close()

                        # Update in-memory state
                        with state_lock:
                            if lab_id in idle_start_times:
                                del idle_start_times[lab_id]
                            live_telemetry[lab_id]["relay_state"] = "OFF"

        except Exception as e:
            print(f"[flask:opt] Optimization loop iteration error: {e}")

        time.sleep(1.0)

# -------------------------------------------------------------
# Virtual Nodes Runner
# -------------------------------------------------------------
def run_virtual_nodes():
    nodes = []
    for lab_id, cfg in config.LABS.items():
        if cfg["type"] == "python":
            n = VirtualLabNode(lab_id, cfg)
            n.daemon = True
            n.start()
            nodes.append(n)
    print(f"[flask:virt] Started {len(nodes)} Python virtual edge nodes.")

# -------------------------------------------------------------
# Web Routes & REST API Endpoints
# -------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", labs=config.LABS, institution=config.INSTITUTION_NAME)

@app.route("/api/status")
def api_status():
    """Returns real-time status, load metrics, active timers, and KPIs."""
    now_t = time.time()
    labs_status = {}
    total_power = 0.0
    active_count = 0
    standby_count = 0

    with state_lock:
        for lab_id, cfg in config.LABS.items():
            telem = live_telemetry.get(lab_id, {
                "lab_id": lab_id,
                "power_w": 0.0,
                "voltage": 230.0,
                "current": 0.0,
                "relay_state": "ON",
                "timestamp": "Awaiting Telemetry",
                "last_seen": 0
            })

            cur_p = telem["power_w"]
            cur_relay = telem["relay_state"]
            total_power += cur_p

            if cur_relay == "ON":
                active_count += 1
            else:
                standby_count += 1

            idle_start = idle_start_times.get(lab_id)
            is_idle = (cur_relay == "ON" and cur_p < config.IDLE_POWER_W and idle_start is not None)
            
            if is_idle:
                elapsed = max(0, int(now_t - idle_start))
                remaining = max(0, config.IDLE_DURATION_S - elapsed)
            else:
                elapsed = 0
                remaining = config.IDLE_DURATION_S

            labs_status[lab_id] = {
                "id": lab_id,
                "name": cfg["name"],
                "full_name": cfg["full_name"],
                "type": cfg["type"],
                "description": cfg["description"],
                "power_w": cur_p,
                "voltage": telem["voltage"],
                "current": telem["current"],
                "relay_state": cur_relay,
                "timestamp": telem["timestamp"],
                "is_idle": is_idle,
                "elapsed_s": elapsed,
                "remaining_s": remaining,
                "idle_threshold_w": config.IDLE_POWER_W,
                "idle_duration_s": config.IDLE_DURATION_S
            }

    # Query Total Verified Savings from SQLite
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(kwh_saved_est), 0.0) FROM shutdown_log")
    kwh_saved = float(cur.fetchone()[0])
    conn.close()

    co2_saved = round(kwh_saved * 0.82, 3)

    return jsonify({
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "total_power_w": round(total_power, 1),
        "total_power_kw": round(total_power / 1000.0, 2),
        "active_labs": active_count,
        "standby_labs": standby_count,
        "total_labs": len(config.LABS),
        "kwh_saved": round(kwh_saved, 4),
        "co2_saved_kg": co2_saved,
        "labs": labs_status,
        "broker": f"{config.MQTT_HOST}:{config.MQTT_PORT}",
        "namespace": config.TOPIC_PREFIX
    })

@app.route("/api/telemetry")
def api_telemetry():
    """Returns recent power telemetry for time-series charts (last 60 mins)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT lab_id, ts, power_w
        FROM readings
        WHERE id IN (
            SELECT id FROM readings ORDER BY id DESC LIMIT 200
        )
        ORDER BY id ASC
    """)
    rows = cur.fetchall()
    conn.close()

    series = {lab_id: {"labels": [], "data": []} for lab_id in config.LABS}
    for lab_id, ts, power_w in rows:
        if lab_id in series:
            # Short timestamp string (HH:MM:SS)
            short_ts = ts.split("T")[-1] if "T" in ts else ts[-8:]
            series[lab_id]["labels"].append(short_ts)
            series[lab_id]["data"].append(power_w)

    return jsonify(series)

@app.route("/api/forecast")
def api_forecast():
    """Returns 6-hour ML forecast predictions from SQLite."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT target_ts, predicted_w
        FROM forecasts
        ORDER BY id DESC LIMIT 24
    """)
    rows = cur.fetchall()
    conn.close()

    # Aggregate by timestamp
    agg = {}
    for target_ts, pred in rows:
        short_t = target_ts.split("T")[-1][:5] if "T" in target_ts else target_ts[:16]
        agg[short_t] = round(agg.get(short_t, 0.0) + pred, 1)

    labels = list(agg.keys())
    data = list(agg.values())
    peak_val = max(data) if data else 0.0
    peak_label = labels[data.index(peak_val)] if data else ""

    return jsonify({
        "labels": labels,
        "data": data,
        "peak_power_w": peak_val,
        "peak_time": peak_label
    })

@app.route("/api/audit")
def api_audit():
    """Returns recent autonomous shutdown log audit trail."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, lab_id, ts_flagged, ts_restored, avg_power_w, reason, kwh_saved_est, confirmed
        FROM shutdown_log
        ORDER BY id DESC LIMIT 15
    """)
    rows = cur.fetchall()
    conn.close()

    logs = []
    for r in rows:
        lab_name = config.LABS.get(r[1], {}).get("name", r[1])
        logs.append({
            "id": r[0],
            "lab_id": r[1],
            "lab_name": lab_name,
            "ts_flagged": r[2],
            "ts_restored": r[3] if r[3] else "Active (Tripped)",
            "avg_power_w": round(r[4], 1) if r[4] else 0.0,
            "reason": r[5],
            "kwh_saved_est": round(r[6], 4) if r[6] else 0.0,
            "confirmed": bool(r[7])
        })
    return jsonify(logs)

@app.route("/api/models")
def api_models():
    """Returns ML validation metrics comparing GBR against baseline."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT scope, mae, r2, baseline_mae
        FROM model_metrics
        ORDER BY id DESC LIMIT 5
    """)
    rows = cur.fetchall()
    conn.close()

    metrics = []
    for r in rows:
        lab_name = config.LABS.get(r[0], {}).get("name", r[0])
        metrics.append({
            "scope": lab_name,
            "mae": round(r[1], 2),
            "r2": round(r[2], 3),
            "baseline_mae": round(r[3], 2)
        })
    return jsonify(metrics)

@app.route("/api/cmd/<lab_id>/<command>", methods=["POST"])
def api_command(lab_id, command):
    """Executes closed-loop SHUTDOWN or RESTORE command."""
    command = command.upper()
    if lab_id not in config.LABS or command not in ("SHUTDOWN", "RESTORE"):
        return jsonify({"success": False, "error": "Invalid request"}), 400

    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")

    # Send over MQTT with QoS 1 and retain=True so edge nodes immediately catch up
    topic = f"{config.TOPIC_PREFIX}/{lab_id}/cmd"
    if mqtt_pub_client:
        mqtt_pub_client.publish(topic, command, qos=1, retain=True)
        print(f"[flask:cmd] Dispatched retained '{command}' -> {topic}")

    conn = get_db()
    cur = conn.cursor()

    if command == "SHUTDOWN":
        # Log manual trip
        with state_lock:
            p = live_telemetry.get(lab_id, {}).get("power_w", 0.0)
            if lab_id in idle_start_times:
                del idle_start_times[lab_id]
            if lab_id in live_telemetry:
                live_telemetry[lab_id]["relay_state"] = "OFF"

        cur.execute("""
            INSERT INTO shutdown_log (lab_id, ts_flagged, avg_power_w, reason, confirmed)
            VALUES (?, ?, ?, 'Manual Dashboard Override', 0)
        """, (lab_id, now_str, p))
        conn.commit()

    elif command == "RESTORE":
        # Close open shutdown record & compute savings
        cur.execute("""
            SELECT id, ts_flagged FROM shutdown_log
            WHERE lab_id = ? AND ts_restored IS NULL
            ORDER BY id DESC LIMIT 1
        """, (lab_id,))
        open_row = cur.fetchone()

        saved_kwh = 0.0
        if open_row:
            log_id, ts_flag = open_row
            t_flagged = datetime.datetime.strptime(ts_flag, "%Y-%m-%dT%H:%M:%S")
            duration_s = max(1.0, (now - t_flagged).total_seconds())

            exp_w = optimizer.get_expected_power_for_hour(lab_id, now.hour)
            standby_w = config.LABS[lab_id]["standby_w"]
            saved_kwh = round(max(0.0, ((exp_w - standby_w) * (duration_s / 3600.0)) / 1000.0), 4)

            cur.execute("""
                UPDATE shutdown_log
                SET ts_restored = ?, kwh_saved_est = ?
                WHERE id = ?
            """, (now_str, saved_kwh, log_id))
            conn.commit()

        with state_lock:
            if lab_id in live_telemetry:
                live_telemetry[lab_id]["relay_state"] = "ON"

    conn.close()
    return jsonify({"success": True, "lab_id": lab_id, "command": command})

# -------------------------------------------------------------
# System Initialization & Server Launch
# -------------------------------------------------------------
def initialize_system():
    # 1. Check SQLite DB
    if not os.path.exists(config.DB_PATH):
        print("[init] DB missing. Generating backfill data...")
        from backfill import run_backfill
        run_backfill(days=30)

    # 2. Check Models
    sample_model = os.path.join(config.MODELS_DIR, "cc_lab_gbr.joblib")
    if not os.path.exists(sample_model):
        print("[init] ML models missing. Training initial models...")
        from forecast import train_and_forecast
        train_and_forecast()

    # 3. Populate initial live_telemetry from latest DB readings
    conn = get_db()
    cur = conn.cursor()
    for lab_id in config.LABS:
        cur.execute("""
            SELECT power_w, voltage, current, relay_state, ts
            FROM readings WHERE lab_id = ? ORDER BY id DESC LIMIT 1
        """, (lab_id,))
        row = cur.fetchone()
        if row:
            p_val = round(row[0], 1)
            r_val = str(row[3]).upper()
            live_telemetry[lab_id] = {
                "lab_id": lab_id,
                "power_w": p_val,
                "voltage": round(row[1], 1),
                "current": round(row[2], 2),
                "relay_state": r_val,
                "timestamp": str(row[4]),
                "last_seen": time.time()
            }
            if r_val == "ON" and p_val < config.IDLE_POWER_W:
                idle_start_times[lab_id] = time.time()
    conn.close()

    # 4. Launch Background Threads
    t_sub = threading.Thread(target=run_mqtt_subscriber, daemon=True)
    t_sub.start()

    t_opt = threading.Thread(target=run_autonomous_optimizer, daemon=True)
    t_opt.start()

    t_virt = threading.Thread(target=run_virtual_nodes, daemon=True)
    t_virt.start()

    print("[init] All backend microgrid services initialized.")

if __name__ == "__main__":
    initialize_system()
    port = int(os.environ.get("PORT", 5000))
    print("=" * 70)
    print("  RCSS CAMPUS MICRO-GRID & ENERGY OPTIMIZATION SYSTEM (CM-EOS)")
    print(f"  * Web Dashboard: http://localhost:{port}")
    print(f"  * HiveMQ Broker: {config.MQTT_HOST}:{config.MQTT_PORT}")
    print(f"  * Namespace:     {config.TOPIC_PREFIX}")
    print("=" * 70)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
