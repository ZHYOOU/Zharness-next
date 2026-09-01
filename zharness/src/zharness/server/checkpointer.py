"""PostgreSQL-backed LangGraph checkpoint lifecycle. / PostgreSQL 支持的 LangGraph 检查点生命周期。"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_POSTGRES_URI_ENV = "ZHARNESS_POSTGRES_URI"
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_STRICT_SERIALIZER = JsonPlusSerializer(allowed_msgpack_modules=None)


def _postgres_uri() -> str:
    """Resolve an explicit or managed-Compose PostgreSQL URI. / 解析显式或由 Compose 托管的 PostgreSQL URI。"""

    postgres_uri = os.environ.get(_POSTGRES_URI_ENV, "").strip()
    if postgres_uri:
        return postgres_uri

    managed = os.environ.get("ZHARNESS_POSTGRES_MANAGED", "true").lower()
    if managed in _FALSE_VALUES:
        raise RuntimeError(f"{_POSTGRES_URI_ENV} is required")

    user = quote(os.environ.get("ZHARNESS_POSTGRES_USER", "zharness"), safe="")
    password = quote(
        os.environ.get("ZHARNESS_POSTGRES_PASSWORD", "change-me"),
        safe="",
    )
    database = quote(os.environ.get("ZHARNESS_POSTGRES_DB", "zharness"), safe="")
    port = os.environ.get("ZHARNESS_POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@127.0.0.1:{port}/{database}"


@asynccontextmanager
async def create_postgres_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """Create and initialize the server-wide PostgreSQL checkpointer. / 创建并初始化服务器级 PostgreSQL 检查点存储。"""

    async with AsyncPostgresSaver.from_conn_string(
        _postgres_uri(),
        serde=_STRICT_SERIALIZER,
    ) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
