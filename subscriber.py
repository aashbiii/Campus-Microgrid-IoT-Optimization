"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Subscriber Module: Ingests real-time MQTT telemetry into SQLite with WAL mode.
"""

import json
import sqlite3
import random
import datetime
import paho.mqtt.client as mqtt
import config

def get_db_connection():
    """Returns a short-lived SQLite connection configured with WAL mode."""
    conn = sqlite3.connect(config.DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def on_connect(client, userdata, flags, rc):
    wildcard_topic = f"{config.TOPIC_PREFIX}/+/telemetry"
    if rc == 0:
        client.subscribe(wildcard_topic)
        print(f"[subscriber] Connected to MQTT broker ({config.MQTT_HOST}).")
        print(f"[subscriber] Subscribed to wildcard telemetry: {wildcard_topic}")
    else:
        print(f"[subscriber] Connection failed with result code {rc}")

def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode("utf-8")
        data = json.loads(payload_str)

        # Validate mandatory telemetry fields
        required_fields = ["lab_id", "voltage", "current", "power_w", "relay_state"]
        if not all(field in data for field in required_fields):
            print(f"[subscriber] Warning: Dropping malformed message: {payload_str}")
            return

        lab_id = str(data["lab_id"])
        
        # Always use current gateway/server timestamp for live telemetry.
        # This completely resolves edge clock drift or hardcoded dummy timestamps from microcontrollers!
        ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        voltage = float(data["voltage"])
        current = float(data["current"])
        power_w = float(data["power_w"])
        relay_state = str(data["relay_state"]).upper()

        if relay_state not in ("ON", "OFF"):
            relay_state = "ON"

        # Atomic insert
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO readings (lab_id, ts, voltage, current, power_w, relay_state, source)
            VALUES (?, ?, ?, ?, ?, ?, 'live')
        """, (lab_id, ts, voltage, current, power_w, relay_state))
        conn.commit()
        conn.close()
        print(f"[subscriber] Live Telemetry -> {lab_id}: {power_w:.1f}W | {voltage:.1f}V | {current:.2f}A | Relay: {relay_state}")

    except json.JSONDecodeError:
        print(f"[subscriber] Error: Received non-JSON message on {msg.topic}")
    except Exception as e:
        print(f"[subscriber] Ingestion error: {e}")

def run_subscriber():
    client_id = f"cm_eos_subscriber_{random.randint(1000, 9999)}"
    client = config.create_mqtt_client(client_id)
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"[subscriber] Connecting to {config.MQTT_HOST}:{config.MQTT_PORT}...")
    try:
        client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[subscriber] Ingestion service shutting down cleanly.")
        client.disconnect()

if __name__ == "__main__":
    run_subscriber()
