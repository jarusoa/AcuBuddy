"""Build the AcuBuddy hybrid index from Acumatica documentation files.

Place your documentation files (PDFs, .txt, .md, .cs, .xml, etc.) in the
`data/` directory, then run this script. The pipeline:

  1. Loads PDFs section-by-section using their TOC outline
  2. Tags every section with metadata (source, doc_type, area, page range)
  3. Chunks each section with overlap
  4. Builds a dense Chroma index (bge-large-en-v1.5)
  5. Builds a sparse BM25 index alongside it (bm25.pkl)

Usage:
    python build_index.py [--data-dir data] [--index-dir chroma_db] [--clean]
"""

import argparse
import shutil
import sys
from pathlib import Path

from acu_buddy.rag import build_index, load_documents, split_documents


def main():
    parser = argparse.ArgumentParser(
        description="Build AcuBuddy hybrid (dense + BM25) index from documentation files."
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing documentation files (default: data)",
    )
    parser.add_argument(
        "--index-dir",
        default="chroma_db",
        help="Directory to persist the index (default: chroma_db)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the index directory before rebuilding",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    index_dir = Path(args.index_dir)

    if not data_dir.exists():
        print(f"ERROR: Data directory '{data_dir}' not found.", file=sys.stderr)
        print("Create it and place your Acumatica docs inside.", file=sys.stderr)
        sys.exit(1)

    if args.clean and index_dir.exists():
        print(f"Removing existing index at {index_dir} ...")
        shutil.rmtree(index_dir)

    if index_dir.exists() and not (index_dir / "bm25.pkl").exists():
        print(
            f"WARNING: {index_dir} exists but has no BM25 file — looks like an old "
            "index. Re-run with --clean to rebuild from scratch.",
            file=sys.stderr,
        )

    print(f"Loading documents from {data_dir} ...")
    docs = load_documents(str(data_dir))

    if not docs:
        print("No documents found. Add files to the data/ directory and retry.", file=sys.stderr)
        sys.exit(1)

    print("Splitting sections into chunks ...")
    chunks = split_documents(docs)

    print(f"Building hybrid index in {index_dir} ...")
    build_index(chunks, str(index_dir))

    print("Done. Start the server with: uvicorn server:app --host 127.0.0.1 --port 5000 --reload")


if __name__ == "__main__":
    main()
