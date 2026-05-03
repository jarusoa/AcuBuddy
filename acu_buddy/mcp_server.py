"""MCP server exposing AcuBuddy hybrid retrieval as tools.

Run via stdio (the standard transport every MCP client supports):

    python -m acu_buddy.mcp_server

Tools:
    - search_docs(query, doc_type?, area?, k?=5)
    - find_code_samples(query, k?=5)            (filters by area=customization|framework)
    - get_section(source_name, section)
    - list_doc_sources(area?, doc_type?)

The model can call these multiple times per turn — that's the whole point.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from acu_buddy.rag import (
    HybridIndex,
    get_section_text,
    list_sources,
    load_index,
    search,
)

INDEX_DIR = os.getenv(
    "ACUBUDDY_INDEX_DIR",
    str(Path(__file__).resolve().parent.parent / "chroma_db"),
)
DEFAULT_K = int(os.getenv("ACUBUDDY_SEARCH_K", "5"))
MAX_K = 20

mcp = FastMCP("acubuddy")

_index: HybridIndex | None = None


def _get_index() -> HybridIndex:
    global _index
    if _index is None:
        _index = load_index(INDEX_DIR)
    return _index


def _result_payload(r) -> dict:
    return {
        "citation": r.citation(),
        "source_name": r.metadata.get("source_name"),
        "section": r.metadata.get("section"),
        "page_start": r.metadata.get("page_start"),
        "page_end": r.metadata.get("page_end"),
        "doc_type": r.metadata.get("doc_type"),
        "area": r.metadata.get("area"),
        "score": round(r.score, 4),
        "content": r.content,
    }


@mcp.tool()
def search_docs(
    query: str,
    doc_type: Optional[str] = None,
    area: Optional[str] = None,
    k: int = DEFAULT_K,
) -> list[dict]:
    """Hybrid search (BM25 + dense + cross-encoder rerank) over Acumatica docs.

    Args:
        query: Natural-language question or keyword query. Acumatica jargon
            (e.g. "PXSelectJoin", "PXFormulaAttribute") works well — BM25 catches
            exact terms, dense catches semantic intent.
        doc_type: Optional filter. One of: guide, reference, checklist, diagram.
        area: Optional filter. Examples: customization, framework, ar, ap, gl,
            ui, workflow, integration, orders, inventory, projects, manufacturing,
            implementation. Use list_doc_sources() to see all values present.
        k: Number of results (default 5, max 20).

    Returns: list of {citation, source_name, section, page_start, page_end,
        doc_type, area, score, content}.

    Tip: call this multiple times with different filters/queries to triangulate
    a complete answer rather than relying on one search.
    """
    k = max(1, min(k, MAX_K))
    results = search(_get_index(), query, k=k, doc_type=doc_type, area=area)
    return [_result_payload(r) for r in results]


@mcp.tool()
def find_code_samples(query: str, k: int = DEFAULT_K) -> list[dict]:
    """Search restricted to the developer-focused guides (customization,
    framework, integration, ui, plugin, mobile, workflow). Use when the
    question is about writing C# / DAC / graph extension code, not about
    end-user functionality.
    """
    k = max(1, min(k, MAX_K))
    code_areas = {
        "customization",
        "framework",
        "integration",
        "ui",
        "plugin",
        "mobile",
        "workflow",
    }
    index = _get_index()
    raw = search(index, query, k=k * 4)
    filtered = [r for r in raw if r.metadata.get("area") in code_areas]
    return [_result_payload(r) for r in filtered[:k]]


@mcp.tool()
def get_section(source_name: str, section: str) -> dict:
    """Fetch the full text of a single section, concatenating all its chunks.

    Use this after search_docs returns a partial chunk and you want the whole
    section for context. source_name is the PDF filename; section is the title
    as it appears in citations (e.g. "Defining a Cache Extension").

    Returns: {source_name, section, found, content}.
    """
    text = get_section_text(_get_index(), source_name, section)
    return {
        "source_name": source_name,
        "section": section,
        "found": text is not None,
        "content": text or "",
    }


@mcp.tool()
def list_doc_sources(
    area: Optional[str] = None,
    doc_type: Optional[str] = None,
) -> list[dict]:
    """Enumerate every indexed source file with its metadata and section list.

    Use this to discover what areas/doc_types exist before filtering, or to
    find which guide covers a topic by browsing section titles.

    Returns: list of {source_name, doc_type, area, sections, chunk_count}.
    """
    rows = list_sources(_get_index())
    if area:
        rows = [r for r in rows if r["area"] == area]
    if doc_type:
        rows = [r for r in rows if r["doc_type"] == doc_type]
    return rows


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
