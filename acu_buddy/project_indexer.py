"""Project-aware indexer for Acumatica customization projects.

Walks a customization project (a folder of .cs files extracted from a
Customization Project) and builds a *structured catalog* of:

  - DACs (classes inheriting IBqlTable) with their fields and attributes
  - DAC extensions (PXCacheExtension<T>) with the target DAC and added fields
  - Graphs (PXGraph<...>) with their primary DAC if declared
  - Graph extensions (PXGraphExtension<T>) with the target graph
  - Event handlers in either the modern Events.* style or the legacy
    DAC_Field_Kind style

The catalog is JSON-serialised so the MCP server can answer "list every
DAC field" / "all extensions of ARInvoice" / "every event handler on
SOOrder.shipDate" *exhaustively* — vector search can't.

This is regex-based by design. Acumatica code is stylised enough that
this catches >95% of cases without a Roslyn dependency, and the parser
can be swapped for tree-sitter / Roslyn later behind the same data
shape.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _warn_walk_error(error: OSError) -> None:
    """os.walk callback — log directories we can't read and keep going.

    Acumatica's wwwroot has folders IIS holds locks on (Bin\\, log/cache
    dirs, sometimes App_Data) and ACL'd folders inside CstSrc that the
    current user can't list. Without this, os.walk silently skips them
    and you have no idea what was missed.
    """
    path = getattr(error, "filename", None) or "<unknown>"
    print(f"  SKIP {path}: {error.strerror or error}", file=sys.stderr)

CATALOG_VERSION = 1
CATALOG_FILENAME = "project_catalog.json"

# Comparison is case-insensitive (see _is_excluded_dir) so Windows folders
# like "Bin" or "App_Data" match these lowercase names.
#
# Note: only directory names that NEVER legitimately appear inside a user's
# customization source. We deliberately do NOT exclude "Pages" or "Frames",
# because those are common folder names some devs use for organising their
# own customizations. Multi-company users who point at wwwroot just get a
# slightly larger catalog, which is fine — entries are tagged by project.
EXCLUDE_DIRS = {
    # Build / VCS / dependency caches
    "obj",
    "bin",
    ".git",
    ".vs",
    "node_modules",
    "packages",
    # ASP.NET / Acumatica wwwroot infrastructure that is never user source
    "app_data",
    "app_code",
    "websitecache",
    "websitevalidation",
    "cstpublished",
}


def _is_excluded_dir(name: str) -> bool:
    return name.lower() in EXCLUDE_DIRS


def _resolve_project(file_path: Path, root: Path) -> str:
    """Identify which 'project' a given .cs file belongs to.

    Walks up from the file's directory toward `root`, picking the most
    specific project marker:
      1. The closest directory containing a .csproj file → use its name.
      2. A directory whose parent is named `CstSrc` (case-insensitive) →
         use the directory name (this is the Acumatica convention for
         unpacked customization sources).
      3. Fallback: the first path component below `root`, or the root's
         own basename if the file lives directly in `root`.
    """
    file_path = file_path.resolve()
    root = root.resolve()

    cur = file_path.parent
    while True:
        try:
            for entry in cur.iterdir():
                if entry.is_file() and entry.suffix.lower() == ".csproj":
                    return cur.name
        except OSError:
            pass
        if cur != root and cur.parent.name.lower() == "cstsrc":
            return cur.name
        if cur == root or cur == cur.parent:
            break
        cur = cur.parent

    try:
        rel = file_path.relative_to(root)
    except ValueError:
        return "(unknown)"
    parts = rel.parts[:-1]
    if parts:
        return parts[0]
    return root.name or "(root)"

EVENT_FIELD_KINDS = (
    "FieldSelecting",
    "FieldDefaulting",
    "FieldUpdated",
    "FieldUpdating",
    "FieldVerifying",
    "CommandPreparing",
    "ExceptionHandling",
)
EVENT_ROW_KINDS = (
    "RowSelected",
    "RowSelecting",
    "RowInserted",
    "RowInserting",
    "RowUpdated",
    "RowUpdating",
    "RowDeleted",
    "RowDeleting",
    "RowPersisted",
    "RowPersisting",
)

CLASS_DECL_RE = re.compile(
    r"\b(?:public|internal|private|protected)\s+"
    r"(?:partial\s+|sealed\s+|abstract\s+|static\s+|new\s+)*"
    r"class\s+(?P<name>\w+)"
    r"(?:\s*<[^>]*>)?"
    r"(?:\s*:\s*(?P<bases>[^{]+?))?"
    r"(?=\s*(?:\bwhere\b|[\{\r\n]))",
    re.MULTILINE,
)

FIELD_DECL_RE = re.compile(
    r"public\s+virtual\s+(?P<type>[\w\.\<\>\[\]\?, ]+?)\s+"
    r"(?P<name>\w+)\s*\{\s*get\s*;\s*set\s*;\s*\}"
)

ATTR_LINE_RE = re.compile(r"^\s*\[(?P<body>.+)\]\s*$")

EVENT_LEGACY_FIELD_RE = re.compile(
    r"(?:public|protected|private|internal)\s+(?:virtual\s+|override\s+|new\s+)*"
    r"\w+\s+"
    r"(?P<dac>\w+)_(?P<field>\w+)_(?P<kind>"
    + "|".join(EVENT_FIELD_KINDS)
    + r")\s*\("
)
EVENT_LEGACY_ROW_RE = re.compile(
    r"(?:public|protected|private|internal)\s+(?:virtual\s+|override\s+|new\s+)*"
    r"\w+\s+"
    r"(?P<dac>\w+)_(?P<kind>"
    + "|".join(EVENT_ROW_KINDS)
    + r")\s*\("
)
EVENT_MODERN_RE = re.compile(
    r"(?:public|protected|private|internal)\s+(?:virtual\s+|override\s+)*"
    r"\w+\s+_\s*\(\s*Events\.(?P<kind>\w+)\s*<\s*(?P<dac>[\w\.]+)"
    r"(?:\s*,\s*(?P<field_qualified>[\w\.]+))?\s*>\s*[\w]*\s+\w+\s*\)"
)


@dataclass
class FieldInfo:
    name: str
    type: str
    attributes: list[str]
    line: int


@dataclass
class DacInfo:
    name: str
    kind: str  # "dac" | "dac_extension"
    extends: str | None
    fields: list[FieldInfo] = field(default_factory=list)
    file: str = ""
    line: int = 0
    project: str = ""


@dataclass
class GraphInfo:
    name: str
    kind: str  # "graph" | "graph_extension"
    extends: str | None
    primary_dac: str | None
    file: str
    line: int
    project: str = ""


@dataclass
class EventInfo:
    enclosing_class: str
    kind: str
    target_dac: str
    target_field: str | None
    style: str  # "modern" | "legacy"
    file: str
    line: int
    project: str = ""


@dataclass
class ProjectCatalog:
    project_root: str
    built_at: str
    file_count: int
    dacs: list[DacInfo] = field(default_factory=list)
    graphs: list[GraphInfo] = field(default_factory=list)
    events: list[EventInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": CATALOG_VERSION,
            **asdict(self),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectCatalog":
        def _take(known_fields, raw):
            return {k: v for k, v in raw.items() if k in known_fields}

        dac_fields = {"name", "kind", "extends", "fields", "file", "line", "project"}
        graph_fields = {"name", "kind", "extends", "primary_dac", "file", "line", "project"}
        event_fields = {
            "enclosing_class",
            "kind",
            "target_dac",
            "target_field",
            "style",
            "file",
            "line",
            "project",
        }

        return cls(
            project_root=data["project_root"],
            built_at=data["built_at"],
            file_count=data["file_count"],
            dacs=[
                DacInfo(
                    **{
                        **_take(dac_fields, d),
                        "fields": [FieldInfo(**f) for f in d.get("fields", [])],
                    }
                )
                for d in data.get("dacs", [])
            ],
            graphs=[GraphInfo(**_take(graph_fields, g)) for g in data.get("graphs", [])],
            events=[EventInfo(**_take(event_fields, e)) for e in data.get("events", [])],
        )

    def projects(self) -> list[str]:
        names = set()
        for d in self.dacs:
            names.add(d.project)
        for g in self.graphs:
            names.add(g.project)
        for e in self.events:
            names.add(e.project)
        return sorted(n for n in names if n)


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for c in s:
        if c in "<([":
            depth += 1
        elif c in ">)]":
            depth -= 1
        if c == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return [x for x in out if x]


def _parse_bases(s: str) -> list[str]:
    s = re.sub(r"\bwhere\b.*", "", s, flags=re.DOTALL).strip()
    return _split_top_level(s)


def _line_at(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _find_class_body(text: str, after_decl_idx: int) -> tuple[int, int] | None:
    i = after_decl_idx
    while i < len(text) and text[i] != "{":
        i += 1
    if i >= len(text):
        return None

    body_start = i + 1
    depth = 1
    in_line_comment = in_block_comment = False
    in_str = in_char = in_verbatim = False
    j = body_start
    while j < len(text) and depth > 0:
        c = text[j]
        if in_line_comment:
            if c == "\n":
                in_line_comment = False
        elif in_block_comment:
            if c == "*" and j + 1 < len(text) and text[j + 1] == "/":
                in_block_comment = False
                j += 1
        elif in_verbatim:
            if c == '"':
                if j + 1 < len(text) and text[j + 1] == '"':
                    j += 1
                else:
                    in_verbatim = False
        elif in_str:
            if c == "\\" and j + 1 < len(text):
                j += 1
            elif c == '"':
                in_str = False
        elif in_char:
            if c == "\\" and j + 1 < len(text):
                j += 1
            elif c == "'":
                in_char = False
        else:
            if c == "/" and j + 1 < len(text):
                nxt = text[j + 1]
                if nxt == "/":
                    in_line_comment = True
                    j += 1
                elif nxt == "*":
                    in_block_comment = True
                    j += 1
                else:
                    pass
            elif c == "@" and j + 1 < len(text) and text[j + 1] == '"':
                in_verbatim = True
                j += 1
            elif c == '"':
                in_str = True
            elif c == "'":
                in_char = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return body_start, j
        j += 1
    return None


def _is_field_companion(bases: list[str]) -> bool:
    return any(".BQL." in b or ".Field<" in b or "BqlString.Field" in b for b in bases)


def _classify(bases: list[str]) -> tuple[str | None, str | None]:
    """Return (kind, target_or_primary).

    kind ∈ {"dac", "dac_extension", "graph", "graph_extension", None}.
    For dac_extension and graph_extension, second value is the target type name.
    For graph (PXGraph<Self, Primary>), second value is the primary DAC name (or None).
    """
    for b in bases:
        b_strip = b.strip()
        if b_strip == "IBqlTable" or b_strip.endswith(".IBqlTable"):
            return "dac", None
        m = re.match(r"PXCacheExtension\s*<\s*([^>]+)\s*>", b_strip)
        if m:
            args = _split_top_level(m.group(1))
            target = args[-1].strip() if args else None
            return "dac_extension", target
        m = re.match(r"PXGraphExtension\s*<\s*([^>]+)\s*>", b_strip)
        if m:
            args = _split_top_level(m.group(1))
            target = args[-1].strip() if args else None
            return "graph_extension", target
        m = re.match(r"PXGraph\s*<\s*([^>]+)\s*>", b_strip)
        if m:
            args = _split_top_level(m.group(1))
            primary = args[1].strip() if len(args) >= 2 else None
            return "graph", primary
    return None, None


def _parse_fields(body: str, body_offset: int, full_text: str) -> list[FieldInfo]:
    fields: list[FieldInfo] = []
    body_lines = body.split("\n")
    line_starts: list[int] = [0]
    for ln in body_lines[:-1]:
        line_starts.append(line_starts[-1] + len(ln) + 1)

    body_line_at_offset = lambda off: max(1, _line_at(full_text, body_offset + off))

    for m in FIELD_DECL_RE.finditer(body):
        type_str = m.group("type").strip()
        if type_str.endswith(",") or "," in type_str.split()[-1]:
            continue
        name = m.group("name")
        line_in_body = body[: m.start()].count("\n")
        attrs: list[str] = []
        for back in range(line_in_body - 1, -1, -1):
            stripped = body_lines[back].strip()
            if not stripped:
                continue
            am = ATTR_LINE_RE.match(body_lines[back])
            if not am:
                break
            attrs.insert(0, am.group("body").strip())
        fields.append(
            FieldInfo(
                name=name,
                type=type_str,
                attributes=attrs,
                line=body_line_at_offset(m.start()),
            )
        )
    return fields


def _parse_events(body: str, body_offset: int, full_text: str, enclosing: str, file_path: str) -> list[EventInfo]:
    events: list[EventInfo] = []

    def _line(off: int) -> int:
        return max(1, _line_at(full_text, body_offset + off))

    for m in EVENT_MODERN_RE.finditer(body):
        kind = m.group("kind")
        dac = m.group("dac").split(".")[-1]
        fq = m.group("field_qualified")
        target_field = fq.split(".")[-1] if fq else None
        events.append(
            EventInfo(
                enclosing_class=enclosing,
                kind=kind,
                target_dac=dac,
                target_field=target_field,
                style="modern",
                file=file_path,
                line=_line(m.start()),
            )
        )

    for m in EVENT_LEGACY_FIELD_RE.finditer(body):
        events.append(
            EventInfo(
                enclosing_class=enclosing,
                kind=m.group("kind"),
                target_dac=m.group("dac"),
                target_field=m.group("field"),
                style="legacy",
                file=file_path,
                line=_line(m.start()),
            )
        )

    for m in EVENT_LEGACY_ROW_RE.finditer(body):
        events.append(
            EventInfo(
                enclosing_class=enclosing,
                kind=m.group("kind"),
                target_dac=m.group("dac"),
                target_field=None,
                style="legacy",
                file=file_path,
                line=_line(m.start()),
            )
        )

    return events


def parse_text(
    text: str, file_label: str = "<input>"
) -> tuple[list[DacInfo], list[GraphInfo], list[EventInfo]]:
    """Parse C# source text. file_label populates the 'file' field on results."""
    dacs: list[DacInfo] = []
    graphs: list[GraphInfo] = []
    events: list[EventInfo] = []

    for m in CLASS_DECL_RE.finditer(text):
        bases_str = m.group("bases") or ""
        bases = _parse_bases(bases_str)
        if _is_field_companion(bases):
            continue

        kind, target = _classify(bases)
        if kind is None:
            continue

        body_range = _find_class_body(text, m.end())
        if body_range is None:
            continue
        body_start, body_end = body_range
        body = text[body_start:body_end]
        cls_name = m.group("name")
        cls_line = _line_at(text, m.start())

        if kind in ("dac", "dac_extension"):
            fields = _parse_fields(body, body_start, text)
            dacs.append(
                DacInfo(
                    name=cls_name,
                    kind=kind,
                    extends=target,
                    fields=fields,
                    file=file_label,
                    line=cls_line,
                )
            )
        elif kind in ("graph", "graph_extension"):
            primary = target if kind == "graph" else None
            extends = target if kind == "graph_extension" else None
            graphs.append(
                GraphInfo(
                    name=cls_name,
                    kind=kind,
                    extends=extends,
                    primary_dac=primary,
                    file=file_label,
                    line=cls_line,
                )
            )
            events.extend(_parse_events(body, body_start, text, cls_name, file_label))

    return dacs, graphs, events


def parse_file(
    path: str,
    project_root: str,
    project_cache: dict[str, str] | None = None,
) -> tuple[list[DacInfo], list[GraphInfo], list[EventInfo]]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], [], []
    rel = os.path.relpath(path, project_root)

    cache = project_cache if project_cache is not None else {}
    dir_key = os.path.dirname(path)
    project = cache.get(dir_key)
    if project is None:
        project = _resolve_project(Path(path), Path(project_root))
        cache[dir_key] = project

    dacs, graphs, events = parse_text(text, file_label=rel)
    for d in dacs:
        d.project = project
    for g in graphs:
        g.project = project
    for e in events:
        e.project = project
    return dacs, graphs, events


def build_catalog(project_root: str) -> ProjectCatalog:
    root = os.path.abspath(project_root)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Project root not found: {root}")

    dacs: list[DacInfo] = []
    graphs: list[GraphInfo] = []
    events: list[EventInfo] = []
    file_count = 0
    project_cache: dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root, onerror=_warn_walk_error):
        dirnames[:] = [d for d in dirnames if not _is_excluded_dir(d)]
        for fname in filenames:
            if not fname.endswith(".cs"):
                continue
            fpath = os.path.join(dirpath, fname)
            file_count += 1
            d, g, e = parse_file(fpath, root, project_cache)
            dacs.extend(d)
            graphs.extend(g)
            events.extend(e)

    return ProjectCatalog(
        project_root=root,
        built_at=_dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        file_count=file_count,
        dacs=dacs,
        graphs=graphs,
        events=events,
    )


def save_catalog(catalog: ProjectCatalog, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(catalog.to_dict(), indent=2), encoding="utf-8")


def load_catalog(path: str) -> ProjectCatalog:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProjectCatalog.from_dict(data)


def search_text(project_root: str, query: str, file_glob: str | None = None, max_hits: int = 100) -> list[dict]:
    """Substring search across project source files. Case-insensitive."""
    root = os.path.abspath(project_root)
    needle = query.lower()
    hits: list[dict] = []

    pattern_re = None
    if file_glob:
        import fnmatch
        pattern_re = re.compile(fnmatch.translate(file_glob), re.IGNORECASE)

    for dirpath, dirnames, filenames in os.walk(root, onerror=_warn_walk_error):
        dirnames[:] = [d for d in dirnames if not _is_excluded_dir(d)]
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, root)
            if pattern_re and not pattern_re.match(rel):
                continue
            try:
                text = Path(fpath).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    hits.append({"file": rel, "line": i, "text": line.strip()[:240]})
                    if len(hits) >= max_hits:
                        return hits
    return hits


def read_file_safely(project_root: str, rel_path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
    """Read a project file with optional 1-based line range. Refuses paths outside project_root."""
    root = os.path.abspath(project_root)
    target = os.path.abspath(os.path.join(root, rel_path))
    if not target.startswith(root + os.sep) and target != root:
        return {"error": "Path is outside the project root", "path": rel_path}
    if not os.path.isfile(target):
        return {"error": "Not a file", "path": rel_path}
    text = Path(target).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    s = max(1, start_line or 1)
    e = min(len(lines), end_line or len(lines))
    snippet = "\n".join(f"{n:5d}  {lines[n - 1]}" for n in range(s, e + 1))
    return {
        "path": rel_path,
        "start_line": s,
        "end_line": e,
        "total_lines": len(lines),
        "content": snippet,
    }
