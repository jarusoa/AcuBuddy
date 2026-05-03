"""MCP server exposing AcuBuddy hybrid retrieval and project catalog as tools.

Run via stdio (the standard transport every MCP client supports):

    python -m acu_buddy.mcp_server

Doc tools:
    - search_docs(query, doc_type?, area?, k?=5)
    - find_code_samples(query, k?=5)
    - get_section(source_name, section)
    - list_doc_sources(area?, doc_type?)

Project tools (require ACUBUDDY_PROJECT_ROOT):
    - reindex_project()
    - find_dac(name, fuzzy?=True)
    - list_dac_fields(dac_name)
    - find_dac_extensions(dac_name)
    - find_graph_extensions(graph_name)
    - find_event_handlers(target_dac, kind?, field?)
    - search_project(query, file_glob?, max_hits?=50)
    - read_project_file(path, start_line?, end_line?)

Validation tool:
    - validate_csharp(code)         (catalog-aware static checks)

The model can call these multiple times per turn — that's the whole point.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from acu_buddy.project_indexer import (
    CATALOG_FILENAME,
    ProjectCatalog,
    build_catalog,
    load_catalog,
    read_file_safely,
    save_catalog,
    search_text,
)
from acu_buddy.rag import (
    HybridIndex,
    get_section_text,
    list_sources,
    load_index,
    search,
)
from acu_buddy.validator import summarize as _summarize_issues
from acu_buddy.validator import validate as _run_validate

INDEX_DIR = os.getenv(
    "ACUBUDDY_INDEX_DIR",
    str(Path(__file__).resolve().parent.parent / "chroma_db"),
)
PROJECT_ROOT = os.getenv("ACUBUDDY_PROJECT_ROOT", "")
DEFAULT_K = int(os.getenv("ACUBUDDY_SEARCH_K", "5"))
MAX_K = 20

mcp = FastMCP("acubuddy")

_index: HybridIndex | None = None
_catalog: ProjectCatalog | None = None


def _get_index() -> HybridIndex:
    global _index
    if _index is None:
        _index = load_index(INDEX_DIR)
    return _index


def _catalog_path() -> str:
    return os.path.join(INDEX_DIR, CATALOG_FILENAME)


def _require_project_root() -> str:
    if not PROJECT_ROOT or not os.path.isdir(PROJECT_ROOT):
        raise RuntimeError(
            "ACUBUDDY_PROJECT_ROOT is not set or not a directory. Set it to the "
            "absolute path of your customization project's source folder."
        )
    return PROJECT_ROOT


def _get_catalog() -> ProjectCatalog:
    global _catalog
    if _catalog is not None:
        return _catalog
    cat_path = _catalog_path()
    if os.path.isfile(cat_path):
        _catalog = load_catalog(cat_path)
        return _catalog
    root = _require_project_root()
    _catalog = build_catalog(root)
    save_catalog(_catalog, cat_path)
    return _catalog


def _dac_to_dict(d) -> dict:
    return {
        "name": d.name,
        "kind": d.kind,
        "extends": d.extends,
        "file": d.file,
        "line": d.line,
        "field_count": len(d.fields),
    }


def _event_to_dict(e) -> dict:
    return asdict(e)


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


@mcp.tool()
def reindex_project() -> dict:
    """Rebuild the structured catalog from ACUBUDDY_PROJECT_ROOT.

    Run this after editing your customization project's source files so the
    other project tools see the latest DACs/graphs/events. The catalog is
    persisted to <index_dir>/project_catalog.json.

    Returns: {project_root, file_count, dacs, graphs, events, built_at}.
    """
    global _catalog
    root = _require_project_root()
    _catalog = build_catalog(root)
    save_catalog(_catalog, _catalog_path())
    return {
        "project_root": _catalog.project_root,
        "built_at": _catalog.built_at,
        "file_count": _catalog.file_count,
        "dacs": len(_catalog.dacs),
        "graphs": len(_catalog.graphs),
        "events": len(_catalog.events),
    }


@mcp.tool()
def find_dac(name: str, fuzzy: bool = True) -> list[dict]:
    """Look up a DAC or DAC extension by class name (case-insensitive).

    Returns matching DACs/extensions with their file location and field count.
    If `fuzzy` is true and no exact match, returns close matches by name.
    Use list_dac_fields(name) to enumerate fields on a specific DAC.
    """
    cat = _get_catalog()
    needle = name.lower()
    exact = [d for d in cat.dacs if d.name.lower() == needle]
    if exact or not fuzzy:
        return [_dac_to_dict(d) for d in exact]

    all_names = [d.name for d in cat.dacs]
    close = difflib.get_close_matches(name, all_names, n=10, cutoff=0.6)
    contains = [d for d in cat.dacs if needle in d.name.lower() and d.name not in close]
    matched = [d for d in cat.dacs if d.name in close] + contains[:10]
    return [_dac_to_dict(d) for d in matched]


@mcp.tool()
def list_dac_fields(dac_name: str) -> dict:
    """Enumerate every `public virtual` field on a DAC or DAC extension.

    Returns each field's type, attributes, and source line. This is the
    exhaustive answer (vector search misses fields outside the top-k chunks).
    """
    cat = _get_catalog()
    matches = [d for d in cat.dacs if d.name.lower() == dac_name.lower()]
    if not matches:
        return {"dac": dac_name, "found": False, "fields": []}
    out_fields = []
    for d in matches:
        for f in d.fields:
            out_fields.append(
                {
                    "name": f.name,
                    "type": f.type,
                    "attributes": f.attributes,
                    "in_class": d.name,
                    "kind": d.kind,
                    "extends": d.extends,
                    "file": d.file,
                    "line": f.line,
                }
            )
    return {
        "dac": matches[0].name,
        "found": True,
        "kind": matches[0].kind,
        "extends": matches[0].extends,
        "fields": out_fields,
    }


@mcp.tool()
def find_dac_extensions(dac_name: str) -> list[dict]:
    """List every PXCacheExtension targeting a given DAC.

    Use this to answer "what extensions exist on ARInvoice?" — vector search
    can return ~5 fuzzy matches; this returns all of them.
    """
    cat = _get_catalog()
    needle = dac_name.lower()
    matches = [
        d
        for d in cat.dacs
        if d.kind == "dac_extension" and d.extends and d.extends.split(".")[-1].lower() == needle
    ]
    return [_dac_to_dict(d) for d in matches]


@mcp.tool()
def find_graph_extensions(graph_name: str) -> list[dict]:
    """List every PXGraphExtension targeting a given graph (e.g. ARInvoiceEntry)."""
    cat = _get_catalog()
    needle = graph_name.lower()
    matches = [
        g
        for g in cat.graphs
        if g.kind == "graph_extension" and g.extends and g.extends.split(".")[-1].lower() == needle
    ]
    return [
        {
            "name": g.name,
            "extends": g.extends,
            "file": g.file,
            "line": g.line,
        }
        for g in matches
    ]


@mcp.tool()
def find_event_handlers(
    target_dac: str,
    kind: Optional[str] = None,
    field: Optional[str] = None,
) -> list[dict]:
    """List event handlers that target a DAC, optionally filtered by event kind
    (RowSelected/FieldUpdated/RowPersisting/...) and/or field name.

    Covers both modern (Events.RowSelected<DAC>) and legacy (DAC_Field_Kind)
    handler styles in one call.
    """
    cat = _get_catalog()
    dac_needle = target_dac.lower()
    out: list[dict] = []
    for e in cat.events:
        if e.target_dac.lower() != dac_needle:
            continue
        if kind and e.kind.lower() != kind.lower():
            continue
        if field and (e.target_field or "").lower() != field.lower():
            continue
        out.append(_event_to_dict(e))
    return out


@mcp.tool()
def search_project(query: str, file_glob: Optional[str] = None, max_hits: int = 50) -> list[dict]:
    """Substring search across the customization project's source files.

    Use this for free-form code questions (e.g. "where do we use
    PXFormulaAttribute?") that aren't covered by the structured tools.
    file_glob is an optional fnmatch pattern relative to the project root,
    e.g. "*.cs" or "Graphs/*.cs".
    """
    root = _require_project_root()
    return search_text(root, query, file_glob=file_glob, max_hits=max(1, min(max_hits, 500)))


@mcp.tool()
def read_project_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict:
    """Read a file from the customization project, optionally a line range
    (1-based, inclusive). Refuses paths outside ACUBUDDY_PROJECT_ROOT.
    """
    root = _require_project_root()
    return read_file_safely(root, path, start_line=start_line, end_line=end_line)


@mcp.tool()
def validate_csharp(code: str) -> dict:
    """Static-validate generated C# against the project catalog.

    Run this on every code block you produce *before* showing it to the user.
    It catches the most common failure modes without needing to compile:

      - errors:   field collisions on a DAC (you added a field that already
                  exists), event handlers referencing fields that don't
                  exist on a cataloged DAC.
      - warnings: class-name collisions with existing project classes.
      - notes:    references to DACs/graphs not in the catalog (might be
                  stock Acumatica types — verify the namespace).

    If errors are present, fix them and re-validate. If only notes remain,
    you're likely fine — just sanity-check the unknown targets.

    Returns: {ok, issue_counts, issues[{severity, kind, message, line, context}], summary}.

    Note: this is a static check, not a real compile. Type mismatches,
    syntax errors, and missing usings are not caught here.
    """
    catalog = None
    try:
        catalog = _get_catalog()
    except RuntimeError:
        pass
    issues = _run_validate(code, catalog)
    out = _summarize_issues(issues)
    if catalog is None:
        out["summary"] = "no project catalog loaded — limited checks. " + out["summary"]
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
