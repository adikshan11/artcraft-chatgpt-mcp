import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ListToolsResult, ToolAnnotations
from pydantic import BaseModel


PHOTOCRAFT_ROOT = Path.home() / "Documents" / "ArtCraft"
PHOTOCRAFT_CLI_PATH = (
    Path.home() / ".local" / "share" / "artcraft-cli" / "photocraft" / "0.5.0"
    / "photocraft-cli"
)


class PhotoCraftToolList(BaseModel):
    app: str
    workspace: str
    tools: list[dict[str, Any]]


SessionOperation = Callable[[ClientSession], Awaitable[Any]]


@dataclass
class _BridgeRequest:
    operation: SessionOperation
    result: asyncio.Future[Any]


def _photocraft_parameters() -> StdioServerParameters:
    cli_path = Path(
        os.environ.get("PHOTOCRAFT_CLI_PATH", str(PHOTOCRAFT_CLI_PATH))
    ).expanduser()
    if not cli_path.is_file() or not os.access(cli_path, os.X_OK):
        raise ToolError(
            f"PhotoCraft CLI not found or not executable at {cli_path}. "
            "Install the matching PhotoCraft CLI or set PHOTOCRAFT_CLI_PATH."
        )

    try:
        root = PHOTOCRAFT_ROOT.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ToolError(
            f"PhotoCraft workspace does not exist: {PHOTOCRAFT_ROOT}"
        ) from exc
    if not root.is_dir():
        raise ToolError(f"PhotoCraft workspace is not a directory: {root}")

    return StdioServerParameters(
        command=str(cli_path),
        args=[
            "mcp",
            "--automation-read-root",
            str(root),
            "--automation-write-root",
            str(root),
        ],
        cwd=str(root),
    )


class PhotoCraftBridge:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._worker: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self._queue: asyncio.Queue[_BridgeRequest | None] | None = None

    async def run(self, operation: SessionOperation) -> Any:
        async with self._lock:
            if self._worker is None or self._worker.done():
                parameters = _photocraft_parameters()
                ready = asyncio.get_running_loop().create_future()
                queue: asyncio.Queue[_BridgeRequest | None] = asyncio.Queue()
                self._ready = ready
                self._queue = queue
                self._worker = asyncio.create_task(
                    self._serve(parameters, queue, ready)
                )

            worker = self._worker
            ready = self._ready
            queue = self._queue
            if ready is None or queue is None:
                raise RuntimeError("PhotoCraft bridge worker was not initialized.")

            try:
                await ready
            except Exception:
                await worker
                self._worker = None
                self._ready = None
                self._queue = None
                raise

            result = asyncio.get_running_loop().create_future()
            queue.put_nowait(_BridgeRequest(operation, result))
            return await result

    async def _serve(
        self,
        parameters: StdioServerParameters,
        queue: asyncio.Queue[_BridgeRequest | None],
        ready: asyncio.Future[None],
    ) -> None:
        try:
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    ready.set_result(None)
                    while (request := await queue.get()) is not None:
                        try:
                            value = await request.operation(session)
                        except Exception as exc:
                            if not request.result.done():
                                request.result.set_exception(exc)
                            raise
                        else:
                            if not request.result.done():
                                request.result.set_result(value)
        except Exception as exc:
            if not ready.done():
                ready.set_exception(
                    ToolError(f"Could not start PhotoCraft MCP server: {exc}")
                )
            else:
                self._fail_pending(
                    queue,
                    f"PhotoCraft MCP connection stopped: {exc}",
                )

    @staticmethod
    def _fail_pending(
        queue: asyncio.Queue[_BridgeRequest | None],
        message: str,
    ) -> None:
        while not queue.empty():
            request = queue.get_nowait()
            if request is not None and not request.result.done():
                request.result.set_exception(ToolError(message))

    async def list_tools(self) -> ListToolsResult:
        return await self.run(lambda session: session.list_tools())

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        return await self.run(
            lambda session: session.call_tool(tool_name, arguments)
        )

    async def close(self) -> None:
        async with self._lock:
            worker = self._worker
            queue = self._queue
            if worker is not None and not worker.done() and queue is not None:
                queue.put_nowait(None)
                await worker
            self._worker = None
            self._ready = None
            self._queue = None


_photocraft_bridge = PhotoCraftBridge()


@asynccontextmanager
async def craft_app_lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
    try:
        yield {}
    finally:
        await _photocraft_bridge.close()


def register_photocraft_tools(server: MCPServer) -> None:
    @server.tool(
        title="List PhotoCraft tools",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=True,
    )
    async def list_photocraft_tools() -> PhotoCraftToolList:
        """List PhotoCraft's native MCP tools and their input schemas.

        PhotoCraft can read and write files only inside ~/Documents/ArtCraft.
        Call this before using call_photocraft_tool to discover the native tool
        name and required arguments.
        """
        result = await _photocraft_bridge.list_tools()
        return PhotoCraftToolList(
            app="PhotoCraft",
            workspace=str(PHOTOCRAFT_ROOT.resolve()),
            tools=[
                tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                for tool in result.tools
            ],
        )

    @server.tool(
        title="Use a PhotoCraft tool",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def call_photocraft_tool(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        """Invoke a native PhotoCraft MCP tool inside ~/Documents/ArtCraft.

        First call list_photocraft_tools to find a valid tool name and its
        argument schema. Treat this as a write-capable operation because the
        native PhotoCraft tools can modify documents and the editor UI.
        """
        if not tool_name.strip():
            raise ToolError("PhotoCraft tool name must not be empty.")

        available_tools = await _photocraft_bridge.list_tools()
        if tool_name not in {tool.name for tool in available_tools.tools}:
            raise ToolError(
                f"Unknown PhotoCraft tool {tool_name!r}; "
                "call list_photocraft_tools to see available tools."
            )
        return await _photocraft_bridge.call_tool(tool_name, arguments)
