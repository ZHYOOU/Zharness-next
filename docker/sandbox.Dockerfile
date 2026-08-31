FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.14-slim

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        coreutils \
        curl \
        findutils \
        git \
        grep \
        make \
        pkg-config \
        python3-dev \
        ripgrep \
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["sleep", "infinity"]
