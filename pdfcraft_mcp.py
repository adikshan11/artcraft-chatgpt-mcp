import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel


PDFCRAFT_ROOT = Path.home() / "Documents"
PDFCRAFT_CLI_PATH = (
    Path.home() / ".local" / "share" / "pdfcraft-cli" / "0.4.0" / "pdfcraft-cli"
)


class PdfCraftResult(BaseModel):
    message: str
    path: str | None = None
    data: dict[str, Any]


def _pdf_path(path: str, *, output: bool = False) -> Path:
    root = PDFCRAFT_ROOT.resolve()
    requested = Path(path).expanduser()
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=not output)
    except (OSError, RuntimeError) as exc:
        raise ToolError(f"PdfCraft could not resolve the requested path: {path}") from exc

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ToolError("PdfCraft paths must stay inside ~/Documents.") from exc

    if output:
        if candidate.exists() or candidate.is_symlink():
            raise ToolError(f"PdfCraft will not overwrite an existing path: {resolved}")
        if resolved.suffix.lower() != ".pdf":
            raise ToolError("PdfCraft output paths must use the .pdf extension.")
        if not resolved.parent.is_dir():
            raise ToolError("The output folder must already exist inside ~/Documents.")
    else:
        if not resolved.is_file():
            raise ToolError(f"PDF not found: {resolved}")
        if resolved.suffix.lower() != ".pdf":
            raise ToolError("Input files must use the .pdf extension.")

    return resolved


async def _run_pdfcraft(
    workflow: Callable[
        [ClientSession],
        Awaitable[tuple[Any | None, str | None]],
    ],
) -> Any:
    cli_path = Path(os.environ.get("PDFCRAFT_CLI_PATH", PDFCRAFT_CLI_PATH))
    if not cli_path.is_file() or not os.access(cli_path, os.X_OK):
        raise ToolError(
            f"PdfCraft CLI not found or not executable at {cli_path}. "
            "Install the matching PdfCraft CLI or set PDFCRAFT_CLI_PATH."
        )

    parameters = StdioServerParameters(
        command=str(cli_path),
        args=["mcp", "--root", str(PDFCRAFT_ROOT.resolve())],
    )
    value: Any | None = None
    failure: str | None = None

    try:
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                value, failure = await workflow(session)
    except OSError as exc:
        raise ToolError(f"Could not start PdfCraft CLI: {exc}") from exc

    if failure:
        raise ToolError(f"PdfCraft operation failed: {failure}")
    return value


async def _invoke(
    session: ClientSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    result = await session.call_tool(tool_name, arguments)
    if result.is_error:
        details = "\n".join(
            item.text for item in result.content if hasattr(item, "text")
        )
        return None, details or f"PdfCraft tool {tool_name} failed."

    if result.structured_content is not None:
        return result.structured_content, None
    return {
        "content": [
            item.text for item in result.content if hasattr(item, "text")
        ]
    }, None


def _document_id(result: dict[str, Any]) -> int | None:
    document_id = result.get("doc")
    if isinstance(document_id, int) and not isinstance(document_id, bool):
        return document_id
    return None


def register_pdfcraft_tools(server: MCPServer) -> None:
    @server.tool(
        title="Inspect a PDF with PdfCraft",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=True,
    )
    async def inspect_pdf_document(path: str) -> PdfCraftResult:
        """Read page count, metadata, page sizes, bookmarks, forms, and PDF properties.

        Provide a PDF path relative to ~/Documents or an absolute path inside ~/Documents.
        """
        pdf = _pdf_path(path)

        async def workflow(session: ClientSession):
            opened, error = await _invoke(session, "doc_open", {"path": str(pdf)})
            if error:
                return None, error
            document_id = _document_id(opened or {})
            if document_id is None:
                return None, "PdfCraft opened the PDF but returned no document id."
            properties, error = await _invoke(
                session, "doc_info", {"doc": document_id}
            )
            if error:
                return None, error
            return PdfCraftResult(
                message="PDF inspected.",
                path=str(pdf),
                data={"document": opened, "properties": properties},
            ), None

        return await _run_pdfcraft(workflow)

    @server.tool(
        title="Extract PDF text with PdfCraft",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=True,
    )
    async def extract_pdf_text(path: str, pages: list[int] | None = None) -> PdfCraftResult:
        """Extract text from all pages or selected 1-based pages of a PDF in ~/Documents."""
        pdf = _pdf_path(path)
        arguments: dict[str, Any] = {}
        if pages is not None:
            _validate_pages(pages)
            arguments["pages"] = pages

        async def workflow(session: ClientSession):
            opened, error = await _invoke(session, "doc_open", {"path": str(pdf)})
            if error:
                return None, error
            document_id = _document_id(opened or {})
            if document_id is None:
                return None, "PdfCraft opened the PDF but returned no document id."
            extracted, error = await _invoke(
                session, "text_extract", {"doc": document_id, **arguments}
            )
            if error:
                return None, error
            return PdfCraftResult(
                message="PDF text extracted.",
                path=str(pdf),
                data=extracted or {},
            ), None

        return await _run_pdfcraft(workflow)

    @server.tool(
        title="Search PDF text with PdfCraft",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=True,
    )
    async def search_pdf_text(path: str, query: str, limit: int = 100) -> PdfCraftResult:
        """Find matching text in a PDF in ~/Documents and return its page locations."""
        if not query.strip():
            raise ToolError("Search query must not be empty.")
        if not 1 <= limit <= 500:
            raise ToolError("Search limit must be between 1 and 500.")
        pdf = _pdf_path(path)

        async def workflow(session: ClientSession):
            opened, error = await _invoke(session, "doc_open", {"path": str(pdf)})
            if error:
                return None, error
            document_id = _document_id(opened or {})
            if document_id is None:
                return None, "PdfCraft opened the PDF but returned no document id."
            matches, error = await _invoke(
                session,
                "text_find",
                {"doc": document_id, "query": query, "limit": limit},
            )
            if error:
                return None, error
            return PdfCraftResult(
                message="PDF search completed.",
                path=str(pdf),
                data=matches or {},
            ), None

        return await _run_pdfcraft(workflow)

    @server.tool(
        title="Combine PDFs with PdfCraft",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    async def combine_pdf_documents(paths: list[str], output_path: str) -> PdfCraftResult:
        """Combine two or more PDFs from ~/Documents into a new PDF there.

        The output must not already exist. Source files are left unchanged.
        """
        if len(paths) < 2:
            raise ToolError("Provide at least two PDFs to combine.")
        pdfs = [_pdf_path(path) for path in paths]
        output = _pdf_path(output_path, output=True)

        async def workflow(session: ClientSession):
            combined, error = await _invoke(
                session,
                "doc_combine",
                {"paths": [str(pdf) for pdf in pdfs], "out": str(output)},
            )
            if error:
                return None, error
            return PdfCraftResult(
                message="PDFs combined into a new file.",
                path=str(output),
                data=combined or {},
            ), None

        return await _run_pdfcraft(workflow)

    @server.tool(
        title="Extract PDF pages with PdfCraft",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    async def extract_pdf_pages(
        path: str,
        pages: list[int],
        output_path: str,
    ) -> PdfCraftResult:
        """Copy selected 1-based pages from a PDF to a new PDF in ~/Documents."""
        _validate_pages(pages)
        pdf = _pdf_path(path)
        output = _pdf_path(output_path, output=True)

        async def workflow(session: ClientSession):
            opened, error = await _invoke(session, "doc_open", {"path": str(pdf)})
            if error:
                return None, error
            document_id = _document_id(opened or {})
            if document_id is None:
                return None, "PdfCraft opened the PDF but returned no document id."
            extracted, error = await _invoke(
                session,
                "page_extract",
                {
                    "doc": document_id,
                    "pages": pages,
                    "out": str(output),
                    "open": False,
                },
            )
            if error:
                return None, error
            return PdfCraftResult(
                message="Selected pages saved to a new PDF.",
                path=str(output),
                data=extracted or {},
            ), None

        return await _run_pdfcraft(workflow)

    @server.tool(
        title="Rotate PDF pages with PdfCraft",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    async def rotate_pdf_pages(
        path: str,
        pages: list[int],
        degrees: int,
        output_path: str,
    ) -> PdfCraftResult:
        """Rotate selected 1-based pages and save the result as a new PDF in ~/Documents."""
        _validate_pages(pages)
        if degrees not in {-270, -180, -90, 90, 180, 270}:
            raise ToolError("Rotation must be -270, -180, -90, 90, 180, or 270 degrees.")
        pdf = _pdf_path(path)
        output = _pdf_path(output_path, output=True)

        async def workflow(session: ClientSession):
            opened, error = await _invoke(session, "doc_open", {"path": str(pdf)})
            if error:
                return None, error
            document_id = _document_id(opened or {})
            if document_id is None:
                return None, "PdfCraft opened the PDF but returned no document id."
            _, error = await _invoke(
                session,
                "page_rotate",
                {"doc": document_id, "pages": pages, "degrees": degrees},
            )
            if error:
                return None, error
            saved, error = await _invoke(
                session,
                "doc_save",
                {"doc": document_id, "path": str(output)},
            )
            if error:
                return None, error
            return PdfCraftResult(
                message="Rotated pages saved to a new PDF.",
                path=str(output),
                data=saved or {},
            ), None

        return await _run_pdfcraft(workflow)


def _validate_pages(pages: list[int]) -> None:
    if not pages or any(
        not isinstance(page, int) or isinstance(page, bool) or page < 1
        for page in pages
    ):
        raise ToolError("Provide one or more positive 1-based page numbers.")
    if len(set(pages)) != len(pages):
        raise ToolError("Page numbers must not contain duplicates.")
