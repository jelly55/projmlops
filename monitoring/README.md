# MLOps Monitoring Stack

Complete monitoring solution for the Weather Prediction MLOps pipeline using Prometheus and Grafana.

## Architecture

```
FastAPI Service (port 8000)
    ↓ /metrics endpoint
Prometheus (port 9090)
    ↓ scrapes metrics every 10s
Grafana (port 3000)
    ↓ visualizes + alerts
```

## Components

### 1. FastAPI Prediction Service
- **Endpoint**: http://localhost:8000
- **Metrics Endpoint**: http://localhost:8000/metrics
- **Health Check**: http://localhost:8000/health
- **API Docs**: http://localhost:8000/docs

**Exposed Metrics**:
- `prediction_requests_total` - Counter of total requests by endpoint and status
- `prediction_latency_seconds` - Histogram of prediction latency
- `data_drift_ratio` - Gauge for data drift detection (0-1)
- `out_of_range_features_total` - Counter of out-of-distribution features
- `model_load_time_seconds` - Gauge for model loading time

### 2. Prometheus
- **UI**: http://localhost:9090
- **Configuration**: `prometheus.yml`
- **Scrape Interval**: 10 seconds
- **Retention**: 30 days

### 3. Grafana
- **UI**: http://localhost:3000
- **Default Credentials**: admin/admin
- **Dashboard**: "Weather MLOps - Model Monitoring"

## Quick Start

### Prerequisites
- Docker and Docker Compose installed
- MLflow credentials configured in `.env` file

### 1. Create `.env` file

```bash
cd monitoring
cat > .env <<EOF
MLFLOW_TRACKING_URI=https://dagshub.com/i228755/my-first-repo.mlflow
MLFLOW_TRACKING_USERNAME=i228755
MLFLOW_TRACKING_PASSWORD=c5facd761018c1bd0b4eeae5fe5dd8d68ac7b1e3
EOF
```

### 2. Start the monitoring stack

```bash
docker-compose up -d
```

This will start:
- FastAPI service on port 8000
- Prometheus on port 9090
- Grafana on port 3000

### 3. Verify services are running

```bash
# Check container status
docker-compose ps

# Check FastAPI health
curl http://localhost:8000/health

# Check Prometheus metrics
curl http://localhost:8000/metrics

# Check Prometheus targets
# Open: http://localhost:9090/targets
```

### 4. Access Grafana Dashboard

1. Open http://localhost:3000
2. Login with `admin/admin` (change password when prompted)
3. Navigate to Dashboards → "Weather MLOps - Model Monitoring"

## Testing the Monitoring

### Generate Sample Requests

```bash
# Normal prediction (within range)
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "features": [1700000000, 20.5, 65, 15, 14, 3, 0, 20.3, 20.1, 20.2, 64]
  }'

# Out-of-range prediction (triggers drift detection)
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "features": [1700000000, 150, 65, 15, 14, 3, 0, 20.3, 20.1, 20.2, 64]
  }'
```

### Load Testing Script

```python
import requests
import time
import random

API_URL = "http://localhost:8000/predict"

def generate_request():
    # Mix of normal and anomalous requests
    if random.random() > 0.8:  # 20% anomalous
        temp = random.uniform(100, 150)  # Out of range
    else:
        temp = random.uniform(-10, 40)  # Normal range

    features = [
        1700000000 + random.randint(0, 1000000),  # time_epoch
        temp,                                      # temp_c
        random.uniform(0, 100),                    # humidity
        random.uniform(0, 50),                     # wind_kph
        random.randint(0, 23),                     # hour
        random.randint(0, 6),                      # dayofweek
        random.choice([0, 1]),                     # is_weekend
        temp + random.uniform(-2, 2),              # lag_temp_1h
        temp + random.uniform(-3, 3),              # lag_temp_2h
        temp + random.uniform(-1, 1),              # rolling_temp_3h
        random.uniform(0, 100),                    # lag_humidity_1h
    ]
    return {"features": features}

# Send 100 requests
for i in range(100):
    try:
        response = requests.post(API_URL, json=generate_request())
        print(f"Request {i+1}: Status {response.status_code}, Latency: {response.json().get('latency_ms')}ms")
        time.sleep(0.5)  # 2 req/sec
    except Exception as e:
        print(f"Error: {e}")
```

## Grafana Dashboard Panels

1. **Prediction API Latency** - Shows avg, P95, P99 latency over time
2. **Request Rate** - Success and error request rates
3. **Data Drift Ratio** - Gauge showing current drift level (0-100%)
4. **Out-of-Range Features** - Bar chart of anomalous features detected
5. **Model Load Time** - Time to load model from MLflow
6. **Total Requests** - Cumulative requests by endpoint and status

## Alerts

Pre-configured alerts (in Grafana):

### 1. High Prediction Latency
- **Condition**: Avg latency > 500ms for 2 minutes
- **Severity**: Warning

### 2. High Data Drift
- **Condition**: Drift ratio > 30% for 5 minutes
- **Severity**: Critical

### 3. High Error Rate
- **Condition**: Error rate > 0.1 req/sec for 2 minutes
- **Severity**: Critical

## Customizing Alerts

Alerts can be configured in:
- Grafana UI: Alerting → Alert Rules
- Config file: `grafana/provisioning/alerting/alerts.yml`

To add notification channels (Slack, Email, etc.):
1. Go to Grafana → Alerting → Contact Points
2. Add your notification channel
3. Link it to alert rules

## Troubleshooting

### Service not accessible
```bash
docker-compose logs <service-name>
# Example: docker-compose logs weather-api
```

### Prometheus not scraping
1. Check Prometheus targets: http://localhost:9090/targets
2. Ensure FastAPI `/metrics` is accessible: `curl http://localhost:8000/metrics`

### Grafana dashboard empty
1. Verify Prometheus datasource is configured
2. Check if metrics are being collected in Prometheus
3. Adjust time range in Grafana (top right corner)

### Model loading issues
Ensure MLflow credentials are correctly set in `.env` file

## Stopping the Stack

```bash
# Stop services
docker-compose down

# Stop and remove volumes (resets all data)
docker-compose down -v
```

## Production Considerations

1. **Persistent Storage**: Volumes are already configured for Prometheus and Grafana data
2. **Security**:
   - Change Grafana admin password
   - Use secrets management for MLflow credentials
   - Enable HTTPS/TLS
3. **Scalability**: Consider Prometheus federation for multiple services
4. **Backup**: Regular backups of Grafana dashboards and Prometheus data
5. **Alert Channels**: Configure real notification channels (Slack, PagerDuty, etc.)
