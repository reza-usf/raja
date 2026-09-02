FROM mcr.microsoft.com/playwright/python:v1.54.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tools ./tools
RUN mkdir -p /app/data/debug

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.main"]
