"""
Airflow DAG for end-to-end weather forecasting data pipeline.

Stages:
1) Extract hourly forecast data from WeatherAPI.
2) Run strict data quality checks; fail fast on schema/null violations.
3) Transform into supervised learning dataset with lags and 4–6h targets.
4) Generate profiling report and log it to MLflow (e.g., Dagshub).
5) Persist processed data to object storage (MinIO/S3/Azure-compat via boto3).
6) Track dataset with DVC and push to the configured remote.

Environment / Airflow Variables:
- WEATHER_API_KEY (required)    : WeatherAPI key (prefer Airflow Variable).
- WEATHER_CITY (optional)       : City name/query; default "London".
- OBJECT_STORE_BUCKET (optional): Bucket name for processed data.
- OBJECT_STORE_ENDPOINT (opt.)  : S3/MinIO endpoint URL.
- OBJECT_STORE_ACCESS_KEY/OBJECT_STORE_SECRET_KEY (opt.) : Credentials.
- OBJECT_STORE_REGION (opt.)    : Region for S3-compatible storage.
- MLFLOW_TRACKING_URI (optional): MLflow tracking URI (e.g., Dagshub).
- MLFLOW_TRACKING_USERNAME / MLFLOW_TRACKING_PASSWORD or MLFLOW_TRACKING_TOKEN.
- DVC_REMOTE (optional)         : Named DVC remote to push to.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import pendulum
import requests
from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException
from airflow.models import Variable

# Ensure project root is importable when Airflow runs from the dags/ folder
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

import train  # noqa: E402

RAW_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
REPORTS_DIR = BASE_DIR / "reports"

for path in (RAW_DIR, PROCESSED_DIR, REPORTS_DIR):
    path.mkdir(parents=True, exist_ok=True)


def _get_api_key() -> str:
    api_key = os.getenv("WEATHER_API_KEY") or Variable.get("WEATHER_API_KEY", default_var=None)
    if not api_key:
        raise AirflowFailException("WEATHER_API_KEY is not set in env or Airflow Variables.")
    return api_key


def _get_city() -> str:
    return os.getenv("WEATHER_CITY") or Variable.get("WEATHER_CITY", default_var="London")


def _quality_gate(df: pd.DataFrame, required_cols: Dict[str, type], null_threshold: float = 0.01) -> None:
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        raise AirflowFailException(f"Missing required columns: {missing_cols}")

    null_ratio = df[required_cols.keys()].isnull().mean()
    if (null_ratio > null_threshold).any():
        raise AirflowFailException(f"Null ratio check failed: {null_ratio.to_dict()}")

    for col, expected_type in required_cols.items():
        if not pd.api.types.is_numeric_dtype(df[col]) and expected_type in (int, float):
            raise AirflowFailException(f"Column {col} expected numeric type.")


def _build_storage_client():
    """Builds a boto3 client for S3/MinIO/Azure-compatible endpoints."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise AirflowFailException("boto3 is required for object storage upload.") from exc

    endpoint = os.getenv("OBJECT_STORE_ENDPOINT")
    access_key = os.getenv("OBJECT_STORE_ACCESS_KEY")
    secret_key = os.getenv("OBJECT_STORE_SECRET_KEY")
    region = os.getenv("OBJECT_STORE_REGION", "us-east-1")
    use_ssl = os.getenv("OBJECT_STORE_USE_SSL", "true").lower() == "true"

    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        use_ssl=use_ssl,
        config=Config(signature_version="s3v4"),
    )


def _upload_to_object_store(local_path: Path, remote_prefix: str) -> Optional[str]:
    bucket = os.getenv("OBJECT_STORE_BUCKET")
    if not bucket:
        logging.info("OBJECT_STORE_BUCKET not set; skipping object storage upload.")
        return None

    client = _build_storage_client()
    key = f"{remote_prefix}/{local_path.name}"
    client.upload_file(str(local_path), bucket, key)
    uri = f"s3://{bucket}/{key}"
    logging.info("Uploaded %s to %s", local_path, uri)
    return uri


def _log_profile_to_mlflow(report_path: Path, city: str, run_name: str) -> None:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        logging.info("MLFLOW_TRACKING_URI not set; skipping MLflow artifact logging.")
        return

    try:
        import mlflow
    except ImportError as exc:
        raise AirflowFailException("mlflow is required to log profiling artifacts.") from exc

    mlflow.set_tracking_uri(tracking_uri)
    username = os.getenv("MLFLOW_TRACKING_USERNAME")
    password = os.getenv("MLFLOW_TRACKING_PASSWORD")
    token = os.getenv("MLFLOW_TRACKING_TOKEN")
    if username and password:
        os.environ["MLFLOW_TRACKING_USERNAME"] = username
        os.environ["MLFLOW_TRACKING_PASSWORD"] = password
    if token:
        os.environ["MLFLOW_TRACKING_TOKEN"] = token

    with mlflow.start_run(run_name=run_name):
        mlflow.log_param("city", city)
        mlflow.log_artifact(str(report_path), artifact_path="profiling")


def _dvc_add_and_push(file_path: Path) -> Path:
    dvc_bin = os.getenv("DVC_BIN", "dvc")
    try:
        subprocess.run([dvc_bin, "add", str(file_path)], cwd=BASE_DIR, check=True)
    except FileNotFoundError as exc:
        raise AirflowFailException("dvc CLI not found; install DVC and/or set DVC_BIN.") from exc
    except subprocess.CalledProcessError as exc:
        raise AirflowFailException(f"dvc add failed: {exc}") from exc

    remote = os.getenv("DVC_REMOTE")
    push_cmd = [dvc_bin, "push"]
    if remote:
        push_cmd.extend(["-r", remote])

    try:
        subprocess.run(push_cmd, cwd=BASE_DIR, check=True)
    except subprocess.CalledProcessError as exc:
        raise AirflowFailException(f"dvc push failed: {exc}") from exc

    dvc_file = file_path.with_suffix(file_path.suffix + ".dvc")
    logging.info("Tracked dataset with DVC: %s", dvc_file)
    return dvc_file


@dag(
    schedule="@daily",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    catchup=False,
    default_args={"owner": "mlops", "retries": 1, "retry_delay": pendulum.duration(minutes=5)},
    tags=["weather", "rps", "dvc", "mlflow"],
)
def weather_rps_pipeline():
    @task
    def extract(**context) -> str:
        api_key = _get_api_key()
        city = _get_city()
        url = (
            "https://api.weatherapi.com/v1/forecast.json"
            f"?key={api_key}&q={city}&days=2&aqi=no&alerts=no"
        )
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        payload = response.json()

        collected_at = datetime.utcnow().isoformat()
        raw = {
            "collected_at_utc": collected_at,
            "city": city,
            "source_url": url,
            "payload": payload,
        }

        raw_path = RAW_DIR / f"weather_raw_{context['ds_nodash']}.json"
        raw_path.write_text(json.dumps(raw, indent=2))
        logging.info("Saved raw payload to %s", raw_path)
        return str(raw_path)

    @task
    def quality_check(raw_path: str, **context) -> str:
        with open(raw_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        city = raw.get("city", "unknown")
        forecast = raw.get("payload", {}).get("forecast", {}).get("forecastday", [])
        hours = []
        for day in forecast:
            hours.extend(day.get("hour", []))

        if not hours:
            raise AirflowFailException("No hourly forecast records found; failing DAG.")

        df = pd.DataFrame(hours)
        df["city"] = city
        df["collected_at_utc"] = raw.get("collected_at_utc")

        required_cols = {
            "time_epoch": int,
            "temp_c": float,
            "humidity": float,
            "wind_kph": float,
        }
        _quality_gate(df, required_cols)

        validated_path = RAW_DIR / f"weather_validated_{context['ds_nodash']}.parquet"
        df.to_parquet(validated_path, index=False)
        logging.info("Validated data saved to %s", validated_path)
        return str(validated_path)

    @task
    def transform(validated_path: str, **context) -> str:
        df = pd.read_parquet(validated_path)
        df["timestamp"] = pd.to_datetime(df["time_epoch"], unit="s", utc=True)
        df = df.sort_values("timestamp")

        # Time-based encodings and supervised targets
        df["hour"] = df["timestamp"].dt.hour
        df["dayofweek"] = df["timestamp"].dt.dayofweek
        df["is_weekend"] = df["dayofweek"] >= 5

        df["lag_temp_1h"] = df["temp_c"].shift(1)
        df["lag_temp_2h"] = df["temp_c"].shift(2)
        df["rolling_temp_3h"] = df["temp_c"].rolling(window=3, min_periods=1).mean()
        df["lag_humidity_1h"] = df["humidity"].shift(1)

        df["temp_c_t_plus_4h"] = df["temp_c"].shift(-4)
        df["temp_c_t_plus_6h"] = df["temp_c"].shift(-6)
        df = df.dropna(subset=["temp_c_t_plus_4h", "temp_c_t_plus_6h"])

        processed_path = PROCESSED_DIR / f"weather_processed_{context['ds_nodash']}.parquet"
        df.to_parquet(processed_path, index=False)
        logging.info("Processed dataset saved to %s", processed_path)
        return str(processed_path)

    @task
    def profile(processed_path: str, **context) -> Optional[str]:
        try:
            from ydata_profiling import ProfileReport
        except ImportError:
            logging.warning("ydata-profiling not installed; skipping profiling step.")
            return None

        df = pd.read_parquet(processed_path)
        report = ProfileReport(df, title="Weather Dataset Profile", minimal=True)
        report_path = REPORTS_DIR / f"profile_{context['ds_nodash']}.html"
        report.to_file(report_path)
        logging.info("Data profiling report saved to %s", report_path)

        _log_profile_to_mlflow(report_path, _get_city(), run_name=f"profiling_{context['ds_nodash']}")
        return str(report_path)

    @task
    def upload(processed_path: str, **context) -> Optional[str]:
        return _upload_to_object_store(Path(processed_path), remote_prefix="weather/processed")

    @task
    def dvc_track(processed_path: str, **context) -> str:
        dvc_file = _dvc_add_and_push(Path(processed_path))
        return str(dvc_file)

    raw = extract()
    validated = quality_check(raw)
    processed = transform(validated)
    profile_report = profile(processed)
    uploaded_uri = upload(processed)
    dvc_artifact = dvc_track(processed)

    # Ensure task ordering
    profile_report.set_upstream(processed)
    uploaded_uri.set_upstream(processed)
    dvc_artifact.set_upstream(processed)

    @task
    def train_task(processed_path: str, **context) -> Optional[str]:
        tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
        run_name = f"train_{context['ds_nodash']}"
        run_id = train.train_and_log_model(
            processed_path=processed_path, tracking_uri=tracking_uri, run_name=run_name
        )
        logging.info("Logged MLflow run_id=%s", run_id)
        return run_id

    mlflow_run = train_task(processed)
    mlflow_run.set_upstream(processed)


weather_rps_dag = weather_rps_pipeline()
