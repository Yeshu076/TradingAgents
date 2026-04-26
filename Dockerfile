FROM python:3.10-slim

WORKDIR /app

# Install system dependencies if required (e.g. gcc for some python packages)
RUN apt-get update && apt-get install -y gcc g++ vim && rm -rf /var/lib/apt/lists/*

# Install python dependencies first to cache the layer
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Install the app (if it takes setup.py or pyproject.toml)
RUN pip install -e .

# By default run the help menu, but docker-compose can override this to daemon.
CMD ["python", "main.py", "--help"]