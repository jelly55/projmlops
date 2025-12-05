#!/usr/bin/env python3
"""
Load testing script for monitoring stack validation.

Generates a mix of normal and anomalous prediction requests to test:
- Latency monitoring
- Request counting
- Data drift detection
- Alerting thresholds
"""

import requests
import time
import random
import sys
from datetime import datetime

API_URL = "http://localhost:8000/predict"
HEALTH_URL = "http://localhost:8000/health"


def check_service_health():
    """Check if the API service is running."""
    try:
        response = requests.get(HEALTH_URL, timeout=5)
        if response.status_code == 200:
            print("[OK] API service is healthy")
            return True
        else:
            print(f"[WARN] API health check returned status {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Cannot connect to API: {e}")
        print(f"        Please ensure the monitoring stack is running:")
        print(f"        cd monitoring && docker-compose up -d")
        return False


def generate_normal_request():
    """Generate a request with features in normal range."""
    base_time = 1700000000 + random.randint(0, 1000000)
    temp = random.uniform(-10, 40)  # Normal temperature range
    humidity = random.uniform(20, 90)
    wind = random.uniform(0, 50)

    features = [
        base_time,                      # time_epoch
        temp,                           # temp_c
        humidity,                       # humidity
        wind,                           # wind_kph
        random.randint(0, 23),          # hour
        random.randint(0, 6),           # dayofweek
        random.choice([0, 1]),          # is_weekend
        temp + random.uniform(-2, 2),   # lag_temp_1h
        temp + random.uniform(-3, 3),   # lag_temp_2h
        temp + random.uniform(-1, 1),   # rolling_temp_3h
        humidity + random.uniform(-5, 5),  # lag_humidity_1h
    ]
    return {"features": features}


def generate_anomalous_request():
    """Generate a request with out-of-range features to trigger drift detection."""
    anomaly_type = random.choice(['high_temp', 'low_temp', 'high_wind', 'invalid_humidity'])

    if anomaly_type == 'high_temp':
        temp = random.uniform(70, 150)  # Extremely high
    elif anomaly_type == 'low_temp':
        temp = random.uniform(-100, -60)  # Extremely low
    elif anomaly_type == 'high_wind':
        temp = random.uniform(15, 25)  # Normal temp
    else:  # invalid_humidity
        temp = random.uniform(15, 25)

    base_time = 1700000000 + random.randint(0, 1000000)
    humidity = random.uniform(120, 200) if anomaly_type == 'invalid_humidity' else random.uniform(30, 70)
    wind = random.uniform(300, 500) if anomaly_type == 'high_wind' else random.uniform(5, 30)

    features = [
        base_time,
        temp,
        humidity,
        wind,
        random.randint(0, 23),
        random.randint(0, 6),
        random.choice([0, 1]),
        temp + random.uniform(-2, 2),
        temp + random.uniform(-3, 3),
        temp + random.uniform(-1, 1),
        humidity + random.uniform(-5, 5),
    ]
    return {"features": features, "anomaly_type": anomaly_type}


def send_requests(num_requests=100, anomaly_rate=0.2, requests_per_sec=2):
    """
    Send prediction requests to the API.

    Args:
        num_requests: Total number of requests to send
        anomaly_rate: Proportion of anomalous requests (0-1)
        requests_per_sec: Request rate
    """
    print(f"\n{'='*60}")
    print(f"Load Test Configuration")
    print(f"{'='*60}")
    print(f"Total requests: {num_requests}")
    print(f"Anomaly rate: {anomaly_rate * 100}%")
    print(f"Request rate: {requests_per_sec} req/sec")
    print(f"Duration: ~{num_requests / requests_per_sec:.0f} seconds")
    print(f"{'='*60}\n")

    stats = {
        "total": 0,
        "success": 0,
        "error": 0,
        "anomalous": 0,
        "drift_detected": 0,
        "total_latency": 0,
    }

    start_time = time.time()

    for i in range(num_requests):
        # Decide if this request should be anomalous
        is_anomaly = random.random() < anomaly_rate

        if is_anomaly:
            request_data = generate_anomalous_request()
            anomaly_type = request_data.pop("anomaly_type")
            stats["anomalous"] += 1
        else:
            request_data = generate_normal_request()
            anomaly_type = None

        try:
            response = requests.post(API_URL, json=request_data, timeout=10)
            stats["total"] += 1

            if response.status_code == 200:
                stats["success"] += 1
                result = response.json()
                latency = result.get("latency_ms", 0)
                stats["total_latency"] += latency

                if result.get("drift_detected"):
                    stats["drift_detected"] += 1

                status_icon = "[DRIFT]" if result.get("drift_detected") else "[OK]"
                anomaly_info = f" ({anomaly_type})" if anomaly_type else ""
                print(f"{status_icon} Request {i+1}/{num_requests}: "
                      f"Pred={result['prediction']:.2f}, "
                      f"Latency={latency:.1f}ms{anomaly_info}")
            else:
                stats["error"] += 1
                print(f"[ERROR] Request {i+1}: Status {response.status_code}")

        except Exception as e:
            stats["error"] += 1
            stats["total"] += 1
            print(f"[ERROR] Request {i+1}: {e}")

        # Rate limiting
        time.sleep(1.0 / requests_per_sec)

    elapsed = time.time() - start_time

    # Print summary
    print(f"\n{'='*60}")
    print(f"Load Test Summary")
    print(f"{'='*60}")
    print(f"Total requests: {stats['total']}")
    print(f"Successful: {stats['success']} ({stats['success']/stats['total']*100:.1f}%)")
    print(f"Errors: {stats['error']}")
    print(f"Anomalous sent: {stats['anomalous']} ({stats['anomalous']/stats['total']*100:.1f}%)")
    print(f"Drift detected: {stats['drift_detected']} ({stats['drift_detected']/stats['total']*100:.1f}%)")
    if stats['success'] > 0:
        print(f"Avg latency: {stats['total_latency']/stats['success']:.1f}ms")
    print(f"Actual duration: {elapsed:.1f}s")
    print(f"Actual rate: {stats['total']/elapsed:.2f} req/sec")
    print(f"{'='*60}\n")

    print("Next steps:")
    print("1. Open Grafana: http://localhost:3000 (admin/admin)")
    print("2. View the 'Weather MLOps - Model Monitoring' dashboard")
    print("3. Check Prometheus: http://localhost:9090")
    print("4. Verify metrics: http://localhost:8000/metrics")


def main():
    print("Weather MLOps - Monitoring Stack Test")
    print("="*60)

    # Check service health
    if not check_service_health():
        sys.exit(1)

    print("\nStarting load test in 3 seconds...")
    time.sleep(3)

    # Run load test
    send_requests(
        num_requests=100,
        anomaly_rate=0.25,  # 25% anomalous requests
        requests_per_sec=2   # 2 req/sec
    )


if __name__ == "__main__":
    main()
