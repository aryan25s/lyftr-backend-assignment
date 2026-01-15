import os
import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional, Tuple

from .config import get_settings
from .models import MessageIn


def _get_db_path() -> str:
    settings = get_settings()
    # DATABASE_URL is interpreted as a file path for simplicity
    return settings.database_url


def init_db() -> None:
    """Initialize SQLite database and create tables if they do not exist."""

    db_path = _get_db_path()
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                from_msisdn TEXT NOT NULL,
                to_msisdn TEXT NOT NULL,
                ts TEXT NOT NULL,
                text TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_msisdn)"
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_connection():
    """Yield a SQLite connection with row factory set to Row."""

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def insert_message_idempotent(msg: MessageIn) -> bool:
    """Insert a message; return True if created, False if duplicate (idempotent)."""

    with get_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO messages (message_id, from_msisdn, to_msisdn, ts, text, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (msg.message_id, msg.from_, msg.to, msg.ts, msg.text),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Unique constraint on message_id violated – treat as duplicate
            return False


def list_messages(
    *,
    limit: int,
    offset: int,
    from_filter: Optional[str] = None,
    since: Optional[str] = None,
    q: Optional[str] = None,
) -> Tuple[List[Dict], int]:
    """List messages with filters, pagination, and deterministic ordering."""

    where_clauses = []
    params: List[object] = []

    if from_filter:
        where_clauses.append("from_msisdn = ?")
        params.append(from_filter)

    if since:
        where_clauses.append("ts >= ?")
        params.append(since)

    if q:
        where_clauses.append("text LIKE ?")
        params.append(f"%{q}%")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    base_query = f"""
        FROM messages
        {where_sql}
    """

    with get_connection() as conn:
        # Total count
        total_row = conn.execute(f"SELECT COUNT(*) AS cnt {base_query}", params).fetchone()
        total = int(total_row["cnt"]) if total_row else 0

        # Data query with ordering: ts ASC, message_id ASC
        rows = conn.execute(
            f"""
            SELECT message_id, from_msisdn, to_msisdn, ts, text, created_at
            {base_query}
            ORDER BY ts ASC, message_id ASC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

    items = [
        {
            "message_id": r["message_id"],
            "from": r["from_msisdn"],
            "to": r["to_msisdn"],
            "ts": r["ts"],
            "text": r["text"],
        }
        for r in rows
    ]

    return items, total


def get_stats() -> Dict:
    """Compute analytics statistics."""

    with get_connection() as conn:
        total_row = conn.execute("SELECT COUNT(*) AS cnt FROM messages").fetchone()
        total_messages = int(total_row["cnt"]) if total_row else 0

        senders_row = conn.execute(
            "SELECT COUNT(DISTINCT from_msisdn) AS cnt FROM messages"
        ).fetchone()
        senders_count = int(senders_row["cnt"]) if senders_row else 0

        top_senders_rows = conn.execute(
            """
            SELECT from_msisdn AS sender, COUNT(*) AS cnt
            FROM messages
            GROUP BY from_msisdn
            ORDER BY cnt DESC
            LIMIT 10
            """
        ).fetchall()
        messages_per_sender = [
            {"sender": r["sender"], "count": int(r["cnt"])} for r in top_senders_rows
        ]

        first_row = conn.execute(
            "SELECT ts FROM messages ORDER BY ts ASC, message_id ASC LIMIT 1"
        ).fetchone()
        last_row = conn.execute(
            "SELECT ts FROM messages ORDER BY ts DESC, message_id DESC LIMIT 1"
        ).fetchone()

        first_ts = first_row["ts"] if first_row else None
        last_ts = last_row["ts"] if last_row else None

    return {
        "total_messages": total_messages,
        "senders_count": senders_count,
        "messages_per_sender": messages_per_sender,
        "first_message_ts": first_ts,
        "last_message_ts": last_ts,
    }


