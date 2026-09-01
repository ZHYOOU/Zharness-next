"""PostgreSQL-backed LangGraph checkpoint lifecycle. / PostgreSQL 支持的 LangGraph 检查点生命周期。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from zharness.config.loader import get_settings

_POSTGRES_URI_ENV = "ZHARNESS_POSTGRES_URI"
_STRICT_SERIALIZER = JsonPlusSerializer(allowed_msgpack_modules=None)


def _postgres_uri() -> str:
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


@asynccontextmanager
async def create_postgres_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """Create and initialize the server-wide PostgreSQL checkpointer. / 创建并初始化服务器级 PostgreSQL 检查点存储。"""

    async with AsyncPostgresSaver.from_conn_string(
        _postgres_uri(),
        serde=_STRICT_SERIALIZER,
    ) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
