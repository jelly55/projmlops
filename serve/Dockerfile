FROM python:3.12-slim

WORKDIR /app

COPY serve/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY serve/app.py .

# Defaults can be overridden at run time
ENV MODEL_URI=models:/weather_rf_4h/Production
ENV MLFLOW_TRACKING_URI=${MLFLOW_TRACKING_URI}
ENV MLFLOW_TRACKING_USERNAME=${MLFLOW_TRACKING_USERNAME}
ENV MLFLOW_TRACKING_PASSWORD=${MLFLOW_TRACKING_PASSWORD}

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
