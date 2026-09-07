#!/usr/bin/env bash

set -euo pipefail

# Unified ZHarness development launcher. / 统一的 ZHarness 开发启动器。
#
# Modeled on the legacy serve.sh launcher: preflight and reclaim the service
# ports, start managed PostgreSQL, backend, frontend and Nginx, wait for each to
# become ready, and tear everything down on Ctrl+C or `make stop`. Each service
# logs to its own file under .zharness/logs/.
# / 参照旧版 serve.sh 启动器实现：预检并回收服务端口，依次启动托管 PostgreSQL、
# 后端、前端与 Nginx，等待各服务就绪，并在 Ctrl+C 或 make stop 时统一清理。
# 各服务日志分别写入 .zharness/logs/ 下。

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/.zharness"
LOG_DIR="${RUNTIME_DIR}/logs"
NGINX_CONFIG="${REPO_ROOT}/nginx/nginx.dev.conf"
NGINX_PID_FILE="${RUNTIME_DIR}/nginx.pid"
FRONTEND_LOCK="${REPO_ROOT}/frontend/.next/dev/lock"
SERVER_LOG="${RUNTIME_DIR}/server.log"

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="2024"
FRONTEND_PORT="3000"
NGINX_PORT="2026"
POSTGRES_PORT="5432"
MANAGED_POSTGRES_CONTAINER="zharness-next-postgres-1"

# Every ZHarness checkout shares the same dev ports, so a service started from
# any of them must be reclaimable from here. ZHARNESS_ROOTS covers this repo,
# its git worktrees, and sibling `zharness*` checkouts. Sorted most-specific
# first (longest path first).
# / 每个 ZHarness 检出共用相同的开发端口，因此任何一处启动的服务都必须能在此回收。
# ZHARNESS_ROOTS 包含本仓库、其 git worktree 以及同级的 zharness* 检出。
# 按最长路径优先排序。
ZHARNESS_ROOTS="$({
    printf '%s\n' "$REPO_ROOT"
    git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2}'
    ls -d "${REPO_ROOT}/../"zharness* 2>/dev/null || true
} | awk 'NF && !seen[$0]++ {print length($0)"\t"$0}' | sort -rn | sed 's/^[0-9]*\t//')"

# Load the backend host/port from the project configuration. / 从项目配置加载后端主机与端口。
load_config() {
    local out key value
    out="$("${REPO_ROOT}/scripts/server.sh" config)" || {
        printf '✗ Failed to load project configuration.\n' >&2
        exit 1
    }
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        key="${line%%=*}"
        value="${line#*=}"
        export "${key}=${value}"
    done <<<"${out}"
    BACKEND_HOST="${ZHARNESS_SERVER_HOST:-127.0.0.1}"
    BACKEND_PORT="${ZHARNESS_SERVER_PORT:-2024}"
}

# Print the PIDs listening on a port. / 列出监听指定端口的进程 PID。
_port_pids() {
    local port=$1
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp "( sport = :$port )" 2>/dev/null | sed -nE 's/.*users:\(\(".*",pid=([0-9]+).*/\1/p'
        return 0
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true
        return 0
    fi
    if command -v netstat >/dev/null 2>&1; then
        netstat -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p {
            n = split($7, a, ",")
            for (i = 1; i <= n; i++) { gsub(/[^0-9]/, "", a[i]); if (a[i] != "") print a[i] }
        }'
    fi
}

_is_port_listening() {
    [ -n "$(_port_pids "$1")" ]
}

# True if the process belongs to a ZHarness checkout. / 判断进程是否属于 ZHarness 检出。
_is_zharness_pid() {
    local pid=$1 root args cwd
    if [ -r "/proc/$pid/environ" ] &&
        tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -Fxq "ZHARNESS_DAEMON_ROOT=$REPO_ROOT"; then
        return 0
    fi
    args=$(ps -p "$pid" -o args= 2>/dev/null) || return 1
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || cwd=""
    while IFS= read -r root; do
        [ -n "$root" ] || continue
        case "$args" in
            *"$root"*) return 0 ;;
        esac
        case "$cwd" in
            "$root" | "$root"/*) return 0 ;;
        esac
    done <<<"$ZHARNESS_ROOTS"
    return 1
}

# True if the process is an Nginx dev gateway of a ZHarness checkout.
# / 判断进程是否为某个 ZHarness 检出的 Nginx 开发网关。
_is_repo_nginx_pid() {
    local pid=$1 args root master
    args=$(ps -p "$pid" -o args= 2>/dev/null) || return 1
    case "$args" in *nginx*) ;; *) return 1 ;; esac
    while IFS= read -r root; do
        [ -n "$root" ] || continue
        case "$args" in *"$root"*) return 0 ;; esac
    done <<<"$ZHARNESS_ROOTS"
    # Worker process: fall back to verifying its master. / 工作进程：回退到校验其主进程。
    master="$(_nginx_master "$pid")"
    [ -n "$master" ] || return 1
    args=$(ps -p "$master" -o args= 2>/dev/null) || return 1
    while IFS= read -r root; do
        [ -n "$root" ] || continue
        case "$args" in *"$root"*) return 0 ;; esac
    done <<<"$ZHARNESS_ROOTS"
    return 1
}

# Return the master PID of an Nginx process (or the PID itself). / 返回 Nginx 进程的主进程 PID（或自身）。
_nginx_master() {
    local pid=$1 parent
    while true; do
        case "$(ps -p "$pid" -o comm= 2>/dev/null)" in nginx) ;; *) break ;; esac
        parent="$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ')"
        [ -n "$parent" ] && [ "$parent" != "$pid" ] || break
        case "$(ps -p "$parent" -o comm= 2>/dev/null)" in nginx) pid="$parent" ;; *) break ;; esac
    done
    printf '%s\n' "$pid"
}

# Reclaim a port held by ZHarness processes; error on foreign owners.
# / 回收被 ZHarness 进程占用的端口；对无关进程则报错。
_reclaim_port() {
    local port=$1 pid master foreign="" reclaimed=0
    while IFS= read -r pid; do
        [ -n "$pid" ] || continue
        if _is_repo_nginx_pid "$pid"; then
            # Kill the master so workers die with it. / 结束主进程，让工作进程随之退出。
            master="$(_nginx_master "$pid")"
            kill -TERM "$master" 2>/dev/null || true
            reclaimed=1
        elif _is_zharness_pid "$pid"; then
            kill -TERM "$pid" 2>/dev/null || true
            reclaimed=1
        else
            foreign="$foreign $pid"
        fi
    done < <(_port_pids "$port")

    if [ "$reclaimed" = "1" ]; then
        for _ in {1..40}; do
            _is_port_listening "$port" || break
            sleep 0.25
        done
        # Force-kill ZHarness survivors. / 强制结束未退出的 ZHarness 进程。
        while IFS= read -r pid; do
            [ -n "$pid" ] || continue
            if _is_repo_nginx_pid "$pid"; then
                master="$(_nginx_master "$pid")"
                kill -KILL "$master" 2>/dev/null || true
            elif _is_zharness_pid "$pid"; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        done < <(_port_pids "$port")
    fi

    if [ -n "$foreign" ]; then
        # Re-verify: the port may have been freed concurrently (e.g. another
        # cleanup racing with this one). / 复核：端口可能已被并发释放（例如两个清理任务竞争）。
        sleep 1
        if ! _is_port_listening "$port"; then
            return 0
        fi
        printf '✗ Port %s is used by a non-ZHarness process%s.\n' "$port" "$foreign" >&2
        printf '  Free the port manually, then retry.\n' >&2
        return 1
    fi
    return 0
}

# Ensure the managed PostgreSQL host port is free (or owned by our container).
# / 确保托管 PostgreSQL 的宿主机端口可用（或属于本项目的容器）。
_ensure_postgres_ready() {
    if ! _is_port_listening "$POSTGRES_PORT"; then
        return 0
    fi
    if ! command -v docker >/dev/null 2>&1; then
        printf '✗ Port %s is in use but docker is unavailable to inspect it.\n' "$POSTGRES_PORT" >&2
        return 1
    fi
    local name
    name="$(docker ps --filter "publish=$POSTGRES_PORT" --format '{{.Names}}' 2>/dev/null | head -1 || true)"
    if [ -z "$name" ]; then
        printf '✗ Port %s is in use by a non-container process.\n' "$POSTGRES_PORT" >&2
        return 1
    fi
    if [ "$name" = "$MANAGED_POSTGRES_CONTAINER" ]; then
        return 0
    fi
    if [[ "$name" == zharness* ]]; then
        printf '↻ Stopping %s to free port %s for this project.\n' "$name" "$POSTGRES_PORT"
        docker stop "$name" >/dev/null 2>&1 || true
        return 0
    fi
    printf '✗ Port %s is used by unrelated container %s.\n' "$POSTGRES_PORT" "$name" >&2
    return 1
}

# Pre-flight: reclaim service ports held by other ZHarness checkouts.
# / 预检：回收被其他 ZHarness 检出占用的服务端口。
_preflight() {
    printf 'Pre-flight checks...\n'
    _reclaim_port "$BACKEND_PORT" || return 1
    _reclaim_port "$FRONTEND_PORT" || return 1
    _reclaim_port "$NGINX_PORT" || return 1
    _ensure_postgres_ready || return 1
    printf '✓ Ports are free\n'
}

# Wait until a port is listening. / 等待端口开始监听。
_wait_for_port() {
    local port=$1 timeout=$2 name=$3 waited=0
    until _is_port_listening "$port"; do
        if [ "$waited" -ge "$timeout" ]; then
            printf '✗ %s did not start within %ss on port %s.\n' "$name" "$timeout" "$port" >&2
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}

# Run a service in the background and wait for its port. / 后台运行服务并等待端口就绪。
run_service() {
    local name=$1 cmd=$2 port=$3 timeout=$4 log=$5
    if _is_port_listening "$port"; then
        printf '✗ %s cannot start because port %s is already in use.\n' "$name" "$port" >&2
        printf '  Run `make stop` first or free the port manually.\n' >&2
        cleanup 1
    fi
    printf 'Starting %s...\n' "$name"
    (cd "$REPO_ROOT" && sh -c "$cmd") >>"$log" 2>&1 &
    _wait_for_port "$port" "$timeout" "$name" || {
        printf '✗ %s failed to start; tail of %s:\n' "$name" "$log" >&2
        tail -20 "$log" >&2
        cleanup 1
    }
    printf '✓ %s started on port %s\n' "$name" "$port"
}

stop_nginx() {
    local pid
    if [ -f "$NGINX_PID_FILE" ]; then
        pid="$(<"$NGINX_PID_FILE")"
        if _is_repo_nginx_pid "$pid"; then
            printf 'Stopping Nginx (PID %s).\n' "$pid"
            kill -TERM "$pid" 2>/dev/null || true
            for _ in {1..40}; do
                _is_port_listening "$NGINX_PORT" || break
                sleep 0.25
            done
        fi
        rm -f "$NGINX_PID_FILE"
    fi
    # Reclaim any leftover Nginx holding the gateway port. / 回收仍占用网关端口的 Nginx。
    while IFS= read -r pid; do
        [ -n "$pid" ] || continue
        if _is_repo_nginx_pid "$pid"; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done < <(_port_pids "$NGINX_PORT")
}

stop_frontend() {
    local pid
    if [ -f "$FRONTEND_LOCK" ]; then
        pid="$(node -e '
const fs = require("node:fs");
const lock = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
if (Number.isInteger(lock.pid) && lock.pid > 0) process.stdout.write(String(lock.pid));
' "$FRONTEND_LOCK" 2>/dev/null || true)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            printf 'Stopping frontend (PID %s).\n' "$pid"
            kill -TERM "$pid" 2>/dev/null || true
            for _ in {1..40}; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.25
            done
            kill -KILL "$pid" 2>/dev/null || true
        fi
        rm -f "$FRONTEND_LOCK"
    fi
    # Reclaim any leftover Next.js dev server. / 回收残留的 Next.js 开发服务器。
    while IFS= read -r pid; do
        [ -n "$pid" ] || continue
        if _is_zharness_pid "$pid"; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done < <(_port_pids "$FRONTEND_PORT")
}

# Tear everything down. / 统一清理全部服务。
cleanup() {
    local status="${1:-0}"
    trap - EXIT INT TERM
    printf '\nStopping ZHarness...\n'
    stop_frontend
    stop_nginx
    "${REPO_ROOT}/scripts/server.sh" stop || true
    # Reclaim any leftover service ports. / 回收任何残留的服务端口。
    _reclaim_port "$BACKEND_PORT" || true
    _reclaim_port "$FRONTEND_PORT" || true
    _reclaim_port "$NGINX_PORT" || true
    printf '✓ All services stopped\n'
    exit "$status"
}

print_gateway_urls() {
    printf 'Local gateway:   http://localhost:%s\n' "$NGINX_PORT"
    local address
    for address in $(hostname -I 2>/dev/null || true); do
        [[ "$address" == *:* ]] && continue
        printf 'Network gateway: http://%s:%s\n' "$address" "$NGINX_PORT"
    done
}

start_development() {
    load_config
    _preflight
    mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

    trap 'cleanup 130' INT
    trap 'cleanup 143' TERM
    trap 'cleanup 0' EXIT

    printf '\n==========================================\n'
    printf '  Starting ZHarness\n'
    printf '==========================================\n'
    printf '  Backend     → %s:%s  (LangGraph server)\n' "$BACKEND_HOST" "$BACKEND_PORT"
    printf '  Frontend    → localhost:%s  (Next.js)\n' "$FRONTEND_PORT"
    printf '  Nginx       → localhost:%s  (reverse proxy)\n' "$NGINX_PORT"
    printf '  Logs        → %s\n' "$LOG_DIR"
    printf '\n'

    # 1. Backend: managed PostgreSQL + LangGraph server (background, PID file).
    # / 后端：托管 PostgreSQL + LangGraph 服务（后台运行，记录 PID）。
    if ! "${REPO_ROOT}/scripts/server.sh" start; then
        printf '✗ Backend failed to start; tail of %s:\n' "$SERVER_LOG" >&2
        tail -20 "$SERVER_LOG" >&2
        cleanup 1
    fi
    printf '✓ Backend started on %s:%s\n' "$BACKEND_HOST" "$BACKEND_PORT"

    # 2. Frontend. / 前端。
    run_service "Frontend" \
        "pnpm --dir frontend dev" \
        "$FRONTEND_PORT" 120 "$LOG_DIR/frontend.log"

    # 3. Nginx gateway. / Nginx 网关。
    run_service "Nginx" \
        "nginx -p '${REPO_ROOT}/' -c '${NGINX_CONFIG}'" \
        "$NGINX_PORT" 10 "$LOG_DIR/nginx.log"

    printf '\n==========================================\n'
    printf '  ✓ ZHarness is running!\n'
    printf '==========================================\n'
    print_gateway_urls
    printf '\n  Press Ctrl+C to stop all services\n'
    printf '\n'
    wait
}

stop_development() {
    cleanup 0
}

case "${1:-start}" in
    start)
        start_development
        ;;
    stop)
        stop_development
        ;;
    *)
        printf 'Usage: %s {start|stop}\n' "$0" >&2
        exit 2
        ;;
esac