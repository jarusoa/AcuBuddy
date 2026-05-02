import os
from langchain_community.document_loaders import TextLoader, PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
SEARCH_K = 5

PDF_LOADER = PyMuPDFLoader
TEXT_LOADER = TextLoader

EXCLUDE_EXTS = {".gitkeep", ".db", ".bin", ".pickle", ".parquet", ".lock"}
PDF_EXTS = {".pdf"}


def _get_loader(file_path: str):
    ext = os.path.splitext(file_path)[1].lower()
    if ext in PDF_EXTS:
        return PDF_LOADER(file_path)
    return TEXT_LOADER(file_path, encoding="utf-8", autodetect_encoding=True)


def load_documents(data_dir: str):
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    docs = []
    file_count = 0
    for root, _, files in os.walk(data_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXCLUDE_EXTS:
                continue
            fpath = os.path.join(root, fname)
            try:
                loader = _get_loader(fpath)
                file_docs = loader.load()
                for doc in file_docs:
                    doc.metadata["source"] = fpath
                docs.extend(file_docs)
                file_count += 1
            except Exception as e:
                print(f"  SKIP {fpath}: {e}")

    print(f"Loaded {len(docs)} documents from {file_count} files in {data_dir}")
    return docs


def split_documents(docs):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    print(f"Split into {len(chunks)} chunks")
    return chunks


def build_index(docs, persist_dir: str):
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    os.makedirs(persist_dir, exist_ok=True)

    vecstore = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        persist_directory=persist_dir,
        collection_name="acumatica_docs",
    )
    print(f"Vector index built and persisted to {persist_dir}")
    return vecstore


def load_index(persist_dir: str):
    if not os.path.isdir(persist_dir):
        raise FileNotFoundError(f"Index directory not found: {persist_dir}")

    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    vecstore = Chroma(
        persist_directory=persist_dir,
        embedding_function=embeddings,
        collection_name="acumatica_docs",
    )
    print(f"Loaded index from {persist_dir}")
    return vecstore


def search(vecstore, query: str, k: int = SEARCH_K):
    results = vecstore.similarity_search(query, k=k)
    return [doc.page_content for doc in results]
