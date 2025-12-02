# Real-Time Predictive System (Weather) - Airflow DAG

This project builds a production-style ETL + retraining dataset pipeline around WeatherAPI hourly forecasts. The DAG runs daily, ingests live data, enforces data quality, engineers time-series features for 4–6 hour temperature forecasting, generates a profiling report, pushes artifacts to object storage, and versions the processed dataset with DVC.

## Prerequisites
- Python 3.9+ with Apache Airflow 2.8+.
- WeatherAPI key (do **not** commit it).  
- DVC installed and a configured remote (e.g., MinIO/S3/Azure Blob via S3 API).  
- Optional: MLflow Tracking server (Dagshub-compatible) for profiling artifacts.

Install dependencies:
```bash
pip install -r requirements.txt
```

## Configuration (env or Airflow Variables)
- `WEATHER_API_KEY` (required): WeatherAPI key (e.g., `6c4642f7be9a4f8f8a0135427250212`).  
- `WEATHER_CITY` (optional): Query/city string; default `London`.
- Object storage (optional, enables upload step):  
  - `OBJECT_STORE_BUCKET` (e.g., `rps-data`)  
  - `OBJECT_STORE_ENDPOINT` (e.g., `http://localhost:9000` for MinIO)  
  - `OBJECT_STORE_ACCESS_KEY`, `OBJECT_STORE_SECRET_KEY`  
  - `OBJECT_STORE_REGION` (default `us-east-1`), `OBJECT_STORE_USE_SSL` (`true`/`false`)
- MLflow (optional, for profiling artifact):  
  - `MLFLOW_TRACKING_URI` (e.g., `https://dagshub.com/<user>/<repo>.mlflow`)  
  - `MLFLOW_TRACKING_USERNAME` / `MLFLOW_TRACKING_PASSWORD` or `MLFLOW_TRACKING_TOKEN`
- DVC:  
  - `DVC_REMOTE` (optional) to push to a named remote.
- MLflow / Dagshub (for training artifacts):  
  - `MLFLOW_TRACKING_URI` (e.g., `https://dagshub.com/<user>/<repo>.mlflow`)  
  - `MLFLOW_TRACKING_USERNAME` / `MLFLOW_TRACKING_PASSWORD` or `MLFLOW_TRACKING_TOKEN`

Set variables in Airflow UI or an `.env` loaded by your scheduler (avoid committing secrets).

## Running the DAG
1. Place `dags/weather_rps_dag.py` in your Airflow `dags/` directory (already present here).  
2. Start Airflow scheduler/webserver; ensure env vars above are loaded.  
3. Trigger `weather_rps_pipeline` manually or let it run on the daily schedule.

Task outline:
- **extract**: Call WeatherAPI `forecast.json` (2 days of hourly data), save raw JSON with timestamp.  
- **quality_check**: Schema + null ratio gate (>1% null fails DAG).  
- **transform**: Time encodings, lag features, and 4h/6h temp targets; save Parquet.  
- **profile**: ydata-profiling report; logs artifact to MLflow if configured.  
- **train_task**: Trains a RandomForestRegressor on processed data and logs params/metrics/model to MLflow (Dagshub if configured).  
- **upload**: Upload processed Parquet to object storage (`weather/processed/...`).  
- **dvc_track**: `dvc add` + `dvc push` for the processed dataset (remote required).

## Branching & PR Policy (dev → test → master)
- Long-lived branches: `dev` (integration), `test` (staging), `master` (release).  
- All work starts on feature branches from `dev` (e.g., `feature/<ticket>`).  
- Merge paths: `feature → dev`, then `dev → test`, then `test → master`.  
- Enforce PRs with ≥1 approval on `test` and `master` (recommended on `dev` too).  
- Require status checks to pass and branch to be up-to-date before merging.  
- Protect `test` and `master` in repo settings (no direct pushes; approvals required; required checks enabled).

## DVC Remote Quickstart (MinIO/S3-style)
```bash
dvc init
dvc remote add -d rps-remote s3://rps-data --endpointurl http://localhost:9000
export AWS_ACCESS_KEY_ID=<OBJECT_STORE_ACCESS_KEY>
export AWS_SECRET_ACCESS_KEY=<OBJECT_STORE_SECRET_KEY>
dvc push  # executed by DAG as well
```

## Artifact Locations
- Raw: `data/raw/weather_raw_<ds>.json`, validated Parquet in same folder.
- Processed: `data/processed/weather_processed_<ds>.parquet`.
- Profiling reports: `reports/profile_<ds>.html` (also in MLflow if configured).
- DVC metadata: `.dvc` file adjacent to processed Parquet.

## Notes
- The DAG fails fast if required columns are missing or null ratios exceed 1%.  
- Profiling step is skipped automatically if `ydata-profiling` is absent.  
- Object storage upload is skipped if `OBJECT_STORE_BUCKET` is unset.  
- Ensure `dvc` binary is on PATH for the scheduler; otherwise the DAG will fail in `dvc_track`.
