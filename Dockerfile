FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_ENV=production
ENV SQLITE_DB_PATH=/app/data/planner_cloud.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

CMD ["sh", "scripts/start_cloud.sh"]
