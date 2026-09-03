"""PostgreSQL-backed LangGraph checkpoint lifecycle. / PostgreSQL 支持的 LangGraph 检查点生命周期。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from zharness.server.database import postgres_uri as _postgres_uri

_STRICT_SERIALIZER = JsonPlusSerializer(allowed_msgpack_modules=None)


@asynccontextmanager
async def create_postgres_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """Create and initialize the server-wide PostgreSQL checkpointer. / 创建并初始化服务器级 PostgreSQL 检查点存储。"""

    async with AsyncPostgresSaver.from_conn_string(
        _postgres_uri(),
        serde=_STRICT_SERIALIZER,
    ) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
