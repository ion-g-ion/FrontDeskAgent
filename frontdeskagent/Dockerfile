# https://developers.home-assistant.io/docs/add-ons/
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.22-2026.03.1
FROM ${BUILD_FROM}

# Execute during the build of the image
ARG TEMPIO_VERSION BUILD_ARCH
RUN \
    curl -sSLf -o /usr/bin/tempio \
    "https://github.com/home-assistant/tempio/releases/download/${TEMPIO_VERSION}/tempio_${BUILD_ARCH}"

RUN set -eux; \
    success=0; \
    for attempt in 1 2 3 4 5; do \
        if apk add --no-cache \
            ffmpeg; then \
            success=1; \
            break; \
        fi; \
        echo "apk add failed (attempt ${attempt}), retrying..."; \
        sleep $((attempt * 2)); \
    done; \
    test "$success" -eq 1

COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

# Copy root filesystem
COPY rootfs /
