"""Retrieval pipeline for AcuBuddy.

Pipeline:
  1. Section-aware loading: PDFs are split by their outline (TOC) so chunks
     carry a real section title and page range. Non-PDF text/code files are
     loaded whole.
  2. Metadata tagging: every document carries {source, source_name, doc_type,
     area, section, page_start, page_end} so callers can filter.
  3. Recursive character chunking inside each section.
  4. Dense index via Chroma + bge-large-en-v1.5 embeddings.
  5. Sparse index via BM25 over the same chunks (pickled alongside Chroma).
  6. Hybrid search: dense top-N + sparse top-N -> RRF fusion -> cross-encoder
     rerank top-M -> return top-k with metadata.
"""

from __future__ import annotations

import os
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF
from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rank_bm25 import BM25Okapi

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
EMBEDDING_MODEL = os.getenv("ACUBUDDY_EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5")
RERANKER_MODEL = os.getenv("ACUBUDDY_RERANKER_MODEL", "BAAI/bge-reranker-base")
USE_RERANKER = os.getenv("ACUBUDDY_USE_RERANKER", "1") not in ("0", "false", "False")
COLLECTION = "acumatica_docs"
BM25_FILE = "bm25.pkl"

DENSE_TOP_N = 30
SPARSE_TOP_N = 30
RERANK_TOP_M = 20
DEFAULT_K = 5

EXCLUDE_EXTS = {".gitkeep", ".db", ".bin", ".pickle", ".parquet", ".lock", ".pkl"}
PDF_EXTS = {".pdf"}
BINARY_CHUNK = 1024
NULL_THRESHOLD = 0.05

_TOKEN_RE = re.compile(r"[a-z0-9]+")

AREA_MAP = {
    "accountspayable": "ap",
    "accountsreceivable": "ar",
    "generalledger": "gl",
    "cashmanagement": "cash",
    "currencymanagement": "currency",
    "customermanagement": "crm",
    "customizationguide": "customization",
    "customizationupdate": "customization",
    "dacoverview": "framework",
    "frameworkdevelopmentguide": "framework",
    "integrationdevelopmentguide": "integration",
    "integrations": "integration",
    "uidev": "ui",
    "uidevref": "ui",
    "interfaceguide": "ui",
    "mobileframeworkguide": "mobile",
    "plugindevelopmentguide": "plugin",
    "workflowapi": "workflow",
    "workflows": "workflow",
    "ordermgmt": "orders",
    "invmgmt": "inventory",
    "wms": "inventory",
    "wmsengine": "inventory",
    "manufacturing": "manufacturing",
    "projects": "projects",
    "fixedassets": "fixedassets",
    "deferredrevenue": "deferred",
    "creditpolicy": "credit",
    "taxes": "taxes",
    "payroll": "payroll",
    "timeandexpenses": "time",
    "servicemanagement": "service",
    "equipmentmanagement": "equipment",
    "routemanagement": "route",
    "contractmanagement": "contracts",
    "commerce": "commerce",
    "reportingtools": "reporting",
    "implementationguide": "implementation",
    "implementationchecklists": "implementation",
    "installationguide": "installation",
    "administration": "admin",
    "organizationstructure": "admin",
    "gettingstarted": "intro",
    "self-service_portal_admin": "portal",
    "self-service_portal_user": "portal",
    "arena_plm_integration": "integration",
    "financedatamigration": "migration",
    "unittestframeworkguide": "testing",
}


@dataclass
class SearchResult:
    content: str
    metadata: dict
    score: float

    def citation(self) -> str:
        m = self.metadata
        bits = [m.get("source_name", "?")]
        section = m.get("section")
        if section and section != "(none)":
            bits.append(section)
        page_start = m.get("page_start")
        page_end = m.get("page_end")
        if page_start:
            bits.append(
                f"p.{page_start}" if page_start == page_end else f"pp.{page_start}-{page_end}"
            )
        return " — ".join(bits)


def _is_binary(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(BINARY_CHUNK)
    except OSError:
        return True
    if not chunk:
        return False
    return (chunk.count(0) / len(chunk)) > NULL_THRESHOLD


def _classify(filename: str) -> tuple[str, str]:
    name = Path(filename).stem.lower()
    if "checklist" in name:
        doc_type = "checklist"
    elif "diagram" in name:
        doc_type = "diagram"
    elif "overview" in name or "ref" in name:
        doc_type = "reference"
    else:
        doc_type = "guide"

    cleaned = name.replace("acumaticaerp_", "")
    area = AREA_MAP.get(cleaned, "general")
    if area == "general":
        for key, value in AREA_MAP.items():
            if cleaned.startswith(key):
                area = value
                break
    return doc_type, area


def _file_metadata(fpath: str) -> dict:
    doc_type, area = _classify(fpath)
    return {
        "source": fpath,
        "source_name": Path(fpath).name,
        "doc_type": doc_type,
        "area": area,
    }


def _flatten_toc(toc: list, page_count: int) -> list[tuple[str, int, int]]:
    """Turn PyMuPDF TOC into [(title, page_start, page_end)] with 1-based pages."""
    if not toc:
        return []
    sections = []
    for i, entry in enumerate(toc):
        _level, title, start_page = entry[0], entry[1], entry[2]
        if start_page <= 0:
            continue
        end_page = page_count
        for j in range(i + 1, len(toc)):
            next_start = toc[j][2]
            if next_start > start_page:
                end_page = next_start - 1
                break
        title_clean = (title or "").strip() or "(untitled)"
        sections.append((title_clean, start_page, end_page))
    return sections


def _load_pdf_sections(fpath: str) -> Iterable[Document]:
    base_meta = _file_metadata(fpath)
    try:
        doc = fitz.open(fpath)
    except Exception as exc:
        print(f"  SKIP {fpath}: {exc}")
        return

    sections = _flatten_toc(doc.get_toc(), doc.page_count)

    if not sections:
        text_parts = []
        for page_idx in range(doc.page_count):
            text_parts.append(doc[page_idx].get_text())
        text = "\n".join(text_parts).strip()
        if text:
            yield Document(
                page_content=text,
                metadata={
                    **base_meta,
                    "section": "(none)",
                    "page_start": 1,
                    "page_end": doc.page_count,
                },
            )
        doc.close()
        return

    for title, start_page, end_page in sections:
        text_parts = []
        for page_idx in range(start_page - 1, min(end_page, doc.page_count)):
            text_parts.append(doc[page_idx].get_text())
        text = "\n".join(text_parts).strip()
        if not text:
            continue
        yield Document(
            page_content=text,
            metadata={
                **base_meta,
                "section": title,
                "page_start": start_page,
                "page_end": end_page,
            },
        )
    doc.close()


def _load_text_file(fpath: str) -> Iterable[Document]:
    base_meta = _file_metadata(fpath)
    try:
        loader = TextLoader(fpath, encoding="utf-8", autodetect_encoding=True)
        for d in loader.load():
            d.metadata.update(base_meta)
            d.metadata.setdefault("section", "(none)")
            d.metadata.setdefault("page_start", 0)
            d.metadata.setdefault("page_end", 0)
            yield d
    except Exception as exc:
        print(f"  SKIP {fpath}: {exc}")


def load_documents(data_dir: str) -> list[Document]:
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    docs: list[Document] = []
    file_count = 0
    for root, _, files in os.walk(data_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXCLUDE_EXTS:
                continue
            fpath = os.path.join(root, fname)
            if ext in PDF_EXTS:
                added = list(_load_pdf_sections(fpath))
            else:
                if _is_binary(fpath):
                    print(f"  SKIP {fpath}: binary file")
                    continue
                added = list(_load_text_file(fpath))
            if added:
                docs.extend(added)
                file_count += 1
                print(f"  + {fpath}  ({len(added)} sections)")

    print(f"Loaded {len(docs)} sections from {file_count} files in {data_dir}")
    return docs


def split_documents(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    for i, chunk in enumerate(chunks):
        chunk.metadata["chunk_id"] = f"c{i:07d}"
    print(f"Split into {len(chunks)} chunks")
    return chunks


def _make_embeddings() -> HuggingFaceEmbeddings:
    cache = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".embedding_cache"))
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
        cache_folder=cache,
    )


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def build_index(chunks: list[Document], persist_dir: str) -> "HybridIndex":
    os.makedirs(persist_dir, exist_ok=True)
    embeddings = _make_embeddings()

    dense = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=persist_dir,
        collection_name=COLLECTION,
    )
    print(f"Dense index built ({len(chunks)} chunks) at {persist_dir}")

    corpus = [c.page_content for c in chunks]
    metas = [c.metadata for c in chunks]
    tokenized = [_tokenize(t) for t in corpus]
    bm25 = BM25Okapi(tokenized)
    bm25_path = os.path.join(persist_dir, BM25_FILE)
    with open(bm25_path, "wb") as f:
        pickle.dump({"bm25": bm25, "corpus": corpus, "metas": metas}, f)
    print(f"BM25 index built and persisted to {bm25_path}")

    return HybridIndex(dense=dense, bm25=bm25, corpus=corpus, metas=metas)


class HybridIndex:
    def __init__(
        self,
        dense: Chroma,
        bm25: BM25Okapi,
        corpus: list[str],
        metas: list[dict],
    ) -> None:
        self.dense = dense
        self.bm25 = bm25
        self.corpus = corpus
        self.metas = metas
        self._reranker = None

    def reranker(self):
        if not USE_RERANKER:
            return None
        if self._reranker is None:
            from sentence_transformers import CrossEncoder

            self._reranker = CrossEncoder(RERANKER_MODEL)
        return self._reranker


def load_index(persist_dir: str) -> HybridIndex:
    if not os.path.isdir(persist_dir):
        raise FileNotFoundError(f"Index directory not found: {persist_dir}")
    bm25_path = os.path.join(persist_dir, BM25_FILE)
    if not os.path.isfile(bm25_path):
        raise FileNotFoundError(
            f"BM25 file missing at {bm25_path}. Rebuild with 'python build_index.py'."
        )

    embeddings = _make_embeddings()
    dense = Chroma(
        persist_directory=persist_dir,
        embedding_function=embeddings,
        collection_name=COLLECTION,
    )
    with open(bm25_path, "rb") as f:
        data = pickle.load(f)
    print(f"Loaded hybrid index from {persist_dir} ({len(data['corpus'])} chunks)")
    return HybridIndex(dense=dense, bm25=data["bm25"], corpus=data["corpus"], metas=data["metas"])


def _build_where(filters: dict | None) -> dict | None:
    if not filters:
        return None
    clean = {k: v for k, v in filters.items() if v is not None}
    if not clean:
        return None
    if len(clean) == 1:
        return clean
    return {"$and": [{k: v} for k, v in clean.items()]}


def _rrf(rankings: list[list[str]], k_const: int = 60) -> dict[str, float]:
    """Reciprocal-rank fusion. Returns chunk_id -> fused score."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k_const + rank + 1)
    return fused


def search(
    index: HybridIndex,
    query: str,
    k: int = DEFAULT_K,
    doc_type: str | None = None,
    area: str | None = None,
) -> list[SearchResult]:
    where = _build_where({"doc_type": doc_type, "area": area})

    dense_hits = index.dense.similarity_search(query, k=DENSE_TOP_N, filter=where)
    dense_ranked = [d.metadata.get("chunk_id", "") for d in dense_hits if d.metadata.get("chunk_id")]
    by_id: dict[str, tuple[str, dict]] = {
        d.metadata["chunk_id"]: (d.page_content, d.metadata)
        for d in dense_hits
        if d.metadata.get("chunk_id")
    }

    tokens = _tokenize(query)
    sparse_ranked: list[str] = []
    if tokens:
        scores = index.bm25.get_scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        for i in order:
            if scores[i] <= 0:
                break
            meta = index.metas[i]
            if doc_type and meta.get("doc_type") != doc_type:
                continue
            if area and meta.get("area") != area:
                continue
            cid = meta.get("chunk_id", "")
            if not cid:
                continue
            sparse_ranked.append(cid)
            by_id.setdefault(cid, (index.corpus[i], meta))
            if len(sparse_ranked) >= SPARSE_TOP_N:
                break

    fused = _rrf([dense_ranked, sparse_ranked])
    fused_order = sorted(fused.items(), key=lambda kv: -kv[1])[:RERANK_TOP_M]

    candidates = [(cid, by_id[cid][0], by_id[cid][1]) for cid, _ in fused_order if cid in by_id]
    if not candidates:
        return []

    reranker = index.reranker()
    if reranker is not None:
        pairs = [(query, content) for _, content, _ in candidates]
        rerank_scores = reranker.predict(pairs).tolist()
        scored = list(zip(candidates, rerank_scores))
        scored.sort(key=lambda x: -x[1])
    else:
        scored = [(c, fused[c[0]]) for c in candidates]
        scored.sort(key=lambda x: -x[1])

    results = []
    for (cid, content, meta), score in scored[:k]:
        results.append(SearchResult(content=content, metadata=meta, score=float(score)))
    return results
