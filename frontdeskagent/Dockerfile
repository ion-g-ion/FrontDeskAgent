# https://developers.home-assistant.io/docs/add-ons/
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.14-alpine3.22-2026.03.1
FROM ${BUILD_FROM}

# Execute during the build of the image
ARG TEMPIO_VERSION BUILD_ARCH
RUN \
    curl -sSLf -o /usr/bin/tempio \
    "https://github.com/home-assistant/tempio/releases/download/${TEMPIO_VERSION}/tempio_${BUILD_ARCH}"

RUN apk add --no-cache \
    python3 \
    py3-pip \
    ffmpeg \
    build-base \
    python3-dev \
    linux-headers

COPY requirements.txt /
RUN pip install --no-cache-dir --no-binary=google-crc32c -r /requirements.txt

# Copy root filesystem
COPY rootfs /
