"""Static validator for model-generated C# against the project catalog.

Catches the highest-value class of errors before the user has to compile:
  - Class name collisions with existing project classes
  - Event handlers referencing fields that don't exist on cataloged DACs
  - DAC extensions adding fields that already exist on the target DAC
    (or any of its other extensions in the catalog)
  - References to DACs/graphs not in the project (informational note —
    can't tell if it's a stock Acumatica type without compiling)

This is intentionally not a real type checker. For full type checking,
shell out to `dotnet build` against the user's Acumatica references.
That's a follow-up; this module is the cheap-but-immediate check.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from acu_buddy.project_indexer import ProjectCatalog, parse_text


@dataclass
class Issue:
    severity: str  # "error" | "warning" | "note"
    kind: str
    message: str
    line: Optional[int]
    context: dict


def _short(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return name.split(".")[-1].strip()


def _collect_extension_fields(dacs) -> dict[str, set[str]]:
    """Map: target_dac_short_lower -> set of field names (lowercased) added by extensions."""
    out: dict[str, set[str]] = {}
    for d in dacs:
        if d.kind != "dac_extension" or not d.extends:
            continue
        tgt = (_short(d.extends) or "").lower()
        if not tgt:
            continue
        out.setdefault(tgt, set()).update(f.name.lower() for f in d.fields)
    return out


def _scope_catalog(catalog: ProjectCatalog, project: Optional[str]) -> ProjectCatalog:
    """Return a catalog filtered to a single project (or the original if project is None)."""
    if not project:
        return catalog
    needle = project.lower()
    return ProjectCatalog(
        project_root=catalog.project_root,
        built_at=catalog.built_at,
        file_count=catalog.file_count,
        dacs=[d for d in catalog.dacs if d.project.lower() == needle],
        graphs=[g for g in catalog.graphs if g.project.lower() == needle],
        events=[e for e in catalog.events if e.project.lower() == needle],
    )


def validate(
    code: str,
    catalog: Optional[ProjectCatalog],
    project: Optional[str] = None,
) -> list[Issue]:
    """Run static checks on `code`.

    If `catalog` is None, only intra-input checks run (no cross-reference).
    If `project` is provided, the catalog is scoped to that customization
    project before checking — so adding `UsrFoo` to CompanyA's `ARInvoice`
    won't conflict with CompanyB's `ARInvoice` extension.
    Returns ordered list of issues.
    """

    issues: list[Issue] = []
    in_dacs, in_graphs, in_events = parse_text(code, file_label="<input>")

    new_class_names = {d.name for d in in_dacs} | {g.name for g in in_graphs}

    if catalog is None:
        if not in_dacs and not in_graphs:
            issues.append(
                Issue(
                    severity="note",
                    kind="empty_input",
                    message="No DAC, DAC extension, graph, or graph extension found in the input.",
                    line=None,
                    context={},
                )
            )
        return issues

    catalog = _scope_catalog(catalog, project)

    by_dac = {d.name: d for d in catalog.dacs}
    by_graph = {g.name: g for g in catalog.graphs}
    ext_fields_by_target = _collect_extension_fields(catalog.dacs)

    # 1. Class name collisions
    for cls_name in new_class_names:
        existing = by_dac.get(cls_name) or by_graph.get(cls_name)
        if existing is not None:
            issues.append(
                Issue(
                    severity="warning",
                    kind="class_name_clash",
                    message=(
                        f"Class '{cls_name}' already exists in the project at "
                        f"{existing.file}:{existing.line}. Pick a different name "
                        f"or confirm you intend to replace it."
                    ),
                    line=None,
                    context={"existing_file": existing.file, "existing_line": existing.line},
                )
            )

    # 2. DAC extension target / field collisions
    for d in in_dacs:
        if d.kind == "dac_extension" and d.extends:
            tgt_short = _short(d.extends) or ""
            tgt_lower = tgt_short.lower()
            base = by_dac.get(tgt_short)

            if base is None:
                issues.append(
                    Issue(
                        severity="note",
                        kind="unknown_target",
                        message=(
                            f"Extends DAC '{d.extends}' which isn't in the project catalog. "
                            f"If this is a stock Acumatica DAC, confirm the namespace; otherwise "
                            f"you may need to index that source first."
                        ),
                        line=d.line,
                        context={"target": d.extends},
                    )
                )

            existing_fields: set[str] = set()
            if base is not None:
                existing_fields |= {f.name.lower() for f in base.fields}
            existing_fields |= ext_fields_by_target.get(tgt_lower, set())

            for f in d.fields:
                if f.name.lower() in existing_fields:
                    issues.append(
                        Issue(
                            severity="error",
                            kind="field_collision",
                            message=(
                                f"Field '{f.name}' already exists on '{tgt_short}' "
                                f"(via the base DAC or another cataloged extension). "
                                f"Pick a different field name."
                            ),
                            line=f.line,
                            context={"target": tgt_short, "field": f.name},
                        )
                    )

    # 3. Graph extension target validity
    for g in in_graphs:
        if g.kind == "graph_extension" and g.extends:
            tgt_short = _short(g.extends) or ""
            if tgt_short not in by_graph:
                issues.append(
                    Issue(
                        severity="note",
                        kind="unknown_target",
                        message=(
                            f"Extends graph '{g.extends}' which isn't in the project catalog. "
                            f"If this is a stock Acumatica graph (e.g. ARInvoiceEntry), this is "
                            f"expected — confirm the namespace."
                        ),
                        line=g.line,
                        context={"target": g.extends},
                    )
                )

    # 4. Event handler field validity
    for e in in_events:
        if e.enclosing_class not in new_class_names:
            continue  # event from an inner / unrelated class — skip

        base = by_dac.get(e.target_dac)
        if base is None:
            issues.append(
                Issue(
                    severity="note",
                    kind="unknown_target",
                    message=(
                        f"Event handler at line {e.line} targets '{e.target_dac}', which isn't in "
                        f"the project catalog. If this is a stock Acumatica DAC, that's expected."
                    ),
                    line=e.line,
                    context={"target_dac": e.target_dac},
                )
            )
            continue

        if e.target_field is None:
            continue

        valid_fields = {f.name.lower() for f in base.fields}
        valid_fields |= ext_fields_by_target.get(e.target_dac.lower(), set())

        if e.target_field.lower() not in valid_fields:
            issues.append(
                Issue(
                    severity="error",
                    kind="field_not_found",
                    message=(
                        f"Event handler references '{e.target_dac}.{e.target_field}', but no "
                        f"such field exists on '{e.target_dac}' or any of its cataloged extensions. "
                        f"Check the spelling against list_dac_fields('{e.target_dac}')."
                    ),
                    line=e.line,
                    context={
                        "target_dac": e.target_dac,
                        "target_field": e.target_field,
                        "valid_fields": sorted(valid_fields),
                    },
                )
            )

    return issues


def summarize(issues: list[Issue]) -> dict:
    counts = {"error": 0, "warning": 0, "note": 0}
    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    parts = [f"{n} {sev}{'s' if n != 1 else ''}" for sev, n in counts.items() if n]
    summary = " · ".join(parts) if parts else "no issues"
    return {
        "ok": counts.get("error", 0) == 0,
        "issue_counts": counts,
        "issues": [asdict(i) for i in issues],
        "summary": summary,
    }
