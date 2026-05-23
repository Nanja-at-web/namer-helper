FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Config- und Report-Verzeichnisse
VOLUME ["/etc/namer-helper", "/var/lib/namer-helper/reports", "/var/lib/namer/failed"]

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
