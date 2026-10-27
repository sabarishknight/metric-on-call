FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir pandas google-cloud-bigquery google-cloud-storage
COPY common ./common
COPY pipelines ./pipelines
ENV WAREHOUSE=bigquery
CMD ["python", "-m", "pipelines.load_to_warehouse", "--landing", "/tmp/landing"]
