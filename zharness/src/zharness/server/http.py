import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zharness.host.paths import THREAD_ID_PATTERN, WorkspacePathError
from zharness.sandbox.manager import (
    SandboxUnavailableError,
    get_sandbox_manager,
)

logger = logging.getLogger(__name__)

_THREAD_DELETE_PATH = re.compile(rf"(?:^|/)threads/(?P<thread_id>{THREAD_ID_PATTERN})$")


async def _sandbox_cleanup_loop(manager: Any, stop: asyncio.Event) -> None:
    """Periodically prune idle and over-limit Docker sandboxes. / 定期清理空闲及超出数量限制的 Docker 沙箱。"""

    interval = manager.settings.cleanup_interval_seconds
    while not stop.is_set():
        try:
            removed = await asyncio.to_thread(manager.prune)
            if removed:
                logger.info("Pruned %d sandbox containers", len(removed))
        except SandboxUnavailableError:
            logger.exception("Failed to prune sandbox containers")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


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
    """Run periodic sandbox cleanup and remove containers on shutdown. / 运行沙箱定期清理，并在关闭时删除容器。"""

    manager = get_sandbox_manager()
    cleanup_stop = asyncio.Event()
    cleanup_task = None
    if callable(getattr(manager, "prune", None)):
        cleanup_task = asyncio.create_task(_sandbox_cleanup_loop(manager, cleanup_stop))
    try:
        yield
    finally:
        cleanup_stop.set()
        if cleanup_task is not None:
            await cleanup_task
        try:
            removed = await asyncio.to_thread(manager.shutdown_all)
            logger.info(
                "Removed or stopped %d sandbox instances during shutdown",
                len(removed),
            )
        except SandboxUnavailableError:
            logger.exception("Failed to shut down sandbox instances")


app = Starlette(lifespan=lifespan)
app.add_middleware(ThreadSandboxCleanupMiddleware)
