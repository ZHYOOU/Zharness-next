import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zharness.host.paths import THREAD_ID_PATTERN, WorkspacePathError
from zharness.sandbox.manager import (
    SandboxUnavailableError,
    get_sandbox_manager,
)

logger = logging.getLogger(__name__)

_THREAD_DELETE_PATH = re.compile(rf"(?:^|/)threads/(?P<thread_id>{THREAD_ID_PATTERN})$")


class ThreadSandboxCleanupMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope.get("method") != "DELETE":
            await self.app(scope, receive, send)
            return

        match = _THREAD_DELETE_PATH.fullmatch(scope["path"])
        if match is None:
            await self.app(scope, receive, send)
            return

        thread_id = match.group("thread_id")

        async def capture_status(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 204:
                try:
                    await asyncio.to_thread(
                        get_sandbox_manager().remove_for_thread,
                        thread_id,
                    )
                except (SandboxUnavailableError, WorkspacePathError):
                    logger.exception(
                        "Failed to remove sandbox for deleted thread %s",
                        thread_id,
                    )
            await send(message)

        await self.app(scope, receive, capture_status)


@asynccontextmanager
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    """Stop sandboxes during graceful shutdown. / 在优雅关闭期间停止全部沙箱。"""

    try:
        yield
    finally:
        try:
            stopped = await asyncio.to_thread(get_sandbox_manager().stop_all)
            logger.info("Stopped %d sandbox containers during shutdown", len(stopped))
        except SandboxUnavailableError:
            logger.exception("Failed to stop sandbox containers during shutdown")


app = Starlette(lifespan=lifespan)
app.add_middleware(ThreadSandboxCleanupMiddleware)
