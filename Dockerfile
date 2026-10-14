FROM python:3.12-slim
WORKDIR /app
COPY adapter/adapter.py /app/adapter.py
USER 65532:65532
ENTRYPOINT ["python", "/app/adapter.py"]
