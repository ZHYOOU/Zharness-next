FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        coreutils \
        findutils \
        git \
        grep \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["sleep", "infinity"]
