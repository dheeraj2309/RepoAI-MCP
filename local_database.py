import os
from pathlib import Path
from sqlmodel import SQLModel, create_engine, Session

DATA_DIR = Path(os.environ.get("DATA_DIR", "~/.repo-ai")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATA_DIR / "db.sqlite"
CHROMA_PATH = DATA_DIR / "chroma_db"

engine = create_engine(
    f"sqlite:///{DATABASE_PATH}", connect_args={"check_same_thread": False}, echo=False
)


def create_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
