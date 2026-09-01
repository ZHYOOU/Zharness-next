#!/usr/bin/env bash

set -euo pipefail

# Resolve all runtime paths from the repository root. / 从仓库根目录解析所有运行时路径。
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/.zharness"
PID_FILE="${RUNTIME_DIR}/server.pid"
LOG_FILE="${RUNTIME_DIR}/server.log"
ENV_FILE="${REPO_ROOT}/zharness/.env"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

is_running() {
    [[ -f "${PID_FILE}" ]] || return 1
    local pid
    pid="$(<"${PID_FILE}")"
    [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

# Dump YAML-backed settings as KEY=VALUE lines, applying environment overrides
# through the Python config module. / 以 KEY=VALUE 行形式导出 YAML 配置，环境变量覆盖由 Python 配置模块应用。
config_values() {
    (
        cd "${REPO_ROOT}"
        uv run --package zharness python -c '
from zharness.config import get_settings
s = get_settings()
lines = [
    "ZHARNESS_SERVER_HOST=%s" % s.server.host,
    "ZHARNESS_SERVER_PORT=%s" % s.server.port,
    "ZHARNESS_SANDBOX_PROVIDER=%s" % s.sandbox.provider,
    "ZHARNESS_POSTGRES_MANAGED=%s" % ("true" if s.postgres.managed else "false"),
    "ZHARNESS_POSTGRES_USER=%s" % s.postgres.user,
    "ZHARNESS_POSTGRES_DB=%s" % s.postgres.database,
    "ZHARNESS_POSTGRES_PORT=%s" % s.postgres.port,
    "LANGSMITH_TRACING=%s" % ("true" if s.langsmith.tracing else "false"),
]
if s.langsmith.project:
    lines.append("LANGSMITH_PROJECT=%s" % s.langsmith.project)
print("\n".join(lines))
'
    )
}

_CONFIG_LOADED=0
load_config() {
    [[ "${_CONFIG_LOADED}" == "1" ]] && return 0
    local line key value
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        export "${key}=${value}"
    done < <(config_values)
    _CONFIG_LOADED=1
}

configured_sandbox_provider() {
    load_config
    printf '%s\n' "${ZHARNESS_SANDBOX_PROVIDER:-docker}" | tr '[:upper:]' '[:lower:]'
}

managed_postgres_enabled() {
    load_config
    local configured="${ZHARNESS_POSTGRES_MANAGED:-true}"
    [[ "${configured}" != "0" && "${configured}" != "false" && \
        "${configured}" != "no" && "${configured}" != "off" ]]
}

run_compose() {
    if [[ -f "${ENV_FILE}" ]]; then
        docker compose --env-file "${ENV_FILE}" --file "${COMPOSE_FILE}" "$@"
    else
        docker compose --file "${COMPOSE_FILE}" "$@"
    fi
}

check_docker() {
    local provider
    provider="$(configured_sandbox_provider)"
    if [[ "${provider}" == "local" ]] && ! managed_postgres_enabled; then
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
    if managed_postgres_enabled && ! docker compose version >/dev/null 2>&1; then
        printf 'Managed PostgreSQL requires the Docker Compose plugin.\n' >&2
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

start_postgres() {
    if ! managed_postgres_enabled; then
        return 0
    fi
    (
        cd "${REPO_ROOT}"
        run_compose up --detach --wait --wait-timeout 60 postgres
    )
    printf 'PostgreSQL is ready.\n'
}

stop_postgres() {
    if ! managed_postgres_enabled; then
        return 0
    fi
    (
        cd "${REPO_ROOT}"
        run_compose stop postgres
    )
}

show_postgres_logs() {
    (
        cd "${REPO_ROOT}"
        run_compose logs --follow postgres
    )
}

check_start_requirements() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        printf 'Missing %s; copy zharness/.env.example and configure it first.\n' "${ENV_FILE}" >&2
        return 1
    fi
    load_config
    local port="${ZHARNESS_SERVER_PORT:-2024}"
    if [[ ! "${port}" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
        printf 'Invalid ZHARNESS_SERVER_PORT: %s\n' "${port}" >&2
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
    SERVER_HOST="${ZHARNESS_SERVER_HOST:-127.0.0.1}"
    SERVER_PORT="${ZHARNESS_SERVER_PORT:-2024}"
    start_postgres
    cd "${REPO_ROOT}"
    exec uv run langgraph dev --no-browser \
        --host "${SERVER_HOST}" --port "${SERVER_PORT}"
}

start_server() {
    check_start_requirements
    start_postgres
    if is_running; then
        printf 'ZHarness is already running (PID %s).\n' "$(<"${PID_FILE}")"
        return 0
    fi

    rm -f "${PID_FILE}"
    SERVER_HOST="${ZHARNESS_SERVER_HOST:-127.0.0.1}"
    SERVER_PORT="${ZHARNESS_SERVER_PORT:-2024}"

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
        stop_postgres
        ;;
    restart)
        stop_server
        stop_postgres
        start_server
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    postgres-start)
        check_start_requirements
        start_postgres
        ;;
    postgres-stop)
        stop_postgres
        ;;
    postgres-logs)
        show_postgres_logs
        ;;
    *)
        printf 'Usage: %s {dev|start|stop|restart|status|logs|postgres-start|postgres-stop|postgres-logs}\n' "$0" >&2
        exit 2
        ;;
esac
