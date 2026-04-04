FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget gnupg2 && \
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends google-chrome-stable && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
COPY carsearch/ carsearch/
COPY web/ web/

RUN pip install --no-cache-dir . && \
    playwright install chromium

EXPOSE 8000

# Default: run the web app. Override with "python -m carsearch" for CLI.
CMD ["python", "-m", "web"]
