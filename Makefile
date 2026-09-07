SHELL := /bin/bash

.DEFAULT_GOAL := help

.PHONY: help dev up backend-dev backend-start backend-stop start stop restart status logs frontend-dev frontend-stop frontend-build postgres-start postgres-stop postgres-logs clean clean-dry-run

help: ## Show available commands. / 显示可用命令。
	@awk 'BEGIN {FS = ":.*## "; printf "Usage / 用法: make <target>\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev: ## Start the backend and frontend together. / 同时启动后端和前端。
	@./scripts/dev.sh

up: dev ## Alias for dev. / dev 的别名。

backend-dev: ## Start only the backend in the foreground. / 仅在前台启动后端。
	@./scripts/server.sh dev

backend-start: ## Start only the backend in the background. / 仅在后台启动后端。
	@./scripts/server.sh start

start: backend-start ## Backward-compatible backend-start alias. / 兼容旧用法的 backend-start 别名。

stop: ## Stop the frontend, backend, and PostgreSQL. / 停止前端、后端和 PostgreSQL。
	@./scripts/dev.sh stop

backend-stop: ## Stop only the backend and managed PostgreSQL. / 仅停止后端和托管 PostgreSQL。
	@./scripts/server.sh stop

restart: ## Restart only the background backend. / 仅重启后台后端。
	@./scripts/server.sh restart

status: ## Show the backend status. / 显示后端状态。
	@./scripts/server.sh status

logs: ## Follow backend logs. / 持续查看后端日志。
	@./scripts/server.sh logs

frontend-dev: ## Start the frontend development server. / 启动前端开发服务器。
	@pnpm --dir frontend dev

frontend-stop: ## Stop the frontend, Nginx gateway, backend, and PostgreSQL. / 停止前端、Nginx 网关、后端和 PostgreSQL。
	@./scripts/dev.sh stop

frontend-build: ## Build the frontend for production. / 构建生产版前端。
	@pnpm --dir frontend build

postgres-start: ## Start the managed PostgreSQL service. / 启动托管的 PostgreSQL 服务。
	@./scripts/server.sh postgres-start

postgres-stop: ## Stop the managed PostgreSQL service. / 停止托管的 PostgreSQL 服务。
	@./scripts/server.sh postgres-stop

postgres-logs: ## Follow managed PostgreSQL logs. / 持续查看托管的 PostgreSQL 日志。
	@./scripts/server.sh postgres-logs

clean: stop ## Stop the server and remove runtime data. / 停止服务并清理运行数据。
	@uv run --package zharness python scripts/cleanup.py -y

clean-dry-run: ## Preview the runtime data cleanup. / 预览将清理的运行数据。
	@uv run --package zharness python scripts/cleanup.py --dry-run
