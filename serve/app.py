from __future__ import annotations

import os
import tempfile
from typing import List

import mlflow
import joblib
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class PredictRequest(BaseModel):
    features: List[float]


@app.get("/health")
def health():
    return {"status": "ok"}


def _load_model():
    """
    Load model from MLflow. MODEL_URI can be a registry URI (e.g., models:/weather_rf_4h/Production)
    or a run artifact URI. Uses a temp dir to avoid cache collisions in CI.
    """
    model_uri = os.getenv("MODEL_URI", "models:/weather_rf_4h/Production")
    with tempfile.TemporaryDirectory() as tmp:
        local_path = mlflow.artifacts.download_artifacts(model_uri=model_uri, dst_path=tmp)
        # Expect model.pkl under model/ (matches train.py logging)
        candidate = os.path.join(local_path, "model", "model.pkl")
        return joblib.load(candidate)


@app.post("/predict")
def predict(req: PredictRequest):
    model = _load_model()
    preds = model.predict([req.features])
    return {"prediction": float(preds[0])}
