#!/usr/bin/env bash

set -euo pipefail

# Resolve all runtime paths from the repository root. / 从仓库根目录解析所有运行时路径。
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/.zharness"
PID_FILE="${RUNTIME_DIR}/server.pid"
LOG_FILE="${RUNTIME_DIR}/server.log"
ENV_FILE="${REPO_ROOT}/zharness/.env"
SERVER_HOST="${ZHARNESS_SERVER_HOST:-127.0.0.1}"
SERVER_PORT="${ZHARNESS_SERVER_PORT:-2024}"

is_running() {
    [[ -f "${PID_FILE}" ]] || return 1
    local pid
    pid="$(<"${PID_FILE}")"
    [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

configured_sandbox_provider() {
    (
        cd "${REPO_ROOT}"
        uv run --package zharness python -c \
            'import os, sys; from dotenv import load_dotenv; load_dotenv(sys.argv[1], override=False); print(os.environ.get("ZHARNESS_SANDBOX_PROVIDER", "docker").lower())' \
            "${ENV_FILE}"
    )
}

check_docker() {
    local provider
    provider="$(configured_sandbox_provider)"
    if [[ "${provider}" == "local" ]]; then
        return 0
    fi

    if ! command -v docker >/dev/null 2>&1; then
        printf 'Docker sandbox is enabled, but the docker command is unavailable.\n' >&2
        return 1
    fi
    if ! command -v timeout >/dev/null 2>&1; then
        printf 'Docker sandbox is enabled, but the timeout command is unavailable.\n' >&2
        return 1
    fi

    local docker_status
    if timeout --signal=TERM --kill-after=1s 5s docker info >/dev/null 2>&1; then
        return 0
    else
        docker_status=$?
    fi
    if [[ "${docker_status}" == "124" || "${docker_status}" == "137" ]]; then
        printf 'Docker health check timed out after 5 seconds.\n' >&2
        printf 'Docker may be paused or unresponsive; resume or restart it, then retry.\n' >&2
    else
        printf 'Docker sandbox is enabled, but Docker is not running or is not accessible.\n' >&2
        printf 'Start Docker and verify that `docker info` succeeds, then retry.\n' >&2
    fi
    return 1
}

check_start_requirements() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        printf 'Missing %s; copy zharness/.env.example and configure it first.\n' "${ENV_FILE}" >&2
        return 1
    fi
    if [[ ! "${SERVER_PORT}" =~ ^[0-9]+$ ]] || ((SERVER_PORT < 1 || SERVER_PORT > 65535)); then
        printf 'Invalid ZHARNESS_SERVER_PORT: %s\n' "${SERVER_PORT}" >&2
        return 1
    fi
    check_docker
}

dev_server() {
    if is_running; then
        printf 'ZHarness is already running in the background (PID %s).\n' "$(<"${PID_FILE}")" >&2
        return 1
    fi

    rm -f "${PID_FILE}"
    check_start_requirements
    cd "${REPO_ROOT}"
    exec uv run langgraph dev --no-browser \
        --host "${SERVER_HOST}" --port "${SERVER_PORT}"
}

start_server() {
    if is_running; then
        printf 'ZHarness is already running (PID %s).\n' "$(<"${PID_FILE}")"
        return 0
    fi

    rm -f "${PID_FILE}"
    check_start_requirements

    mkdir -p "${RUNTIME_DIR}"
    (
        cd "${REPO_ROOT}"
        nohup setsid uv run langgraph dev --no-browser \
            --host "${SERVER_HOST}" --port "${SERVER_PORT}" \
            >>"${LOG_FILE}" 2>&1 </dev/null &
        printf '%s\n' "$!" >"${PID_FILE}"
    )

    local pid
    pid="$(<"${PID_FILE}")"
    sleep 1
    if ! kill -0 "${pid}" 2>/dev/null; then
        rm -f "${PID_FILE}"
        printf 'ZHarness failed to start; inspect %s.\n' "${LOG_FILE}" >&2
        return 1
    fi

    printf 'ZHarness started (PID %s) at http://%s:%s.\n' \
        "${pid}" "${SERVER_HOST}" "${SERVER_PORT}"
    printf 'Logs: %s\n' "${LOG_FILE}"
}

stop_server() {
    if ! is_running; then
        rm -f "${PID_FILE}"
        printf 'ZHarness is not running.\n'
        return 0
    fi

    local pid
    pid="$(<"${PID_FILE}")"
    local command
    command="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
    if [[ "${command}" != *"langgraph"* ]]; then
        printf 'Refusing to stop PID %s because it is not a LangGraph process: %s\n' \
            "${pid}" "${command}" >&2
        return 1
    fi

    # Signal the process group so the reloader and worker stop together. / 向进程组发送信号，使重载器和工作进程一同停止。
    kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
    for _ in {1..60}; do
        if ! kill -0 "${pid}" 2>/dev/null; then
            rm -f "${PID_FILE}"
            printf 'ZHarness stopped.\n'
            return 0
        fi
        sleep 0.25
    done

    printf 'ZHarness did not stop gracefully; forcing shutdown.\n'
    kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "${pid}" 2>/dev/null || true
    rm -f "${PID_FILE}"
}

show_status() {
    if is_running; then
        printf 'ZHarness is running (PID %s) at http://%s:%s.\n' \
            "$(<"${PID_FILE}")" "${SERVER_HOST}" "${SERVER_PORT}"
    else
        rm -f "${PID_FILE}"
        printf 'ZHarness is not running.\n'
        return 1
    fi
}

show_logs() {
    if [[ ! -f "${LOG_FILE}" ]]; then
        printf 'No server log exists at %s.\n' "${LOG_FILE}" >&2
        return 1
    fi
    tail -f "${LOG_FILE}"
}

case "${1:-}" in
    dev)
        dev_server
        ;;
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        stop_server
        start_server
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    *)
        printf 'Usage: %s {dev|start|stop|restart|status|logs}\n' "$0" >&2
        exit 2
        ;;
esac
