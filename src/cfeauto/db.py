"""Index SQLite partagé entre le worker et l'interface web."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from .securite import make_private_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    account_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    login_confirmed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    level TEXT NOT NULL,
    account_id TEXT,
    siren TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    siren TEXT NOT NULL,
    dossier_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    received_at TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    UNIQUE (siren, external_id)
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    siren TEXT NOT NULL,
    dossier_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    year INTEGER NOT NULL,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    source_key TEXT NOT NULL,
    message_id INTEGER REFERENCES messages(id),
    collected_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    UNIQUE (siren, kind, sha256)
);
CREATE INDEX IF NOT EXISTS documents_source ON documents (siren, kind, source_key);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        make_private_dir(path.parent)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            if "login_confirmed" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN login_confirmed INTEGER NOT NULL DEFAULT 0")
        path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self._connect()
        try:
            with conn:
                return conn.execute(sql, params)
        finally:
            conn.close()

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # Exécutions

    def enqueue_run(self, trigger: str, account_id: str | None = None) -> int:
        """Ne crée pas de doublon si une collecte attend ou tourne déjà."""
        active = self._one("SELECT id FROM runs WHERE status IN ('queued', 'running') ORDER BY id LIMIT 1")
        if active:
            return active["id"]
        return self._execute(
            "INSERT INTO runs (status, trigger, account_id, created_at) VALUES ('queued', ?, ?, ?)",
            (trigger, account_id, now()),
        ).lastrowid

    def claim_next_run(self) -> sqlite3.Row | None:
        run = self._one("SELECT * FROM runs WHERE status = 'queued' ORDER BY id LIMIT 1")
        if run is None:
            return None
        claimed = self._execute(
            "UPDATE runs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
            (now(), run["id"]),
        ).rowcount
        return run if claimed else None

    def finish_run(self, run_id: int, status: str) -> None:
        self._execute("UPDATE runs SET status = ?, finished_at = ? WHERE id = ?", (status, now(), run_id))

    def fail_interrupted_runs(self) -> None:
        self._execute("UPDATE runs SET status = 'failed', finished_at = ? WHERE status = 'running'", (now(),))

    def confirm_login(self, run_id: int) -> bool:
        """La personne indique dans l'interface qu'elle s'est connectée."""
        return self._execute(
            "UPDATE runs SET login_confirmed = 1 WHERE id = ? AND status = 'running'", (run_id,)
        ).rowcount > 0

    def take_login_confirmation(self, run_id: int) -> bool:
        """Consomme la confirmation : une reconnexion ultérieure en demandera une nouvelle."""
        return self._execute(
            "UPDATE runs SET login_confirmed = 0 WHERE id = ? AND login_confirmed = 1", (run_id,)
        ).rowcount > 0

    def has_scheduled_run_today(self) -> bool:
        today = datetime.now().date().isoformat()
        return self._one(
            "SELECT 1 FROM runs WHERE trigger = 'schedule' AND created_at >= ?", (today,)
        ) is not None

    def recent_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM runs WHERE id = ?", (run_id,))

    def log(self, run_id: int, level: str, message: str, account_id: str | None = None, siren: str | None = None) -> None:
        self._execute(
            "INSERT INTO run_events (run_id, level, account_id, siren, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, level, account_id, siren, message, now()),
        )

    def run_events(self, run_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM run_events WHERE run_id = ? ORDER BY id", (run_id,))

    # Documents

    def has_source(self, siren: str, kind: str, source_key: str) -> bool:
        return self._one(
            "SELECT 1 FROM documents WHERE siren = ? AND kind = ? AND source_key = ?", (siren, kind, source_key)
        ) is not None

    def has_recent_document(self, siren: str, kind: str, days: int) -> bool:
        limit = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        return self._one(
            "SELECT 1 FROM documents WHERE siren = ? AND kind = ? AND collected_at >= ?", (siren, kind, limit)
        ) is not None

    def has_sha(self, siren: str, kind: str, sha256: str) -> bool:
        return self._one(
            "SELECT 1 FROM documents WHERE siren = ? AND kind = ? AND sha256 = ?", (siren, kind, sha256)
        ) is not None

    def add_document(self, *, siren: str, dossier_name: str, kind: str, year: int, title: str, path: str,
                     sha256: str, source_key: str, message_id: int | None = None) -> int:
        return self._execute(
            "INSERT INTO documents (siren, dossier_name, kind, year, title, path, sha256, source_key, message_id, collected_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (siren, dossier_name, kind, year, title, path, sha256, source_key, message_id, now()),
        ).lastrowid

    def get_document(self, document_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM documents WHERE id = ?", (document_id,))

    def mark_document_seen(self, document_id: int) -> None:
        self._execute("UPDATE documents SET seen = 1 WHERE id = ?", (document_id,))

    def documents_for(self, siren: str) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM documents WHERE siren = ? ORDER BY year DESC, id DESC", (siren,))

    def exported_sirens(self) -> list[str]:
        return [row["siren"] for row in self._all("SELECT DISTINCT siren FROM documents ORDER BY siren")]

    def attachments_for(self, message_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM documents WHERE message_id = ? ORDER BY id", (message_id,))

    def dossiers_overview(self) -> list[sqlite3.Row]:
        return self._all(
            """
            SELECT siren, MAX(dossier_name) AS name, SUM(docs) AS docs, SUM(new_docs) AS new_docs,
                   SUM(msgs) AS msgs, SUM(new_msgs) AS new_msgs
            FROM (
                SELECT siren, dossier_name, COUNT(*) AS docs, SUM(seen = 0) AS new_docs, 0 AS msgs, 0 AS new_msgs
                FROM documents GROUP BY siren
                UNION ALL
                SELECT siren, dossier_name, 0, 0, COUNT(*), SUM(seen = 0) FROM messages GROUP BY siren
            ) GROUP BY siren ORDER BY name
            """
        )

    # Messages

    def has_message(self, siren: str, external_id: str) -> bool:
        return self._one(
            "SELECT 1 FROM messages WHERE siren = ? AND external_id = ?", (siren, external_id)
        ) is not None

    def add_message(self, *, siren: str, dossier_name: str, external_id: str, received_at: str, subject: str, body: str) -> int:
        return self._execute(
            "INSERT INTO messages (siren, dossier_name, external_id, received_at, subject, body, collected_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (siren, dossier_name, external_id, received_at, subject, body, now()),
        ).lastrowid

    def get_message(self, message_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM messages WHERE id = ?", (message_id,))

    def mark_message_seen(self, message_id: int) -> None:
        self._execute("UPDATE messages SET seen = 1 WHERE id = ?", (message_id,))

    def messages_for(self, siren: str) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM messages WHERE siren = ? ORDER BY id DESC", (siren,))
