/*
 * ==============================================================================
 * Campus Micro-Grid & Energy Optimization System (CM-EOS)
 * Institution: Rajagiri College of Social Sciences (RCSS), Kalamassery
 * Engineering Team: Team PIXEL
 * Edge Firmware: ESP32 DevKit + Potentiometer + Relay + Status LED
 *
 * Microcontroller: ESP32 DevKit V1
 * Sensors/Actuators:
 *   - Potentiometer Wiper -> GPIO 34 (Simulates Workstation Electrical Load)
 *   - Relay Control IN    -> GPIO 26 (Main Contactor / Relay Coil)
 *   - Status Indicator    -> GPIO 2  (Status Indicator LED)
 *
 * MQTT Broker: broker.hivemq.com:1883
 * Topic Prefix: campusmg_x7k2
 * ==============================================================================
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ------------------------------------------------------------------------------
// LAB IDENTITY SELECTOR (Uncomment ONE lab identity per Wokwi tab)
// Default is CC Lab. For Carmel Lab Wokwi project, comment LAB_CC_LAB and uncomment LAB_CARMEL_LAB.
// ------------------------------------------------------------------------------
#define LAB_CC_LAB
// #define LAB_CARMEL_LAB

#if defined(LAB_CARMEL_LAB)
  const char* lab_id = "carmel_lab";
  const char* telemetry_topic = "campusmg_x7k2/carmel_lab/telemetry";
  const char* cmd_topic = "campusmg_x7k2/carmel_lab/cmd";
  const float STANDBY_LOAD_W = 150.0;
#else
  const char* lab_id = "cc_lab";
  const char* telemetry_topic = "campusmg_x7k2/cc_lab/telemetry";
  const char* cmd_topic = "campusmg_x7k2/cc_lab/cmd";
  const float STANDBY_LOAD_W = 200.0;
#endif

// ------------------------------------------------------------------------------
// Network & Broker Credentials
// ------------------------------------------------------------------------------
const char* ssid = "Wokwi-GUEST";
const char* password = "";
const char* mqtt_server = "broker.hivemq.com";
const int mqtt_port = 1883;

// ------------------------------------------------------------------------------
// Hardware Pin Mappings
// ------------------------------------------------------------------------------
const int PIN_POT   = 34;  // ADC1 Channel 6 (Analog Wiper)
const int PIN_RELAY = 26;  // Relay coil control (HIGH = Closed, LOW = Open)
const int PIN_LED   = 2;   // Visual status indicator LED (Green)

// ------------------------------------------------------------------------------
// Global State Variables
// ------------------------------------------------------------------------------
WiFiClient espClient;
PubSubClient client(espClient);

bool relayClosed = true;            // true = Relay ON (Closed); false = OFF (Tripped)
unsigned long lastPublishTime = 0;
const unsigned long publishIntervalMs = 4000; // 4 seconds telemetry cadence
int lastPublishedAdc = -999;                  // Tracks last published wiper position

// ------------------------------------------------------------------------------
// WiFi Initialization
// ------------------------------------------------------------------------------
void setup_wifi() {
  delay(100);
  Serial.println();
  Serial.print("[WiFi] Connecting to SSID: ");
  Serial.println(ssid);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.print("[WiFi] Connected! IP Address: ");
    Serial.println(WiFi.localIP());
    configTime(19800, 0, "pool.ntp.org"); // IST UTC+5:30
  } else {
    Serial.println("\n[WiFi] Connection timeout. Retrying...");
  }
}

// ------------------------------------------------------------------------------
// Inbound MQTT Command Handler
// ------------------------------------------------------------------------------
void mqtt_callback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  message.trim();
  String upperMsg = message;
  upperMsg.toUpperCase();

  Serial.println("==============================================");
  Serial.print("[RCSS CMD RECEIVED] Topic: ");
  Serial.println(topic);
  Serial.print("[RCSS CMD RECEIVED] Payload: '");
  Serial.print(message);
  Serial.println("'");

  if (upperMsg.indexOf("SHUTDOWN") >= 0 || upperMsg.indexOf("OFF") >= 0 || upperMsg.indexOf("TRIP") >= 0) {
    relayClosed = false;
    digitalWrite(PIN_RELAY, LOW); // De-energize relay (Open contactor)
    digitalWrite(PIN_LED, LOW);   // Turn OFF green status LED
    Serial.println(">>> ACTUATION EXECUTED: Relay OPENED (SHUTDOWN). Standby load clamped. LED OFF.");
  } else if (upperMsg.indexOf("RESTORE") >= 0 || upperMsg.indexOf("ON") >= 0 || upperMsg.indexOf("RESET") >= 0) {
    relayClosed = true;
    digitalWrite(PIN_RELAY, HIGH); // Energize relay (Closed contactor)
    digitalWrite(PIN_LED, HIGH);  // Turn ON green status LED
    Serial.println(">>> ACTUATION EXECUTED: Relay CLOSED (RESTORED). Normal operation resumed. LED ON.");
  } else {
    Serial.println("[CMD WARNING] Unrecognized command string.");
  }
  Serial.println("==============================================");
}

// ------------------------------------------------------------------------------
// MQTT Connection Management & Reconnect Logic
// ------------------------------------------------------------------------------
void reconnect_mqtt() {
  while (!client.connected()) {
    Serial.print("[MQTT] Connecting to HiveMQ broker...");
    String clientId = "RCSS_ESP32_" + String(lab_id) + "_" + String(random(1000, 9999));

    if (client.connect(clientId.c_str())) {
      Serial.println(" CONNECTED!");
      client.subscribe(cmd_topic, 1);
      Serial.print("[MQTT] Subscribed (QoS 1) to command topic: ");
      Serial.println(cmd_topic);
    } else {
      Serial.print(" Failed (rc=");
      Serial.print(client.state());
      Serial.println("). Retrying in 3 seconds...");
      delay(3000);
    }
  }
}

// ------------------------------------------------------------------------------
// Main Setup Routine
// ------------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println("\n==============================================");
  Serial.println("  RCSS Microgrid ESP32 Edge Firmware Started  ");
  Serial.println("  Institution: RCSS Kalamassery               ");
  Serial.println("  Engineering Team: Team PIXEL                ");
  Serial.print("  Target Lab: ");
  Serial.println(lab_id);
  Serial.println("==============================================");

  // Configure GPIO Pins
  pinMode(PIN_POT, INPUT);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_LED, OUTPUT);

  // Initial State: Energized (Active ON)
  digitalWrite(PIN_RELAY, HIGH);
  digitalWrite(PIN_LED, HIGH);

  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(mqtt_callback);
  client.setBufferSize(512);
  client.setKeepAlive(30);
}

// ------------------------------------------------------------------------------
// Main Execution Loop
// ------------------------------------------------------------------------------
void loop() {
  if (!client.connected()) {
    reconnect_mqtt();
  }
  client.loop();

  int curAdc = analogRead(PIN_POT);

  // Instant Auto-Restore on Meter Demand:
  // If the circuit is shut down/tripped, but the meter is turned to high (ADC > 600, ~500W+ load),
  // instantly re-energize the relay, turn the light ON, and notify the broker/dashboard!
  if (!relayClosed && curAdc > 600) {
    relayClosed = true;
    digitalWrite(PIN_RELAY, HIGH);
    digitalWrite(PIN_LED, HIGH);
    client.publish(cmd_topic, "RESTORE", true);
    Serial.println("==============================================");
    Serial.println(">>> DEMAND DETECTED: Potentiometer raised to HIGH!");
    Serial.println(">>> AUTO-RESTORED: Relay CLOSED & Light turned ON!");
    Serial.println("==============================================");
  }

  // Continuously maintain physical actuator states in hardware
  digitalWrite(PIN_RELAY, relayClosed ? HIGH : LOW);
  digitalWrite(PIN_LED, relayClosed ? HIGH : LOW);

  unsigned long currentMillis = millis();

  // Responsive Event-Driven Telemetry (Delta Trigger):
  // Publishes immediately when the potentiometer dial is turned (>120 ADC change)
  // or periodically every 4 seconds. Eliminates wait time between dial turn & dashboard countdown!
  bool dialMoved = (abs(curAdc - lastPublishedAdc) > 120);
  bool canPublishDelta = (dialMoved && (currentMillis - lastPublishTime >= 600));
  bool periodicTimeout = (currentMillis - lastPublishTime >= publishIntervalMs);

  if (periodicTimeout || canPublishDelta) {
    lastPublishTime = currentMillis;
    lastPublishedAdc = curAdc;

    // 1. Read Analog Potentiometer & Electrical Parameters
    int adcValue = curAdc;
    float voltage = 230.0 + ((float)random(-15, 15) / 10.0);
    float current = 0.0;
    float power_w = 0.0;

    if (relayClosed) {
      // Linear mapping: ADC (0 to 4095) -> Current (0.2A to 15.0A)
      // When dial is turned counter-clockwise near 0: Power < 100W (triggers idle countdown!)
      current = ((float)adcValue / 4095.0) * 14.8 + 0.2;
      power_w = voltage * current;
    } else {
      // Circuit tripped: Draw only clamped standby baseline
      current = round((STANDBY_LOAD_W / voltage) * 100.0) / 100.0;
      power_w = STANDBY_LOAD_W + ((float)random(-3, 3));
    }

    // 3. Obtain current timestamp (via NTP if available)
    time_t nowSec = time(nullptr);
    struct tm timeinfo;
    char timeBuffer[25];
    if (nowSec > 100000 && localtime_r(&nowSec, &timeinfo)) {
      strftime(timeBuffer, sizeof(timeBuffer), "%Y-%m-%dT%H:%M:%S", &timeinfo);
    } else {
      snprintf(timeBuffer, sizeof(timeBuffer), "2026-10-08T%02lu:%02lu:%02lu", (millis()/3600000)%24, (millis()/60000)%60, (millis()/1000)%60);
    }

    // 4. Serialize JSON Telemetry Packet
    StaticJsonDocument<256> doc;
    doc["lab_id"] = lab_id;
    doc["voltage"] = round(voltage * 10.0) / 10.0;
    doc["current"] = round(current * 100.0) / 100.0;
    doc["power_w"] = round(power_w * 10.0) / 10.0;
    doc["relay_state"] = relayClosed ? "ON" : "OFF";
    doc["timestamp"] = timeBuffer;

    char payloadBuffer[256];
    serializeJson(doc, payloadBuffer);

    // 5. Publish to MQTT Broker
    boolean pubOk = client.publish(telemetry_topic, payloadBuffer);
    if (pubOk) {
      Serial.print("[RCSS TELEMETRY PUB] -> ");
      Serial.println(payloadBuffer);
    } else {
      Serial.println("[RCSS TELEMETRY PUB] ERROR: Failed to publish message.");
    }
  }
}
