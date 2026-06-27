from __future__ import annotations
from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime


class LocalRepo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    repo_path: str = Field(unique=True, index=True)
    repo_name: str
    index_status: str = Field(default="never_indexed")
    embedding_provider: str = Field(default="voyage")
    embedding_model_name: str = Field(default="voyage-code-2")
    latest_run_id: Optional[int] = Field(default=None)
    total_node_count: int = Field(default=0)
    last_indexed_at: Optional[datetime] = Field(default=None)
    last_commit_sha: Optional[str] = Field(default=None)
    last_trigger_type: str = Field(default="initial")
    last_run_files_total: int = Field(default=0)
    last_run_files_processed: int = Field(default=0)
    last_run_nodes_embedded: int = Field(default=0)
    last_run_started_at: Optional[datetime] = Field(default=None)
    last_run_completed_at: Optional[datetime] = Field(default=None)
    last_run_error: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now())
