"""Database connection management for SQLite."""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

# Use a module-level lock for schema initialization to prevent race conditions at startup
_init_lock = threading.Lock()

# Define the local database path
# It will be stored in backend/data/smartsort.db
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "smartsort.db"


def get_db_path() -> Path:
    """Get the path to the database file. Creates the data directory if it doesn't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_DB_PATH


@contextmanager
def get_connection(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional scope around a series of database operations.

    Yields a connection that has foreign keys enabled.
    The transaction is committed on success, or rolled back if an exception occurs.
    """
    path = db_path or get_db_path()
    
    # SQLite connections should not be shared across threads by default.
    # We open a new connection per operation context.
    conn = sqlite3.connect(
        database=str(path),
        # Wait up to 10 seconds for locks to clear
        timeout=10.0,
    )
    # Return row objects that support dictionary-like access
    conn.row_factory = sqlite3.Row

    try:
        # Enforce foreign key constraints (SQLite disables them by default)
        conn.execute("PRAGMA foreign_keys = ON;")
        
        yield conn
        
        # Commit the transaction if no exception was raised
        conn.commit()
    except Exception:
        # Roll back on error
        conn.rollback()
        raise
    finally:
        # Ensure the connection is always closed
        conn.close()
