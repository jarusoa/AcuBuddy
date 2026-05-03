"""Build the AcuBuddy project catalog from a customization project's source.

Walks every .cs file under the project root and extracts a structured
catalog of DACs, DAC extensions, graphs, graph extensions, and event
handlers. The catalog is written to <index-dir>/project_catalog.json
and read by the MCP server's project tools (find_dac, list_dac_fields,
find_graph_extensions, find_event_handlers, ...).

Usage:
    python index_project.py --project-root <path> [--index-dir chroma_db]

If --project-root is omitted, falls back to ACUBUDDY_PROJECT_ROOT.
"""

import argparse
import os
import sys
from pathlib import Path

from acu_buddy.project_indexer import CATALOG_FILENAME, build_catalog, save_catalog


def main():
    parser = argparse.ArgumentParser(
        description="Build the AcuBuddy structured catalog from a customization project."
    )
    parser.add_argument(
        "--project-root",
        default=os.environ.get("ACUBUDDY_PROJECT_ROOT"),
        help="Root folder of the customization project (defaults to ACUBUDDY_PROJECT_ROOT).",
    )
    parser.add_argument(
        "--index-dir",
        default="chroma_db",
        help="Where to write project_catalog.json (default: chroma_db).",
    )
    args = parser.parse_args()

    if not args.project_root:
        print(
            "ERROR: --project-root is required (or set ACUBUDDY_PROJECT_ROOT).",
            file=sys.stderr,
        )
        sys.exit(1)

    root = Path(args.project_root).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: project root '{root}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    index_dir = Path(args.index_dir).expanduser().resolve()
    index_dir.mkdir(parents=True, exist_ok=True)
    out_path = index_dir / CATALOG_FILENAME

    print(f"Scanning {root} ...")
    catalog = build_catalog(str(root))
    save_catalog(catalog, str(out_path))

    print(f"Indexed {catalog.file_count} .cs files:")
    print(f"  DACs / DAC extensions:   {len(catalog.dacs)}")
    print(f"  Graphs / graph extensions: {len(catalog.graphs)}")
    print(f"  Event handlers:          {len(catalog.events)}")
    print(f"Catalog written to {out_path}")


if __name__ == "__main__":
    main()
