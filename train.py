"""
Simple training script used by the Airflow DAG.

- Loads the processed parquet from the ETL pipeline.
- Trains a basic regression model (RandomForestRegressor) to predict 4h-ahead temperature.
- Logs params, metrics, and model artifact to MLflow (Dagshub remote if configured).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

def train_and_log_model(
    processed_path: str,
    tracking_uri: Optional[str] = None,
    run_name: str = "weather_rf_4h",
    return_metrics: bool = False,
) -> str | tuple:
    """
    Train a simple RandomForestRegressor on processed data and log to MLflow.

    Args:
        processed_path: Path to processed parquet produced by the DAG.
        tracking_uri: Optional MLflow tracking URI (e.g., Dagshub). If None, uses env/MLflow default.
        run_name: Name for the MLflow run.

    Returns:
        The MLflow run_id.
    """
    import pandas as pd
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import train_test_split
    import mlflow

    df = pd.read_parquet(processed_path)
    target = "temp_c_t_plus_4h"
    # Keep only numeric feature columns to avoid datetime/object conversion errors.
    numeric_cols = [
        c
        for c in df.columns
        if c not in {target, "temp_c_t_plus_6h"}
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    X = df[numeric_cols]
    y = df[target]

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    params = {
        "n_estimators": 200,
        "max_depth": 12,
        "random_state": 42,
        "n_jobs": -1,
    }

    model = RandomForestRegressor(**params)
    model.fit(X_train, y_train)

    preds = model.predict(X_val)
    try:
        rmse = mean_squared_error(y_val, preds, squared=False)
    except TypeError:
        # Fallback for older sklearn without the `squared` param.
        rmse = mean_squared_error(y_val, preds) ** 0.5
    mae = mean_absolute_error(y_val, preds)
    r2 = r2_score(y_val, preds)

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)

    metrics = {"rmse": float(rmse), "mae": float(mae), "r2": float(r2)}

    run = mlflow.start_run(run_name=run_name)
    with run:
        mlflow.log_params(params)
        for k, v in metrics.items():
            mlflow.log_metric(k, v)
        mlflow.log_param("processed_path", processed_path)
        mlflow.log_param("features", ",".join(numeric_cols))
        # Dagshub's MLflow endpoint may not support the new "logged model" API.
        # Save locally and log as a plain artifact instead of mlflow.sklearn.log_model.
        from tempfile import TemporaryDirectory
        import joblib

        with TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.pkl"
            joblib.dump(model, model_path)
            mlflow.log_artifact(str(model_path), artifact_path="model")
    return (run.info.run_id, metrics) if return_metrics else run.info.run_id


if __name__ == "__main__":
    path = os.getenv("PROCESSED_PATH")
    if not path:
        raise SystemExit("PROCESSED_PATH env var required.")
    uri = os.getenv("MLFLOW_TRACKING_URI")
    run_id = train_and_log_model(path, tracking_uri=uri)
    print(f"Completed run: {run_id}")
