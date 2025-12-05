from __future__ import annotations

import os
import time
import tempfile
from typing import List
import numpy as np

import mlflow
import joblib
from fastapi import FastAPI, Request
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

app = FastAPI(title="Weather Prediction API", version="1.0.0")

# Prometheus Metrics
# Service metrics
REQUEST_COUNT = Counter(
    "prediction_requests_total",
    "Total number of prediction requests",
    ["endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "prediction_latency_seconds",
    "Prediction request latency in seconds",
    ["endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

# Model/Data drift metrics
DRIFT_RATIO = Gauge(
    "data_drift_ratio",
    "Ratio of prediction requests with out-of-distribution feature values"
)

OUT_OF_RANGE_FEATURES = Counter(
    "out_of_range_features_total",
    "Total number of features detected outside expected range"
)

MODEL_LOAD_TIME = Gauge(
    "model_load_time_seconds",
    "Time taken to load the model"
)

# Expected feature ranges (based on weather data)
# These would be calibrated from training data in production
FEATURE_RANGES = {
    "time_epoch": (1700000000, 1800000000),
    "temp_c": (-50, 60),
    "humidity": (0, 100),
    "wind_kph": (0, 200),
    "hour": (0, 23),
    "dayofweek": (0, 6),
    "is_weekend": (0, 1),
    "lag_temp_1h": (-50, 60),
    "lag_temp_2h": (-50, 60),
    "rolling_temp_3h": (-50, 60),
    "lag_humidity_1h": (0, 100),
}

# Model cache to avoid reloading on every request
_model_cache = None
_model_load_timestamp = None


class PredictRequest(BaseModel):
    features: List[float]


def detect_drift(features: List[float]) -> tuple[bool, int]:
    """
    Detect if features are out of expected distribution.

    Returns:
        (is_drift_detected, num_out_of_range_features)
    """
    out_of_range = 0
    feature_names = list(FEATURE_RANGES.keys())

    for idx, value in enumerate(features):
        if idx < len(feature_names):
            min_val, max_val = FEATURE_RANGES[feature_names[idx]]
            if value < min_val or value > max_val:
                out_of_range += 1

    drift_detected = out_of_range > 0
    return drift_detected, out_of_range


def _load_model():
    """
    Load model from MLflow with caching and timing.
    MODEL_URI can be a registry URI (e.g., models:/weather_rf_4h/Production)
    or a run artifact URI.
    """
    global _model_cache, _model_load_timestamp

    # Use cached model if available and less than 5 minutes old
    if _model_cache is not None and _model_load_timestamp is not None:
        if time.time() - _model_load_timestamp < 300:  # 5 minutes
            return _model_cache

    start_time = time.time()
    model_uri = os.getenv("MODEL_URI", "models:/weather_rf_4h/Production")

    with tempfile.TemporaryDirectory() as tmp:
        local_path = mlflow.artifacts.download_artifacts(model_uri=model_uri, dst_path=tmp)
        # Expect model.pkl under model/ (matches train.py logging)
        candidate = os.path.join(local_path, "model", "model.pkl")
        model = joblib.load(candidate)

    load_time = time.time() - start_time
    MODEL_LOAD_TIME.set(load_time)

    _model_cache = model
    _model_load_timestamp = time.time()

    return model


@app.get("/health")
def health():
    """Health check endpoint."""
    REQUEST_COUNT.labels(endpoint="/health", status="success").inc()
    return {"status": "ok", "service": "weather-prediction-api"}


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/predict")
def predict(req: PredictRequest):
    """
    Prediction endpoint with monitoring.

    Tracks:
    - Request count
    - Latency
    - Data drift (out-of-distribution features)
    """
    start_time = time.time()

    try:
        # Load model
        model = _load_model()

        # Detect drift
        drift_detected, num_out_of_range = detect_drift(req.features)
        if drift_detected:
            OUT_OF_RANGE_FEATURES.inc(num_out_of_range)

        # Make prediction
        preds = model.predict([req.features])

        # Update metrics
        latency = time.time() - start_time
        REQUEST_LATENCY.labels(endpoint="/predict").observe(latency)
        REQUEST_COUNT.labels(endpoint="/predict", status="success").inc()

        # Update drift ratio (exponential moving average)
        # This is a simplified version - in production you'd use a sliding window
        if drift_detected:
            current_ratio = DRIFT_RATIO._value.get() if hasattr(DRIFT_RATIO._value, 'get') else 0
            new_ratio = 0.9 * current_ratio + 0.1 * 1.0  # EMA with alpha=0.1
            DRIFT_RATIO.set(new_ratio)
        else:
            current_ratio = DRIFT_RATIO._value.get() if hasattr(DRIFT_RATIO._value, 'get') else 0
            new_ratio = 0.9 * current_ratio  # Decay
            DRIFT_RATIO.set(new_ratio)

        return {
            "prediction": float(preds[0]),
            "latency_ms": round(latency * 1000, 2),
            "drift_detected": drift_detected,
            "model_uri": os.getenv("MODEL_URI", "models:/weather_rf_4h/Production")
        }

    except Exception as e:
        latency = time.time() - start_time
        REQUEST_LATENCY.labels(endpoint="/predict").observe(latency)
        REQUEST_COUNT.labels(endpoint="/predict", status="error").inc()
        raise


@app.get("/")
def root():
    """Root endpoint with API information."""
    return {
        "service": "Weather Prediction API",
        "version": "1.0.0",
        "endpoints": {
            "/health": "Health check",
            "/predict": "Make temperature predictions (POST)",
            "/metrics": "Prometheus metrics",
            "/docs": "OpenAPI documentation"
        }
    }
