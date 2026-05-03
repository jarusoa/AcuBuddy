# AcuBuddy

RAG-based coding assistant for Acumatica ERP development, powered by DeepSeek V4.

## How it works

1. **Index** — Place Acumatica documentation (PDFs, `.txt`, `.md`, `.xml`, `.cs`, etc.) in `data/`, then run `build_index.py` to create a local vector database.
2. **Serve** — Start the FastAPI server that exposes an OpenAI-compatible `/v1/chat/completions` endpoint with streaming support.
3. **Ask** — Point OpenCode (or any OpenAI client) at `http://127.0.0.1:5000/v1`. Every query searches the vector DB for relevant docs, then sends them as context to DeepSeek V4.

## Quick start

```powershell
# 1. Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your API key
copy .env.example .env
# Edit .env and add your DEEPSEEK_API_KEY

# 4. Build the index (after adding docs to data/)
python build_index.py

# 5. Start the server
uvicorn server:app --host 127.0.0.1 --port 5000 --reload
```

The server starts at `http://127.0.0.1:5000`. Auto-generated API docs at `http://127.0.0.1:5000/docs`.

## API Endpoints

| Method | Path                    | Description                    |
|--------|-------------------------|--------------------------------|
| GET    | `/health`              | Health check                   |
| GET    | `/v1/models`           | List available models          |
| POST   | `/v1/chat/completions` | OpenAI-compatible (streaming + non-streaming) |

## Using with OpenCode

An `opencode.json` is included. OpenCode auto-discovers it when you run `opencode` in this directory.

The config registers AcuBuddy as a custom provider with an OpenAI-compatible backend. When you chat with OpenCode, it sends requests through the local server, which:
1. Searches the vector DB for relevant Acumatica docs
2. Injects them as context into the system prompt
3. Forwards everything to DeepSeek V4 (with streaming)
4. Returns the response in OpenAI format

## Configuration

All settings via environment variables (in `.env`):

| Variable            | Default              | Description                     |
|---------------------|----------------------|---------------------------------|
| `DEEPSEEK_API_KEY`  | —                    | DeepSeek API key (required)     |
| `ACUBUDDY_SEARCH_K` | `5`                  | Number of doc chunks to retrieve|

Port and host are uvicorn CLI arguments (see quick start).

## Adding documentation

Drop Acumatica documentation files into `data/`. Supported formats:
- `.pdf` — PDF documents (via PyMuPDF for best extraction)
- `.txt`, `.md`, `.rst` — Text/markdown
- `.xml`, `.html` — Markup
- `.cs`, `.sql`, `.js`, `.py`, `.ts` — Code files

Then rebuild the index:
```powershell
python build_index.py
```

## VS Code

`Ctrl+Shift+B` (Run Build Task) starts the server using uvicorn with hot-reload. Or open Command Palette → "Tasks: Run Task" → "Start AcuBuddy Server".

## MCP server (recommended)

The same hybrid retrieval is also exposed as an MCP server, so any MCP-aware client (Claude Code, OpenCode, Continue, Cline, Cursor, …) can call the search tools directly. The model can issue **multiple searches per turn** with different filters — that's the main reliability win over the chat-completion proxy.

Tools exposed:

| Tool                | Purpose                                                       |
|---------------------|---------------------------------------------------------------|
| `search_docs`       | Hybrid BM25 + dense + reranked search, filterable by area/doc_type |
| `find_code_samples` | Same, restricted to developer-focused guides                  |
| `get_section`       | Fetch the full text of one section by source + title           |
| `list_doc_sources`  | Enumerate every indexed PDF and its sections                   |

Run it (stdio):
```powershell
python -m acu_buddy.mcp_server
```

### Wiring it into clients

**Claude Code** — add to `.mcp.json` in the repo using AcuBuddy:
```json
{
  "mcpServers": {
    "acubuddy": {
      "command": "python",
      "args": ["-m", "acu_buddy.mcp_server"],
      "cwd": "C:/path/to/AcuBuddy"
    }
  }
}
```

**OpenCode** — extend the existing `opencode.json`:
```json
{
  "mcp": {
    "acubuddy": {
      "type": "local",
      "command": ["python", "-m", "acu_buddy.mcp_server"],
      "cwd": "C:/path/to/AcuBuddy"
    }
  }
}
```

**Continue (VS Code)** — in `~/.continue/config.yaml`:
```yaml
mcpServers:
  - name: acubuddy
    command: python
    args: ["-m", "acu_buddy.mcp_server"]
    cwd: C:/path/to/AcuBuddy
```

**Visual Studio (no native MCP)** — run OpenCode or Aider in a terminal pane next to VS, configured as above. The model sees the tools, edits files on disk, VS picks up the changes.

### Environment variables (MCP server)

| Variable                   | Default               | Description                                              |
|----------------------------|-----------------------|----------------------------------------------------------|
| `ACUBUDDY_INDEX_DIR`       | `./chroma_db`         | Where the hybrid index lives                             |
| `ACUBUDDY_SEARCH_K`        | `5`                   | Default `k` for `search_docs`                            |
| `ACUBUDDY_EMBEDDING_MODEL` | `BAAI/bge-large-en-v1.5` | Dense embedding model                                 |
| `ACUBUDDY_RERANKER_MODEL`  | `BAAI/bge-reranker-base` | Cross-encoder for reranking                           |
| `ACUBUDDY_USE_RERANKER`    | `1`                   | Set `0` to skip reranking (faster, lower quality)        |

