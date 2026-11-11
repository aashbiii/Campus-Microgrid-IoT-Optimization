"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Institution: Rajagiri College of Social Sciences (RCSS), Kalamassery
Central Configuration & Single Source of Truth
"""

import os
import paho.mqtt.client as mqtt

# ---------------------------------------------------------
# Institution Metadata
# ---------------------------------------------------------
INSTITUTION_NAME = "Rajagiri College of Social Sciences (RCSS), Kalamassery"
SYSTEM_NAME = "RCSS Campus Micro-Grid & Energy Optimization System"

# ---------------------------------------------------------
# MQTT Broker Settings
# ---------------------------------------------------------
MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883

# Unique namespace prefix to isolate messages on public broker
TOPIC_PREFIX = "campusmg_x7k2"

# ---------------------------------------------------------
# Operational Modes & Thresholds
# ---------------------------------------------------------
DEMO_MODE = True  # True: 30s idle detection, fast evaluation; False: 900s (15 min)

IDLE_POWER_W = 150.0  # Threshold in Watts below which a lab is flagged idle
IDLE_DURATION_S = 30 if DEMO_MODE else 900  # Persistence duration before shutdown
PUBLISH_INTERVAL_S = 5  # Telemetry publish interval for hardware nodes
DAEMON_PUBLISH_INTERVAL_S = 40  # Telemetry publish interval for Python virtual nodes (40 seconds)

# ---------------------------------------------------------
# Machine Learning Parameters
# ---------------------------------------------------------
FORECAST_HORIZON_H = 6  # Number of hours to forecast into the future

# ---------------------------------------------------------
# Directories & File Paths
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODELS_DIR = os.path.join(BASE_DIR, "models")
DB_PATH = os.path.join(DATA_DIR, "microgrid.db")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# ---------------------------------------------------------
# RCSS Lab Profiles (4 Labs: CC Lab, Carmel Lab, Assumption Lab, Chavara Lab)
# ---------------------------------------------------------
LABS = {
    "cc_lab": {
        "name": "CC Lab",
        "full_name": "Central Computing Lab (CC Lab)",
        "type": "wokwi",
        "base_load_w": 3500.0,
        "standby_w": 200.0,
        "num_pcs": 35,
        "active_hours": (9, 17),
        "lunch_hour": 13,
        "weekend_active": False,
        "description": "35 High-performance workstations, core campus curriculum & coding practicals"
    },
    "carmel_lab": {
        "name": "Carmel Lab",
        "full_name": "Carmel Block Computing Lab",
        "type": "wokwi",
        "base_load_w": 2800.0,
        "standby_w": 150.0,
        "num_pcs": 25,
        "active_hours": (9, 16),
        "lunch_hour": 13,
        "weekend_active": False,
        "description": "25 Workstations, project incubation & electronics bench testing"
    },
    "assumption_lab": {
        "name": "Assumption Lab",
        "full_name": "Assumption Block Research Lab",
        "type": "python",
        "base_load_w": 2400.0,
        "standby_w": 140.0,
        "num_pcs": 20,
        "active_hours": (8, 16),
        "lunch_hour": 12,
        "weekend_active": False,
        "description": "20 Research terminals, data analytics & statistical computing"
    },
    "chavera_lab": {
        "name": "Chavara Lab",
        "full_name": "Chavara Block Computing Centre",
        "type": "python",
        "base_load_w": 3000.0,
        "standby_w": 180.0,
        "num_pcs": 30,
        "active_hours": (9, 17),
        "lunch_hour": 13,
        "weekend_active": False,
        "description": "30 Workstations, AI/ML laboratory & institutional seminar computing"
    },
}

# ---------------------------------------------------------
# MQTT Client Factory (Paho-MQTT 1.x & 2.x Compatibility)
# ---------------------------------------------------------
def create_mqtt_client(client_id: str) -> mqtt.Client:
    """Create an MQTT client instance compatible with both paho-mqtt v1 and v2."""
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    return mqtt.Client(client_id=client_id)
