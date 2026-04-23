FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python scripts/generate_sample_data.py

EXPOSE 8000

CMD ["sh", "-c", "python scripts/seed_fast.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
