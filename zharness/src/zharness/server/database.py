"""Shared PostgreSQL connection helpers for ZHarness services. / ZHarness 服务共用的 PostgreSQL 连接工具。"""

from __future__ import annotations

from urllib.parse import quote

from zharness.config.loader import get_settings

_POSTGRES_URI_ENV = "ZHARNESS_POSTGRES_URI"


def postgres_uri() -> str:
    """Resolve an explicit or managed-Compose PostgreSQL URI. / 解析显式或由 Compose 托管的 PostgreSQL URI。"""

    postgres = get_settings().postgres
    postgres_uri = postgres.uri or ""
    if postgres_uri:
        return postgres_uri

    if not postgres.managed:
        raise RuntimeError(f"{_POSTGRES_URI_ENV} is required")

    user = quote(postgres.user, safe="")
    password = quote(postgres.password, safe="")
    database = quote(postgres.database, safe="")
    return f"postgresql://{user}:{password}@127.0.0.1:{postgres.port}/{database}"
