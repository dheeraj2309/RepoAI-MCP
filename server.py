from fastmcp import FastMCP

from local_database import create_tables
from config import config

from tools.setup import (
    start_new_project, 
    setup_existing_repo,
    check_indexing_status,
    check_repo_changes,
    index_file,
)
from tools.search import (
    search_codebase,
    get_function,
    get_class,
    list_symbols,
    get_file_summary,
)
from tools.write import push_to_branch

create_tables()

mcp = FastMCP("repo-ai",instructions= """
You have access to a code intelligence system that indexes
local Git repositories and makes them semantically searchable without
consuming large amounts of context window.

━━ SESSION START ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
At the beginning of every session, before doing anything else:
1. Ask the developer: which project, and is it new or existing?
2. Call start_new_project(repo_path) for brand new projects
   Call setup_existing_repo(repo_path) for existing codebases
3. Call check_indexing_status(repo_id) until status is 'indexed'
4. Then call check_repo_changes(repo_id) to confirm index is current

━━ BEFORE ANY TASK REQUIRING CODEBASE KNOWLEDGE ━━━━━━━━━
Always call check_repo_changes(repo_id) first.
For each file it reports as changed, call index_file(repo_id, file_path).
Never rely on your memory of code from earlier in the conversation —
call search_codebase instead. The index is always more accurate.

━━ IF YOU ARE CLAUDE CODE (CLI) — YOU WRITE FILES DIRECTLY ━━
You have built-in file writing tools. After writing or modifying
any file with those tools:
  → Immediately call index_file(repo_id, file_path) yourself
  → Do not wait for the developer to tell you
  → This keeps the index in sync with what you just wrote
  → Then use search_codebase to verify before building on top of it

━━ IF YOU ARE CLAUDE DESKTOP — DEVELOPER WRITES FILES ━━━━
You cannot write files. The developer writes code manually or
copies your suggestions. After they tell you they have saved a file:
  → Call index_file(repo_id, file_path) immediately
  → Confirm what was indexed before proceeding
  → If they haven't told you the exact filename, ask before indexing

━━ SEARCH DISCIPLINE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use technical terms, not natural language, when searching.
Describe what the code DOES, not what you want to know.
  Good: "user authentication token validation"
  Bad:  "how does login work"

Use tools in this order when building on existing code:
  1. check_repo_changes    → what changed since last index?
  2. index_file            → sync those changes
  3. search_codebase       → find relevant existing code
  4. get_class / get_function → retrieve exact structure needed
  5. write / suggest code  → grounded in what was actually found
  6. index_file            → sync what was just written

━━ NEVER ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Never assume you remember what a file contains from earlier in the
conversation. Always query the index — it reflects the actual file
on disk, your memory does not.
""")

# ── Setup & status tools ──────────────────────────────────────────────────
mcp.tool()(start_new_project)
mcp.tool()(setup_existing_repo)
mcp.tool()(check_indexing_status)
mcp.tool()(check_repo_changes)
mcp.tool()(index_file)

# ── Search & navigation tools ─────────────────────────────────────────────
mcp.tool()(search_codebase)
mcp.tool()(get_function)
mcp.tool()(get_class)
mcp.tool()(list_symbols)
mcp.tool()(get_file_summary)

# ── Write-back tool — only registered if GITHUB_TOKEN is configured ──────
if config.github_token:
    mcp.tool()(push_to_branch)


if __name__ == "__main__":
    mcp.run()
