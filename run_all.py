"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Master Process Orchestrator: Initializes DB/ML, spawns all subsystems, and handles clean teardown.
"""

import os
import sys
import time
import subprocess
import signal
import config

def verify_and_initialize_system():
    """Checks for required data and trained models; executes cold-start scripts if needed."""
    print("=" * 70)
    print("  CAMPUS MICRO-GRID & ENERGY OPTIMIZATION SYSTEM (CM-EOS)")
    print("=" * 70)

    # 1. Check SQLite Database
    if not os.path.exists(config.DB_PATH):
        print("[init] Database not detected. Running 30-day historical backfill...")
        from backfill import run_backfill
        run_backfill(days=30)
    else:
        print("[init] SQLite database verified.")

    # 2. Check Trained Models
    sample_model = os.path.join(config.MODELS_DIR, "cs_lab_1_gbr.joblib")
    if not os.path.exists(sample_model):
        print("[init] Machine learning models missing. Training initial pipeline...")
        from forecast import train_and_forecast
        train_and_forecast()
    else:
        print("[init] ML models verified.")

def main():
    verify_and_initialize_system()

    processes = []

    def signal_handler(sig, frame):
        print("\n" + "=" * 70)
        print("[shutdown] Interrupt signal received (Ctrl+C). Terminating all subsystems...")
        for p in processes:
            try:
                p.terminate()
            except Exception:
                pass
        for p in processes:
            try:
                p.wait(timeout=2)
            except Exception:
                p.kill()
        print("[shutdown] All services stopped cleanly. Goodbye!")
        print("=" * 70)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        # 1. Spawning Unified CM-EOS Core & Flask Dashboard
        print("[launch] Spawning Unified CM-EOS Core & Modern Web Dashboard (app.py)...")
        p_app = subprocess.Popen([sys.executable, "app.py"], cwd=config.BASE_DIR)
        processes.append(p_app)
        time.sleep(2)

        # 2. Streamlit Dashboard (Parallel Access)
        print("[launch] Spawning Streamlit Dashboard on port 8501 (dashboard.py)...")
        p_dash = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "dashboard.py", "--server.headless", "true", "--server.port", "8501"],
            cwd=config.BASE_DIR
        )
        processes.append(p_dash)

        print("\n" + "=" * 70)
        print("  RCSS CAMPUS MICRO-GRID SYSTEM ONLINE AND HEALTHY!")
        print(f"  * Primary Modern Web Dashboard: http://localhost:5000")
        print(f"  * Secondary Streamlit View:     http://localhost:8501")
        print(f"  * HiveMQ MQTT Broker:          {config.MQTT_HOST}:{config.MQTT_PORT}")
        print(f"  * MQTT Namespace:              {config.TOPIC_PREFIX}")
        print("  Press Ctrl+C in this terminal window to stop all services.")
        print("=" * 70 + "\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == "__main__":
    main()
