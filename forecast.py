"""
Campus Micro-Grid & Energy Optimization System (CM-EOS)
Forecast Module: ML time-series feature engineering, GBR training, and 6-hour forward predictions.
"""

import os
import sqlite3
import datetime
import joblib
import pandas as pd
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
import config

def get_db():
    conn = sqlite3.connect(config.DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def load_historical_series():
    """Loads all readings from SQLite and parses timestamps."""
    conn = get_db()
    df = pd.read_sql_query("SELECT lab_id, ts, power_w FROM readings ORDER BY ts ASC", conn)
    conn.close()

    if df.empty:
        raise ValueError("No readings found in database. Run backfill.py first.")

    df["ts"] = pd.to_datetime(df["ts"])
    return df

def build_features_for_series(hourly_series: pd.Series) -> pd.DataFrame:
    """Builds temporal, lag, and rolling window features from an hourly power series."""
    df = pd.DataFrame({"power": hourly_series})

    # Temporal / Calendar features
    df["hour"] = df.index.hour
    df["dayofweek"] = df.index.dayofweek
    df["is_weekend"] = df["dayofweek"].isin([5, 6]).astype(int)

    # Autoregressive lag features
    df["lag_1h"] = df["power"].shift(1)
    df["lag_2h"] = df["power"].shift(2)
    df["lag_24h"] = df["power"].shift(24)

    # Rolling statistics (using shift to prevent lookahead leakage)
    df["rolling_mean_3h"] = df["power"].shift(1).rolling(window=3, min_periods=1).mean()
    df["rolling_mean_24h"] = df["power"].shift(1).rolling(window=24, min_periods=1).mean()

    # Target: power at next hour (t+1)
    df["target"] = df["power"].shift(-1)

    return df.dropna()

def train_and_forecast():
    """Trains GradientBoostingRegressor models per lab and generates recursive 6-hour forecasts."""
    print("[forecast] Loading historical time-series from SQLite...")
    raw_df = load_historical_series()

    conn = get_db()
    cur = conn.cursor()

    # Clear old forecasts
    cur.execute("DELETE FROM forecasts")
    conn.commit()

    feature_cols = [
        "hour", "dayofweek", "is_weekend",
        "lag_1h", "lag_2h", "lag_24h",
        "rolling_mean_3h", "rolling_mean_24h"
    ]

    trained_at_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    campus_hourly_dict = {}

    all_labs = list(config.LABS.keys())
    print(f"[forecast] Training models across {len(all_labs)} lab facilities...")

    for lab_id in all_labs:
        lab_slice = raw_df[raw_df["lab_id"] == lab_id].copy()
        if len(lab_slice) < 50:
            print(f"[forecast] Warning: insufficient data for {lab_id}, skipping.")
            continue

        # Resample to 1-hour intervals with linear interpolation for gaps
        hourly_series = lab_slice.set_index("ts")["power_w"].resample("1h").mean().interpolate(method="time")
        campus_hourly_dict[lab_id] = hourly_series

        feat_df = build_features_for_series(hourly_series)
        X = feat_df[feature_cols]
        y = feat_df["target"]

        # Chronological 80/20 train/test split (no shuffling)
        split_point = int(len(X) * 0.80)
        X_train, X_test = X.iloc[:split_point], X.iloc[split_point:]
        y_train, y_test = y.iloc[:split_point], y.iloc[split_point:]

        # Fit GradientBoostingRegressor
        gbr = GradientBoostingRegressor(
            n_estimators=120,
            max_depth=4,
            learning_rate=0.08,
            random_state=42
        )
        gbr.fit(X_train, y_train)

        # Evaluate on test set
        preds = gbr.predict(X_test)
        mae = float(mean_absolute_error(y_test, preds))
        r2 = float(r2_score(y_test, preds))

        # Baseline: same hour yesterday (lag_24h)
        base_preds = X_test["lag_24h"]
        base_mae = float(mean_absolute_error(y_test, base_preds))

        # Persist metrics to database
        cur.execute("""
            INSERT INTO model_metrics (trained_at, scope, mae, r2, baseline_mae)
            VALUES (?, ?, ?, ?, ?)
        """, (trained_at_str, lab_id, round(mae, 2), round(r2, 4), round(base_mae, 2)))

        # Save model artifact
        model_path = os.path.join(config.MODELS_DIR, f"{lab_id}_gbr.joblib")
        joblib.dump(gbr, model_path)

        # ---------------------------------------------------------
        # Recursive 6-Hour Forward Forecast
        # ---------------------------------------------------------
        last_row = feat_df.iloc[-1].copy()
        latest_ts = feat_df.index[-1]

        # Tracking state for recursive updates
        curr_power = float(feat_df["power"].iloc[-1])
        curr_lag_1h = float(last_row["lag_1h"])
        curr_lag_2h = float(last_row["lag_2h"])
        curr_lag_24h = float(last_row["lag_24h"])
        rolling_buffer_3h = list(feat_df["power"].tail(3).values)
        rolling_buffer_24h = list(feat_df["power"].tail(24).values)

        for step in range(1, config.FORECAST_HORIZON_H + 1):
            future_ts = latest_ts + datetime.timedelta(hours=step)
            f_hour = future_ts.hour
            f_dow = future_ts.weekday()
            f_weekend = 1 if f_dow in [5, 6] else 0

            rm_3h = float(np.mean(rolling_buffer_3h[-3:]))
            rm_24h = float(np.mean(rolling_buffer_24h[-24:]))

            step_feat = pd.DataFrame([{
                "hour": f_hour,
                "dayofweek": f_dow,
                "is_weekend": f_weekend,
                "lag_1h": curr_power,
                "lag_2h": curr_lag_1h,
                "lag_24h": curr_lag_24h,
                "rolling_mean_3h": rm_3h,
                "rolling_mean_24h": rm_24h
            }])[feature_cols]

            predicted_w = float(gbr.predict(step_feat)[0])
            predicted_w = max(config.LABS[lab_id]["standby_w"], predicted_w)

            cur.execute("""
                INSERT INTO forecasts (lab_id, generated_at, target_ts, predicted_w)
                VALUES (?, ?, ?, ?)
            """, (
                lab_id,
                trained_at_str,
                future_ts.strftime("%Y-%m-%dT%H:%M:%S"),
                round(predicted_w, 2)
            ))

            # Shift lag variables for next recursive step
            curr_lag_2h = curr_lag_1h
            curr_lag_1h = curr_power
            curr_power = predicted_w
            rolling_buffer_3h.append(predicted_w)
            rolling_buffer_24h.append(predicted_w)

        print(f"[{lab_id}] GBR Trained: MAE={mae:.1f}W | R²={r2:.3f} | Baseline MAE={base_mae:.1f}W")

    conn.commit()
    conn.close()
    print("[forecast] Successfully computed and stored 6-hour forward predictions.")

if __name__ == "__main__":
    train_and_forecast()
