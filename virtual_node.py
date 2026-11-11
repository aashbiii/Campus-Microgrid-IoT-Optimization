"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Virtual Edge Nodes: Multi-threaded Python nodes emulating software-modeled labs.
"""

import time
import json
import random
import threading
import datetime
import config

class VirtualLabNode(threading.Thread):
    def __init__(self, lab_id: str, cfg: dict):
        super().__init__()
        self.lab_id = lab_id
        self.cfg = cfg
        self.relay_state = "ON"
        self.running = True
        self.client_id = f"virtual_{lab_id}_{random.randint(1000, 9999)}"
        self.client = config.create_mqtt_client(self.client_id)
        self.telemetry_topic = f"{config.TOPIC_PREFIX}/{lab_id}/telemetry"
        self.cmd_topic = f"{config.TOPIC_PREFIX}/{lab_id}/cmd"

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(self.cmd_topic)
            print(f"[node:{self.lab_id}] Connected to broker. Listening on {self.cmd_topic}")
        else:
            print(f"[node:{self.lab_id}] Connection failed with code {rc}")

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8").strip()
        print(f"[node:{self.lab_id}] Command received: '{payload}'")
        if payload == "SHUTDOWN":
            self.relay_state = "OFF"
            print(f"[node:{self.lab_id}] Relay tripped to OFF (Standby mode).")
        elif payload == "RESTORE":
            self.relay_state = "ON"
            print(f"[node:{self.lab_id}] Relay restored to ON (Normal operation).")

    def compute_power(self, dt: datetime.datetime) -> float:
        # If shut down, clamp immediately to standby
        if self.relay_state == "OFF":
            return self.cfg["standby_w"] + abs(random.gauss(0, 3.0))

        hour = dt.hour + (dt.minute / 60.0)
        weekday = dt.weekday()
        is_weekend = weekday >= 5

        base = self.cfg["base_load_w"]
        standby = self.cfg["standby_w"]
        start_h, end_h = self.cfg["active_hours"]

        if is_weekend and not self.cfg["weekend_active"]:
            return standby + abs(random.gauss(0, 4.0))

        if config.DEMO_MODE:
            # Active operational load in Demo Mode (solid healthy draw, well above 150W cutoff)
            pc_usage = random.betavariate(5, 2)
            noise = random.gauss(0, base * 0.015)
            power = standby + ((base - standby) * 0.82 * pc_usage) + noise
            return max(standby, round(power, 2))

        if start_h <= hour < end_h:
            sched = 0.90
            if self.cfg["lunch_hour"] and int(hour) == self.cfg["lunch_hour"]:
                sched = 0.35
        elif (start_h - 1) <= hour < start_h:
            sched = 0.25
        elif end_h <= hour < (end_h + 1):
            sched = 0.15
        else:
            sched = 0.02

        pc_usage = random.betavariate(5, 2)
        noise = random.gauss(0, base * 0.02)
        power = standby + ((base - standby) * sched * pc_usage) + noise
        return max(standby, round(power, 2))

    def run(self):
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        try:
            self.client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
            self.client.loop_start()
            time.sleep(1.0)
        except Exception as e:
            print(f"[node:{self.lab_id}] Could not connect to HiveMQ: {e}")
            return

        interval = getattr(config, "DAEMON_PUBLISH_INTERVAL_S", 40)

        while self.running:
            now = datetime.datetime.now()
            power = self.compute_power(now)
            voltage = round(random.gauss(230.0, 1.2), 2)
            current = round(power / voltage, 2)

            payload = {
                "lab_id": self.lab_id,
                "voltage": voltage,
                "current": current,
                "power_w": power,
                "relay_state": self.relay_state,
                "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S")
            }

            try:
                self.client.publish(self.telemetry_topic, json.dumps(payload))
            except Exception as e:
                print(f"[node:{self.lab_id}] Publish error: {e}")

            time.sleep(interval)

        self.client.loop_stop()
        self.client.disconnect()
        print(f"[node:{self.lab_id}] Cleanly stopped.")

    def stop(self):
        self.running = False

def start_virtual_nodes():
    nodes = []
    print("[virtual_node] Initializing virtual lab nodes...")
    for lab_id, cfg in config.LABS.items():
        if cfg["type"] == "python":
            node = VirtualLabNode(lab_id, cfg)
            node.daemon = True
            node.start()
            nodes.append(node)
    return nodes

if __name__ == "__main__":
    active_nodes = start_virtual_nodes()
    print(f"[virtual_node] {len(active_nodes)} Python nodes active and streaming telemetry.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[virtual_node] Stopping all virtual nodes...")
        for n in active_nodes:
            n.stop()
        for n in active_nodes:
            n.join(timeout=2)
        print("[virtual_node] All nodes stopped.")
