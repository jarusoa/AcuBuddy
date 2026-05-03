# AcuBuddy

Acumatica ERP coding assistant. Hybrid retrieval (BM25 + dense + cross-encoder rerank) over the Acumatica documentation, exposed as MCP tools so any MCP-aware client (OpenCode, Claude Code, Continue, Cline, Cursor) can search docs as the model needs them.

## How it works

1. **Index** — Place Acumatica documentation (PDFs, `.txt`, `.md`, `.xml`, `.cs`, etc.) in `data/`, then run `build_index.py` to build a hybrid index: dense (BAAI/bge-large-en-v1.5 in Chroma) + sparse (BM25) + section-aware metadata.
2. **Catalog** (optional) — Point `ACUBUDDY_PROJECT_ROOT` at your customization project and run `index_project.py`. The structured catalog (DACs, graphs, events) backs the project-aware MCP tools.
3. **Ask** — Run `opencode` (or another MCP-aware client) in this folder. The client auto-spawns the MCP server defined in `opencode.json` and exposes its 13 tools to the model. Answers cite source PDFs with page ranges.

## Quick start

```powershell
# 1. Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your API key
copy .env.example .env
# Edit .env and add your DEEPSEEK_API_KEY (used by OpenCode to call DeepSeek directly)

# 4. Build the index (after adding docs to data/)
python build_index.py --clean

# 5. Run OpenCode in this directory
opencode
```

The bundled `opencode.json` points OpenCode straight at DeepSeek and registers AcuBuddy as an MCP server. OpenCode spawns the MCP server on startup and exposes its tools to the model. Project-level agent instructions live in `AGENTS.md` (auto-discovered by OpenCode and most MCP-aware clients) — they enforce the advisory-only "Code Recipe" output format so the model never edits your `.cs` / `.aspx` files directly.

## Using with OpenCode

An `opencode.json` is included. OpenCode auto-discovers it when you run `opencode` in this directory.

The config:
- Registers `deepseek-v4-pro` (DeepSeek V4 Pro) as the active model
- Reads `DEEPSEEK_API_KEY` from your environment
- Spawns `python -m acu_buddy.mcp_server` and exposes its tools to the model
- Loads `AGENTS.md` for the agent's system instructions (advisory-only output; never edits project files; emits a structured "Code Recipe")

When you ask an Acumatica question, the model decides whether to call `search_docs`, refines with filters (`area="customization"`, `doc_type="reference"`), and may call `get_section` to read a full section. For project-specific questions it uses `find_dac` / `list_dac_fields` / `find_graph_extensions` etc. Generated code goes through `validate_csharp` before being shown to you, in a Code Recipe block you paste into the Customization Project Editor.

> Note: `deepseek-v4-pro` is the model id used in `opencode.json`. If DeepSeek's API rejects it, swap in the id from their pricing page (commonly `deepseek-chat`) — one-line edit.

## Configuration

Environment variables (in `.env`):

| Variable                   | Default                  | Description                                         |
|----------------------------|--------------------------|-----------------------------------------------------|
| `DEEPSEEK_API_KEY`         | —                        | DeepSeek API key (required by OpenCode)             |
| `ACUBUDDY_PROJECT_ROOT`    | —                        | Customization project source folder (enables project tools) |
| `ACUBUDDY_SEARCH_K`        | `5`                      | Default `k` for `search_docs`                       |
| `ACUBUDDY_INDEX_DIR`       | `./chroma_db`            | Where the hybrid index lives                        |
| `ACUBUDDY_EMBEDDING_MODEL` | `BAAI/bge-large-en-v1.5` | Dense embedding model                               |
| `ACUBUDDY_RERANKER_MODEL`  | `BAAI/bge-reranker-base` | Cross-encoder for reranking                         |
| `ACUBUDDY_USE_RERANKER`    | `1`                      | Set `0` to skip reranking (faster, lower quality)   |

## MCP tools

**Doc tools** (always available):

| Tool                | Purpose                                                       |
|---------------------|---------------------------------------------------------------|
| `search_docs`       | Hybrid BM25 + dense + reranked search, filterable by area/doc_type |
| `find_code_samples` | Same, restricted to developer-focused guides                  |
| `get_section`       | Fetch the full text of one section by source + title           |
| `list_doc_sources`  | Enumerate every indexed PDF and its sections                   |

**Project tools** (require `ACUBUDDY_PROJECT_ROOT`):

| Tool                    | Purpose                                                    |
|-------------------------|------------------------------------------------------------|
| `reindex_project`       | Rebuild the structured catalog from project source         |
| `find_dac`              | Look up a DAC or DAC extension by name (fuzzy by default)  |
| `list_dac_fields`       | Enumerate every field on a DAC, with type and attributes   |
| `find_dac_extensions`   | All `PXCacheExtension<T>` for a given DAC                  |
| `find_graph_extensions` | All `PXGraphExtension<T>` for a given graph                |
| `find_event_handlers`   | All event handlers on a DAC, modern + legacy styles        |
| `search_project`        | Substring search over project source (with file-glob)      |
| `read_project_file`     | Read a project file by relative path, optional line range  |

**Validation tool**:

| Tool              | Purpose                                                          |
|-------------------|------------------------------------------------------------------|
| `validate_csharp` | Static checks against the catalog: field collisions, bad event-handler fields, class-name clashes, unknown targets |

The model can call these multiple times per turn with different filters — that's the main reliability win over single-shot RAG.

## Project awareness

Point AcuBuddy at the source folder of your customization project (where the Customization Project Editor extracts `.cs` files):

```powershell
$env:ACUBUDDY_PROJECT_ROOT = "C:\path\to\Your.Customization\Source"
python index_project.py
```

**Don't point at `C:\inetpub\wwwroot\<Instance>` itself** — that pulls in stock Acumatica `App_Code/`, sample folders, and any other unpacked customization on the same instance, polluting the catalog. The right paths are usually:

- `C:\inetpub\wwwroot\<Instance>\CstSrc\<YourCustomizationProjectName>\` (when you "Edit project items as text" inside Acumatica), or
- `C:\Projects\<YourCustomization>\` (when you maintain it as a standalone Visual Studio project)

The walker auto-skips common Acumatica wwwroot folders (`Bin`, `App_Data`, `App_Code`, `Pages`, `Frames`, `CstPublished`, `WebSiteCache`, `WebSiteValidation`) case-insensitively, so accidentally rooting one level too high is annoying but not catastrophic.

This walks every `.cs` file and builds a structured catalog:
- DACs (anything implementing `IBqlTable`) with their `public virtual` fields and attributes
- DAC extensions (`PXCacheExtension<T>`) with the target DAC and added fields
- Graphs (`PXGraph<...>`) with their primary DAC if declared
- Graph extensions (`PXGraphExtension<T>`) with the target graph
- Event handlers in both modern (`Events.RowSelected<DAC>`) and legacy (`DAC_Field_Kind`) styles

The catalog is written to `chroma_db/project_catalog.json`. Rebuild after editing your project (or call `reindex_project` from the model). The catalog covers the "list all X" / "find all extensions of Y" questions that vector search misses.

## Code validation

`validate_csharp` runs static checks against the catalog before the user has to compile. The model is expected to call it on every code block it produces.

What it catches:

| Severity   | What                                                                    |
|------------|-------------------------------------------------------------------------|
| **error**  | Field collision: adding a field that already exists on the target DAC or any of its cataloged extensions |
| **error**  | Event handler references a field that doesn't exist on a cataloged DAC  |
| **warning**| Class-name clash with an existing project class                         |
| **note**   | Reference to a DAC/graph not in the catalog (likely a stock Acumatica type — verify the namespace) |

What it doesn't catch (yet): syntax errors, type mismatches, missing usings, attribute parameter validity. Those need a real compile. A future enhancement is to shell out to `dotnet build` against the user's Acumatica references when available.

The model can use the result to self-correct: errors → fix and re-validate; only notes → likely fine, just verify unknown targets. The `valid_fields` list returned in `field_not_found` errors lets the model spot the right field directly without another tool call.

## Wiring into other clients

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

**Continue (VS Code)** — in `~/.continue/config.yaml`:
```yaml
mcpServers:
  - name: acubuddy
    command: python
    args: ["-m", "acu_buddy.mcp_server"]
    cwd: C:/path/to/AcuBuddy
```

**Visual Studio (no native MCP)** — run OpenCode in a terminal pane next to VS, configured the same way. The model uses MCP tools to research and validate, then emits Code Recipes you paste into the Customization Project Editor (it never writes to your `.cs` / `.aspx` files directly — see `AGENTS.md`).

## Adding documentation

Drop Acumatica documentation files into `data/`. Supported formats:
- `.pdf` — PDF documents (via PyMuPDF, with TOC-aware section splitting)
- `.txt`, `.md`, `.rst` — Text/markdown
- `.xml`, `.html` — Markup
- `.cs`, `.sql`, `.js`, `.py`, `.ts` — Code files

Then rebuild the index:
```powershell
python build_index.py --clean
```
