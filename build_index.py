"""Build the AcuBuddy vector database from Acumatica documentation files.

Place your documentation files (PDFs, .txt, .md, .cs, .xml, etc.) in the
`data/` directory, then run this script to build the search index.

Usage:
    python build_index.py [--data-dir data] [--index-dir chroma_db]
"""

import argparse
import sys
from pathlib import Path
from acu_buddy.rag import load_documents, split_documents, build_index


def main():
    parser = argparse.ArgumentParser(
        description="Build AcuBuddy vector index from documentation files."
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing documentation files (default: data)",
    )
    parser.add_argument(
        "--index-dir",
        default="chroma_db",
        help="Directory to persist the vector index (default: chroma_db)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"ERROR: Data directory '{data_dir}' not found.", file=sys.stderr)
        print(f"Create it and place your Acumatica docs inside.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading documents from {data_dir} ...")
    docs = load_documents(str(data_dir))

    if not docs:
        print("No documents found. Add files to the data/ directory and retry.", file=sys.stderr)
        sys.exit(1)

    print("Splitting documents into chunks ...")
    chunks = split_documents(docs)

    print(f"Building vector index in {args.index_dir} ...")
    build_index(chunks, args.index_dir)

    print("Done. Start the server with: python server.py")


if __name__ == "__main__":
    main()
