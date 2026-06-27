# Code Intelligence MCP

A local MCP server that gives any LLM semantic understanding of your codebase — without dumping raw files into context.

Instead of copy-pasting code into every conversation, the LLM queries an indexed, searchable representation of your repo. Results are grounded in actual code, not the LLM's memory of what it saw earlier.

---

## How It Works

```
Your repo (local disk)
       ↓
Tree-sitter AST extraction → functions, classes, methods as structured nodes
       ↓
Voyage AI embeddings → each node converted to a semantic vector
       ↓
ChromaDB (local) → stored and searchable
       ↓
MCP tools → LLM queries the index instead of holding code in context
```

Git is used for change detection — `git diff` tells the server exactly which files changed since the last index, so only those files get re-embedded. Unchanged functions are never re-processed regardless of what else changed in the same file.

---

## Tools Exposed To The LLM

| Tool | Purpose |
|---|---|
| `start_new_project` | Initialize a brand new project with git setup |
| `setup_existing_repo` | Index an existing codebase |
| `check_indexing_status` | Monitor background indexing progress |
| `check_repo_changes` | Detect file changes since last index via git |
| `index_file` | Re-index a single file after creation or edit |
| `search_codebase` | Semantic search across indexed functions and classes |
| `get_function` | Retrieve a specific function by exact name |
| `get_class` | Retrieve a class and all its methods in one call |
| `list_symbols` | Full inventory of indexed symbols |
| `get_file_summary` | All nodes from a specific file |
| `push_to_branch` | Push LLM-suggested changes to a review branch (optional) |

---

## Requirements

- Python 3.11+
- Git initialized in the repo you want to index (at least one commit)
- Voyage AI API key (free tier — [voyageai.com](https://voyageai.com))

---

## Installation

```bash
git clone https://github.com/you/code-intelligence-mcp
cd code-intelligence-mcp
python -m venv venv
venv/bin/pip install -r requirements.txt
```

**requirements.txt (base — always install):**
```
fastmcp
voyageai
chromadb
sqlmodel
tree-sitter
tree-sitter-python
httpx
pydantic-settings
```

**Optional — local embedding models:**
```bash
pip install sentence-transformers
```

**Optional — OpenAI / Mistral / Cohere / custom endpoints:**
```bash
pip install openai
```

---

## Configuration

Create a `.env` file in the project root:

```env
VOYAGE_API_KEY=your_voyage_api_key_here
DATA_DIR=~/.code-intelligence

# Optional — custom embedding provider
EMBEDDING_PROVIDER=voyage
EMBEDDING_MODEL_NAME=voyage-code-2

# Optional — only needed for push_to_branch tool
GITHUB_TOKEN=your_github_token_here
```

`DATA_DIR` is where the tool stores its own SQLite database and ChromaDB vectors. Set it once and never change it — it has nothing to do with the repos you index.

---

## Connecting To Your LLM Client

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "code-intelligence": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/server.py"],
      "env": {
        "VOYAGE_API_KEY": "your_key",
        "DATA_DIR": "~/.code-intelligence"
      }
    }
  }
}
```

**Claude Code (CLI):**
```bash
claude mcp add code-intelligence \
  /absolute/path/to/venv/bin/python \
  /absolute/path/to/server.py
```

**VS Code** — add to `settings.json`:
```json
{
  "mcp.servers": {
    "code-intelligence": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/server.py"],
      "env": {
        "VOYAGE_API_KEY": "your_key",
        "DATA_DIR": "~/.code-intelligence"
      }
    }
  }
}
```

Restart your client after saving the config.

---

## Usage

Tell the LLM which project you want to work on:

```
"I'm starting a new project at ~/projects/myapp"
→ LLM calls start_new_project

"Index my existing repo at ~/projects/myapp"
→ LLM calls setup_existing_repo
```

After that, the LLM handles the rest — checking for changes before each task, indexing files as they're written, and querying the index instead of holding code in context.

---

## Custom Embedding Providers

The default is Voyage AI's `voyage-code-2`, trained specifically on code. To use a different provider, set these env vars:

```env
# OpenAI
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL_NAME=text-embedding-3-small
EMBEDDING_API_KEY=sk-...

# Local model (any HuggingFace sentence-transformers model)
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL_NAME=nomic-ai/nomic-embed-text-v1.5

# Ollama or any OpenAI-compatible endpoint
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL_NAME=nomic-embed-text
EMBEDDING_BASE_URL=http://localhost:11434/v1
EMBEDDING_API_KEY=ollama
```

Switching providers creates a new isolated ChromaDB collection — existing indexed repos are preserved and remain searchable under their original provider.

---

## Project Structure

```
code-intelligence-mcp/
  server.py              ← FastMCP entry point
  config.py               ← typed environment config
  local_models.py          ← SQLite schema (LocalRepo)
  local_database.py         ← database and ChromaDB paths
  pipeline/
    fetcher.py              ← file walking, git change detection
    parser.py                ← Tree-sitter AST extraction
    chunker.py                ← content-addressed node chunks
    embedder.py                ← embedding API with retry logic
    store.py                    ← ChromaDB operations
    pipeline.py                  ← full index and single-file index
  tools/
    setup.py                     ← project setup and indexing tools
    search.py                      ← search and navigation tools
    write.py                        ← push_to_branch (optional)
```

---

## Notes

- Only Python files are indexed currently. The architecture supports adding languages — each language requires a Tree-sitter grammar and a node-type config.
- `push_to_branch` only appears as a tool if `GITHUB_TOKEN` is configured. It always pushes to a new branch, never to main.
- The server requires at least one git commit in the indexed repo. If the repo has none, the tool creates one automatically using only a `.gitignore` file — your actual code is never committed without your explicit action.