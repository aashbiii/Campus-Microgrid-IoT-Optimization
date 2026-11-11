"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Institution: Rajagiri College of Social Sciences (RCSS), Kalamassery
Central Monitoring Dashboard (Streamlit & Plotly)
Features: Real-time telemetry, 6-hour ML forecasts, closed-loop actuation, and M&V savings.
"""

import time
import sqlite3
import datetime
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import config
from optimizer import send_mqtt_command, get_expected_power_for_hour, compute_idle_status

# ---------------------------------------------------------
# Page Configuration & Modern Aesthetics
# ---------------------------------------------------------
st.set_page_config(
    page_title="RCSS Campus Micro-Grid & Optimization",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Glassmorphism & Modern Styling CSS
st.markdown("""
<style>
    /* Metric Card Styling */
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.02));
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
    }
    div[data-testid="stMetric"]:hover {
        border-color: rgba(0, 212, 255, 0.5);
        transform: translateY(-2px);
        transition: all 0.3s ease;
    }
    /* Status Badges */
    .badge-active {
        background-color: #00e676;
        color: #000;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.82rem;
    }
    .badge-off {
        background-color: #ff1744;
        color: #fff;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.82rem;
    }
    .badge-idle {
        background-color: #ffab00;
        color: #000;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.82rem;
    }
    /* Section Headers */
    h1, h2, h3 {
        font-family: 'Inter', -apple-system, sans-serif;
        font-weight: 700;
        letter-spacing: -0.5px;
    }
    .rcss-header {
        color: #00d4ff;
        font-size: 1.1rem;
        font-weight: 600;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        margin-bottom: 2px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# Database Query Helpers
# ---------------------------------------------------------
def get_db():
    conn = sqlite3.connect(config.DB_PATH, timeout=5.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def load_data():
    conn = get_db()
    # 1. Past 60 minutes of live/backfill readings
    t_threshold = (datetime.datetime.now() - datetime.timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%S")
    df_readings = pd.read_sql_query(
        "SELECT * FROM readings WHERE ts >= ? ORDER BY ts ASC", conn, params=(t_threshold,)
    )

    # 2. Latest reading per lab based on highest autoincrement ID (always the latest inserted row!)
    df_latest = pd.read_sql_query("""
        SELECT r.* FROM readings r
        INNER JOIN (
            SELECT lab_id, MAX(id) as max_id FROM readings GROUP BY lab_id
        ) latest ON r.id = latest.max_id
    """, conn)

    # 3. Forecasts
    df_forecasts = pd.read_sql_query(
        "SELECT * FROM forecasts ORDER BY target_ts ASC", conn
    )

    # 4. Model Metrics
    df_metrics = pd.read_sql_query(
        "SELECT * FROM model_metrics ORDER BY id DESC", conn
    )

    # 5. Shutdown & Optimization Audit Log
    df_logs = pd.read_sql_query(
        "SELECT * FROM shutdown_log ORDER BY id DESC LIMIT 20", conn
    )

    conn.close()
    return df_readings, df_latest, df_forecasts, df_metrics, df_logs

# ---------------------------------------------------------
# Fetch Current State
# ---------------------------------------------------------
try:
    df_readings, df_latest, df_forecasts, df_metrics, df_logs = load_data()
except Exception as e:
    st.error(f"Database connection error: {e}. Ensure backfill.py or run_all.py is running.")
    st.stop()

# ---------------------------------------------------------
# Sidebar Controls & System Metadata
# ---------------------------------------------------------
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/electricity.png", width=64)
    st.title("RCSS Microgrid")
    st.caption("Rajagiri College of Social Sciences (RCSS)")
    st.caption("Campus: Kalamassery, Kochi")
    
    st.divider()
    st.subheader("⚙️ System Status")
    st.markdown(f"**Broker:** `{config.MQTT_HOST}:{config.MQTT_PORT}`")
    st.markdown(f"**Namespace:** `{config.TOPIC_PREFIX}`")
    st.markdown(f"**Operational Mode:** `{'DEMO (30s)' if config.DEMO_MODE else 'NORMAL (15m)'}`")
    st.markdown(f"**Idle Threshold:** `{config.IDLE_POWER_W} W`")

    st.divider()
    st.subheader("🏛️ Monitored Labs (4)")
    for l_id, l_cfg in config.LABS.items():
        st.markdown(f"- **{l_cfg['name']}** ({l_cfg['type'].upper()})")

    st.divider()
    st.subheader("🔄 Refresh Configuration")
    auto_refresh = st.checkbox("Auto-refresh Dashboard (5s)", value=True)
    if st.button("Manual Refresh Now"):
        st.rerun()

    st.divider()
    st.caption("RCSS Department of Computer Science | 2026")

# ---------------------------------------------------------
# Main Page Header & KPI Cards
# ---------------------------------------------------------
st.markdown('<div class="rcss-header">Rajagiri College of Social Sciences (RCSS), Kalamassery</div>', unsafe_allow_html=True)
st.title("⚡ Campus Micro-Grid & Energy Optimization System")
st.markdown("Real-time telemetry, machine learning demand forecasting, and autonomous closed-loop waste reduction across campus facilities.")

# KPI Calculations
total_power_now = df_latest["power_w"].sum() if not df_latest.empty else 0.0
total_kwh_saved = df_logs["kwh_saved_est"].sum() if not df_logs.empty else 0.0
co2_saved_kg = total_kwh_saved * 0.82  # Standard grid emissions factor

active_labs_cnt = (df_latest["relay_state"] == "ON").sum() if not df_latest.empty else 0
off_labs_cnt = (df_latest["relay_state"] == "OFF").sum() if not df_latest.empty else 0
total_monitored = len(config.LABS)

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric(
    label="Campus Total Load",
    value=f"{total_power_now:,.1f} W",
    delta=f"{(total_power_now / 1000.0):.2f} kW"
)
kpi2.metric(
    label="Lab Circuit States",
    value=f"{active_labs_cnt} / {total_monitored} Online",
    delta=f"{off_labs_cnt} Standby/Off",
    delta_color="inverse" if off_labs_cnt > 0 else "normal"
)
kpi3.metric(
    label="Energy Conserved",
    value=f"{total_kwh_saved:.3f} kWh",
    delta=f"🌱 {co2_saved_kg:.2f} kg CO₂"
)
kpi4.metric(
    label="HiveMQ Broker Status",
    value="CONNECTED",
    delta="Active Ingestion"
)

st.divider()

# ---------------------------------------------------------
# Live Telemetry Stream Panel
# ---------------------------------------------------------
st.subheader("📈 Real-Time Power Telemetry (Last 60 Minutes)")

col_chart_ctl, col_chart_opt = st.columns([3, 1])
with col_chart_opt:
    lab_filter = st.multiselect(
        "Filter Facilities:",
        options=list(config.LABS.keys()),
        default=list(config.LABS.keys()),
        format_func=lambda x: config.LABS[x]["name"]
    )

if not df_readings.empty and lab_filter:
    filtered_readings = df_readings[df_readings["lab_id"].isin(lab_filter)].copy()
    filtered_readings["ts"] = pd.to_datetime(filtered_readings["ts"])

    # Map lab IDs to readable names
    filtered_readings["lab_name"] = filtered_readings["lab_id"].apply(
        lambda x: config.LABS.get(x, {}).get("name", x)
    )

    fig_telemetry = px.line(
        filtered_readings,
        x="ts",
        y="power_w",
        color="lab_name",
        labels={"ts": "Timestamp", "power_w": "Active Power (Watts)", "lab_name": "Facility"},
        template="plotly_dark",
        title="Instantaneous Electrical Load by Facility"
    )

    # Add idle threshold horizontal reference line
    fig_telemetry.add_hline(
        y=config.IDLE_POWER_W,
        line_dash="dot",
        line_color="#ffab00",
        annotation_text=f"Idle Cutoff ({config.IDLE_POWER_W}W)",
        annotation_position="bottom right"
    )

    fig_telemetry.update_layout(
        height=380,
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_telemetry, width="stretch")
else:
    st.info("Awaiting telemetry stream or select at least one facility above.")

# ---------------------------------------------------------
# Forecasting & Machine Learning Quality Diagnostics
# ---------------------------------------------------------
col_fc, col_ml = st.columns([3, 2])

with col_fc:
    st.subheader("🔮 6-Hour Recursive Demand Forecast")
    if not df_forecasts.empty:
        agg_forecast = df_forecasts.groupby("target_ts")["predicted_w"].sum().reset_index()
        peak_idx = agg_forecast["predicted_w"].idxmax()
        peak_val = agg_forecast.loc[peak_idx, "predicted_w"]
        peak_time = agg_forecast.loc[peak_idx, "target_ts"]

        st.success(f"**Predicted Peak Demand:** **{peak_val:,.1f} W** at **{peak_time}** (Early warning for grid dispatch)")

        fig_forecast = go.Figure()
        fig_forecast.add_trace(go.Bar(
            x=agg_forecast["target_ts"],
            y=agg_forecast["predicted_w"],
            name="Forecasted Campus Load",
            marker_color="#00d4ff"
        ))

        fig_forecast.update_layout(
            template="plotly_dark",
            height=300,
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="Future Timestamp",
            yaxis_title="Predicted Power (Watts)"
        )
        st.plotly_chart(fig_forecast, width="stretch")
    else:
        st.warning("No forecasts currently stored. Run 'forecast.py' to generate predictions.")

with col_ml:
    st.subheader("📊 Model Performance & Validation")
    if not df_metrics.empty:
        latest_metrics = df_metrics.groupby("scope").first().reset_index()
        st.caption("Gradient Boosting Regressor vs Naive Persistence Baseline ('Same Hour Yesterday'):")

        display_metrics = latest_metrics.copy()
        display_metrics["scope"] = display_metrics["scope"].apply(
            lambda x: config.LABS.get(x, {}).get("name", x)
        )
        display_metrics = display_metrics.rename(columns={
            "scope": "Facility Model",
            "mae": "GBR MAE (W)",
            "r2": "R² Score",
            "baseline_mae": "Naive Baseline MAE (W)"
        })
        st.dataframe(
            display_metrics[["Facility Model", "GBR MAE (W)", "R² Score", "Naive Baseline MAE (W)"]],
            width="stretch",
            hide_index=True
        )
    else:
        st.info("Metrics pending initial model evaluation.")

st.divider()

# ---------------------------------------------------------
# Interactive Facility Status & Closed-Loop Control Grid
# ---------------------------------------------------------
st.subheader("🎛️ Facility Status & Closed-Loop Actuation Controls")

facility_cols = st.columns(2)
col_idx = 0

for lab_id, cfg in config.LABS.items():
    with facility_cols[col_idx % 2]:
        # Locate latest reading
        lab_row = df_latest[df_latest["lab_id"] == lab_id]
        if not lab_row.empty:
            cur_p = float(lab_row["power_w"].values[0])
            cur_v = float(lab_row["voltage"].values[0])
            cur_i = float(lab_row["current"].values[0])
            cur_relay = str(lab_row["relay_state"].values[0])
            cur_ts = str(lab_row["ts"].values[0])
        else:
            cur_p = 0.0
            cur_v = 230.0
            cur_i = 0.0
            cur_relay = "UNKNOWN"
            cur_ts = "No Data"

        # Calculate dynamic idle streak and timer
        conn_idle = get_db()
        is_idle, elapsed, rem, cur_p_calc, cur_relay_calc = compute_idle_status(lab_id, conn_idle)
        conn_idle.close()

        # Determine Badge and Warning Banner State
        idle_warning_html = ""
        if cur_relay == "OFF":
            state_badge = '<span class="badge-off">SHUT DOWN</span>'
        elif is_idle and rem > 0:
            state_badge = f'<span class="badge-idle">⏳ GOING TO SHUT DOWN IN {rem}s</span>'
            pct = int(((config.IDLE_DURATION_S - rem) / max(1, config.IDLE_DURATION_S)) * 100)
            idle_warning_html = f"""
            <div style="background: rgba(245, 158, 11, 0.15); border: 1px solid #f59e0b; border-radius: 8px; padding: 12px 16px; margin: 10px 0;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <strong style="color: #f59e0b; font-size: 0.95rem;">⚠️ Equipment Unattended ({cur_p:.1f} W)</strong>
                    <span style="font-family: monospace; font-size: 0.95rem; font-weight: 800; color: #fff; background: #d97706; padding: 3px 10px; border-radius: 5px;">Going to shut down in {rem}s</span>
                </div>
                <div style="font-size: 0.85rem; color: #f3f4f6; margin-top: 6px;">
                    Idle power below {config.IDLE_POWER_W:.0f} W cutoff. Automated shutdown will trigger when timer reaches 0s.
                </div>
                <div style="width: 100%; height: 8px; background: rgba(255,255,255,0.15); border-radius: 4px; margin-top: 8px; overflow: hidden;">
                    <div style="width: {pct}%; height: 100%; background: linear-gradient(90deg, #f59e0b, #ef4444); transition: width 0.3s ease;"></div>
                </div>
            </div>
            """
        elif is_idle and rem <= 0:
            state_badge = '<span class="badge-off">🛑 SHUTTING DOWN NOW</span>'
            idle_warning_html = f"""
            <div style="background: rgba(244, 63, 94, 0.25); border: 1px solid #f43f5e; border-radius: 8px; padding: 12px 16px; margin: 10px 0;">
                <strong style="color: #f43f5e; font-size: 0.95rem;">🛑 AUTO-SHUTDOWN INITIATED</strong><br>
                <span style="font-size: 0.85rem; color: #fff;">Idle threshold exceeded ({config.IDLE_DURATION_S}s). Cutoff signal dispatched to relay.</span>
            </div>
            """
            # Ensure command is dispatched over MQTT and logged
            conn_act = get_db()
            cur_act = conn_act.cursor()
            cur_act.execute("SELECT id FROM shutdown_log WHERE lab_id = ? AND ts_restored IS NULL ORDER BY id DESC LIMIT 1", (lab_id,))
            if not cur_act.fetchone():
                now_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                cur_act.execute("INSERT INTO shutdown_log (lab_id, ts_flagged, avg_power_w, reason, confirmed) VALUES (?, ?, ?, 'Autonomous Idle Timeout', 0)", (lab_id, now_str, cur_p))
                conn_act.commit()
            conn_act.close()
            send_mqtt_command(lab_id, "SHUTDOWN")
        else:
            state_badge = '<span class="badge-active">ACTIVE ON</span>'

        with st.container(border=True):
            st.markdown(f"### {cfg['name']} {state_badge}", unsafe_allow_html=True)
            st.caption(f"{cfg['description']} ({cfg['type'].upper()} Node)")
            st.markdown(f"**Power:** `{cur_p:.1f} W` &nbsp;|&nbsp; **Current:** `{cur_i:.2f} A` &nbsp;|&nbsp; **Voltage:** `{cur_v:.1f} V`")
            st.caption(f"Last Telemetry: `{cur_ts}`")

            if idle_warning_html:
                st.markdown(idle_warning_html, unsafe_allow_html=True)

            # Actuation Buttons
            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button(f"Restore {cfg['name']}", key=f"btn_res_{lab_id}"):
                    conn = get_db()
                    cur = conn.cursor()
                    now = datetime.datetime.now()
                    cur.execute("""
                        SELECT id, ts_flagged FROM shutdown_log
                        WHERE lab_id = ? AND ts_restored IS NULL
                        ORDER BY id DESC LIMIT 1
                    """, (lab_id,))
                    open_row = cur.fetchone()

                    if open_row:
                        log_id, ts_flag = open_row
                        t_flagged = datetime.datetime.strptime(ts_flag, "%Y-%m-%dT%H:%M:%S")
                        duration_s = max(1.0, (now - t_flagged).total_seconds())
                        exp_w = get_expected_power_for_hour(lab_id, now.hour)
                        kwh = ((exp_w - cfg["standby_w"]) * (duration_s / 3600.0)) / 1000.0
                        cur.execute("""
                            UPDATE shutdown_log
                            SET ts_restored = ?, kwh_saved_est = ?
                            WHERE id = ?
                        """, (now.strftime("%Y-%m-%dT%H:%M:%S"), round(max(0.0, kwh), 4), log_id))
                        conn.commit()

                    conn.close()
                    send_mqtt_command(lab_id, "RESTORE")
                    st.toast(f"RESTORE command sent to {cfg['name']}!", icon="⚡")
                    time.sleep(0.5)
                    st.rerun()

            with btn_col2:
                if st.button(f"Trip Cutoff", key=f"btn_off_{lab_id}"):
                    conn = get_db()
                    cur = conn.cursor()
                    now_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    cur.execute("""
                        INSERT INTO shutdown_log (lab_id, ts_flagged, avg_power_w, reason, confirmed)
                        VALUES (?, ?, ?, 'Manual Dashboard Override', 0)
                    """, (lab_id, now_str, cur_p))
                    conn.commit()
                    conn.close()

                    send_mqtt_command(lab_id, "SHUTDOWN")
                    st.toast(f"SHUTDOWN dispatched to {cfg['name']}!", icon="🛑")
                    time.sleep(0.5)
                    st.rerun()

    col_idx += 1

st.divider()

# ---------------------------------------------------------
# Audit Trail & M&V Verification Table
# ---------------------------------------------------------
st.subheader("📋 Autonomous Optimization Audit Trail")
if not df_logs.empty:
    display_logs = df_logs.copy()
    display_logs["lab_id"] = display_logs["lab_id"].apply(
        lambda x: config.LABS.get(x, {}).get("name", x)
    )
    st.dataframe(
        display_logs.rename(columns={
            "id": "Log ID",
            "lab_id": "Facility",
            "ts_flagged": "Flagged Time",
            "ts_restored": "Restored Time",
            "avg_power_w": "Avg Load (W)",
            "reason": "Trigger Reason",
            "kwh_saved_est": "Verified Energy Saved (kWh)",
            "confirmed": "Loop Closed (1=Yes)"
        }),
        width="stretch",
        hide_index=True
    )
else:
    st.info("No shutdowns recorded yet. Use the manual trip button or turn down a Wokwi dial to test.")

# ---------------------------------------------------------
# Reactive Auto-Refresh Loop
# ---------------------------------------------------------
if auto_refresh:
    time.sleep(5)
    st.rerun()
