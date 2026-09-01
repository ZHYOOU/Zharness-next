SHELL := /bin/bash

.DEFAULT_GOAL := help

.PHONY: help dev start stop restart status logs postgres-start postgres-stop postgres-logs clean clean-dry-run

help: ## Show available commands. / 显示可用命令。
	@awk 'BEGIN {FS = ":.*## "; printf "Usage / 用法: make <target>\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

dev: ## Start the development server in the foreground. / 在前台启动开发服务。
	@./scripts/server.sh dev

start: ## Start the development server in the background. / 在后台启动开发服务。
	@./scripts/server.sh start

stop: ## Stop the background development server. / 停止后台开发服务。
	@./scripts/server.sh stop

restart: ## Restart the development server. / 重启开发服务。
	@./scripts/server.sh restart

status: ## Show the development server status. / 显示开发服务状态。
	@./scripts/server.sh status

logs: ## Follow development server logs. / 持续查看开发服务日志。
	@./scripts/server.sh logs

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
