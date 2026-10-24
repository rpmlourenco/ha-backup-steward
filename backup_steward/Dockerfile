FROM rclone/rclone:1.75.1@sha256:45401ad7410db1d67ffdb58e19059ad20b0d8e0285a60e38bbec55cc1019c7a5 AS rclone
FROM python:3.14-slim-trixie@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd util-linux udev ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY licenses/rclone.txt /usr/share/licenses/rclone/COPYING
COPY steward ./steward
EXPOSE 8099 8100
CMD ["python", "-m", "steward"]
