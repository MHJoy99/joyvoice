"""Prompt-for-AI conversation memory: isolated SQLite store (stdlib only).

Scope (per docs/sol-prompt-memory-plan.md):
- Separate SQLite file under ``app.storage.paths.data_dir()``; never touches
  ``history.json`` or ``settings.json``.
- ``conversations``: id / title / creation-update times.
- ``turns``: raw recognized user text only (``spoken`` | ``user_note``) with
  provenance (time, source), optional original transcript, and a per-job
  idempotency key so retries cannot duplicate a turn. The generated prompt is
  NEVER stored as a user turn — only :func:`add_user_turn` writes turns.
- ``summaries``: derived-text cache with time, exact source turn IDs, version.
  Summaries are never authoritative; removing/clearing a source invalidates
  summaries that reference it. Compression retains raw turns.
- ``meta`` kv: ``active_conversation_id`` with safe empty default (None).
- Conversation isolation: every read/write is scoped to one conversation id.
- Atomic updates via SQLite transactions; corruption falls back to quarantine
  (damaged file renamed aside, fresh DB started) WITHOUT overwriting the
  damaged file and without raising to callers.
- Privacy: no user text, transcripts, or summaries are ever logged.

Public API contract (positional shapes are stable; extra kwargs are optional):
- ``create_conversation(title) -> str``
- ``list_conversations() -> list[dict]``
- ``get_active_id() -> str | None``
- ``set_active_id(id)``
- ``get_context(conversation_id) -> dict`` with ``turns`` and ``summary``
- ``add_user_turn(conversation_id, text, job_key) -> str``
- ``remove_turn(id)``
- ``clear_conversation(id)``
- ``save_summary(...)``
Plus ``delete_conversation``/``remove_conversation`` and ``get_summary`` helpers.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.storage import paths

logger = logging.getLogger("joyvoice.prompt_memory")

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'spoken',
    transcript TEXT,
    job_key TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_conv_job_key ON turns(conversation_id, job_key) WHERE job_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_turns_conv ON turns(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS summaries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_summaries_conv ON summaries(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_VALID_SOURCES = ("spoken", "user_note")


def _is_corruption(exc: BaseException) -> bool:
    """True ONLY for proven SQLITE_CORRUPT/NOTADB or exact malformed/not-a-database signatures.

    Must NEVER return True for lock, busy, permission, read-only, schema,
    constraint, or ordinary I/O errors. A healthy SQLite DB must never be
    quarantined because of transient concurrency or access issues.
    """
    if isinstance(exc, sqlite3.IntegrityError):
        return False

    code = getattr(exc, "sqlite_errorcode", None)
    name = getattr(exc, "sqlite_errorname", None)

    # 1. Exact error codes if exposed by Python / SQLite
    # 11 = SQLITE_CORRUPT, 26 = SQLITE_NOTADB
    if code in (11, 26) or name in ("SQLITE_CORRUPT", "SQLITE_NOTADB"):
        return True

    # Check for primary error codes in extended error codes (e.g. 267 = SQLITE_CORRUPT_VTAB -> 267 & 0xFF == 11)
    if isinstance(code, int) and (code & 0xFF) in (11, 26):
        return True

    msg = str(exc).lower()

    # Explicit exclusions for locks, permissions, busy, interrupts, schema
    if any(
        non in msg
        for non in (
            "locked",
            "busy",
            "permission",
            "access denied",
            "readonly",
            "read-only",
            "interrupted",
            "no such table",
            "no such column",
            "constraint",
            "syntax error",
            "cannot rollback",
            "not authorized",
        )
    ):
        return False

    # Exact string signatures for true disk corruption
    if any(
        corrupt in msg
        for corrupt in (
            "file is not a database",
            "database disk image is malformed",
            "malformed database schema",
            "file is encrypted or is not a database",
            "unsupported file format",
        )
    ):
        return True

    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _db_path() -> Path:
    override = os.environ.get("JV_PROMPT_MEMORY_DB")
    if override:
        return Path(override)
    return paths.prompt_memory_db_path()


def _quarantine_corrupt(path: Path) -> Path:
    """Rename a damaged DB (and its -wal / -shm sidecars) aside without overwriting it; return backup path."""
    stem = path.stem or "prompt_memory"
    dest = path.parent / f"{stem}.corrupt-{int(time.time())}.db"
    counter = 0
    while dest.exists():
        counter += 1
        dest = path.parent / f"{stem}.corrupt-{int(time.time())}-{counter}.db"

    # Move sidecars along with main db file if present
    for suffix in ("", "-wal", "-shm"):
        src_file = Path(str(path) + suffix)
        if src_file.exists():
            dest_file = Path(str(dest) + suffix)
            try:
                os.replace(src_file, dest_file)
            except Exception:
                try:
                    import shutil
                    shutil.copyfile(src_file, dest_file)
                    try:
                        os.remove(src_file)
                    except Exception:
                        pass
                except Exception:
                    pass
    return dest


def _migrate(conn: sqlite3.Connection) -> None:
    """Safe schema migration for existing databases."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
        if cols and "revision" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    except Exception as exc:
        logger.warning("prompt_memory migration failed: %s", type(exc).__name__)


def _connect(db_path: Path | None = None) -> sqlite3.Connection | None:
    """Open DB, init schema, run migrations; on corruption quarantine and re-init. Never raises."""
    path = Path(db_path) if db_path is not None else _db_path()
    conn = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10.0, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        _migrate(conn)
        return conn
    except sqlite3.DatabaseError as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if not _is_corruption(exc):
            logger.warning("prompt_memory DB error (no quarantine): %s", type(exc).__name__)
            return None
        logger.warning("prompt_memory DB unreadable; quarantining file: %s", type(exc).__name__)
        try:
            if path.exists():
                backup = _quarantine_corrupt(path)
                logger.warning("prompt_memory corrupt DB moved aside (%s)", backup.name)
        except Exception as exc2:
            logger.warning("prompt_memory quarantine failed: %s", type(exc2).__name__)
        try:
            conn2 = sqlite3.connect(str(path), timeout=10.0, check_same_thread=False)
            conn2.execute("PRAGMA foreign_keys=ON")
            conn2.executescript(_SCHEMA)
            _migrate(conn2)
            return conn2
        except Exception as exc3:
            logger.warning("prompt_memory re-init failed: %s", type(exc3).__name__)
            return None
    except Exception as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        logger.warning("prompt_memory connect failed: %s", type(exc).__name__)
        return None


def _touch_conversation(conn: sqlite3.Connection, conversation_id: str, *, bump_revision: bool = False) -> None:
    if bump_revision:
        conn.execute(
            "UPDATE conversations SET updated_at=?, revision=revision+1 WHERE id=?",
            (_now_iso(), conversation_id),
        )
    else:
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?",
            (_now_iso(), conversation_id),
        )


def _conv_exists(conn: sqlite3.Connection, conversation_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
    ).fetchone()
    return row is not None


# ------------------------------------------------------------------ lifecycle

def create_conversation(title: str = "New conversation") -> str | None:
    """Create a conversation, set active if none, return its id. Returns None if storage unavailable."""
    clean = title.strip() if isinstance(title, str) and title.strip() else "New conversation"
    conn = _connect()
    if conn is None:
        return None
    try:
        cid = _new_id()
        now = _now_iso()
        with conn:
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at, revision) VALUES (?,?,?,?,1)",
                (cid, clean[:200], now, now),
            )
            row = conn.execute(
                "SELECT value FROM meta WHERE key='active_conversation_id'"
            ).fetchone()
            if row is None or not row[0]:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('active_conversation_id', ?)",
                    (cid,),
                )
        return cid
    except sqlite3.DatabaseError as exc:
        try:
            conn.close()
        except Exception:
            pass
        if _is_corruption(exc):
            path = _db_path()
            try:
                if path.exists():
                    _quarantine_corrupt(path)
            except Exception:
                pass
        else:
            logger.warning("prompt_memory create failed: %s", type(exc).__name__)
        return None
    except Exception as exc:
        logger.warning("prompt_memory create failed: %s", type(exc).__name__)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_conversations() -> list[dict[str, Any]]:
    """List conversations newest-first with turn counts. Never raises."""
    conn = _connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at, revision FROM conversations ORDER BY updated_at DESC, rowid DESC"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for cid, title, created_at, updated_at, rev in rows:
            try:
                n = conn.execute(
                    "SELECT COUNT(*) FROM turns WHERE conversation_id=?", (cid,)
                ).fetchone()
                count = int(n[0]) if n else 0
            except Exception:
                count = 0
            out.append(
                {
                    "id": cid,
                    "title": title,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "revision": rev,
                    "turn_count": count,
                }
            )
        return out
    except sqlite3.DatabaseError as exc:
        logger.warning("prompt_memory list failed: %s", type(exc).__name__)
        try:
            conn.close()
        except Exception:
            pass
        if _is_corruption(exc):
            try:
                p = _db_path()
                if p.exists():
                    _quarantine_corrupt(p)
            except Exception:
                pass
        return []
    except Exception as exc:
        logger.warning("prompt_memory list failed: %s", type(exc).__name__)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_active_id() -> str | None:
    """Return persisted active conversation id, or None. Never raises."""
    conn = _connect()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='active_conversation_id'"
        ).fetchone()
        if not row or not row[0]:
            return None
        cid = str(row[0])
        # Stale pointer (conversation deleted): report None rather than dangling id.
        if not _conv_exists(conn, cid):
            return None
        return cid
    except Exception as exc:
        logger.warning("prompt_memory get_active failed: %s", type(exc).__name__)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_active_id(conversation_id: str | None) -> None:
    """Select the active conversation (or None to unset). Raises ValueError if unknown id."""
    if conversation_id is None:
        conn = _connect()
        if conn is None:
            return
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('active_conversation_id', '')",
                )
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string or None")
    conn = _connect()
    if conn is None:
        raise ValueError("memory unavailable")
    try:
        with conn:
            if not _conv_exists(conn, conversation_id):
                raise ValueError("unknown conversation id")
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('active_conversation_id', ?)",
                (conversation_id,),
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ------------------------------------------------------------------ turns

def add_user_turn(
    conversation_id: str,
    text: str,
    job_key: str | None = None,
    *,
    source: str = "spoken",
    transcript: str | None = None,
    expected_revision: int | None = None,
) -> str:
    """Store one raw user turn; idempotent on ``job_key``. Returns turn id.

    Only this function writes turns — generated prompts must never be passed
    here. Raises ValueError on bad input / unknown conversation / revision mismatch.
    """
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id required")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    if source not in _VALID_SOURCES:
        raise ValueError("source must be 'spoken' or 'user_note'")
    if job_key is not None and (not isinstance(job_key, str) or not job_key.strip()):
        raise ValueError("job_key must be a non-empty string or None")
    conn = _connect()
    if conn is None:
        raise ValueError("memory unavailable")
    try:
        with conn:
            row_conv = conn.execute(
                "SELECT revision FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if row_conv is None:
                raise ValueError("unknown conversation id")
            current_rev = row_conv[0]
            if expected_revision is not None and current_rev != expected_revision:
                raise ValueError(
                    f"revision mismatch for conversation {conversation_id}: "
                    f"expected {expected_revision}, got {current_rev}"
                )

            # Idempotency: same job_key in the same conversation returns original turn id
            if job_key is not None:
                row = conn.execute(
                    "SELECT id FROM turns WHERE conversation_id=? AND job_key=?",
                    (conversation_id, job_key),
                ).fetchone()
                if row is not None:
                    return str(row[0])

            tid = _new_id()
            conn.execute(
                "INSERT INTO turns (id, conversation_id, created_at, text, source, transcript, job_key)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    tid,
                    conversation_id,
                    _now_iso(),
                    text,
                    source,
                    transcript if isinstance(transcript, str) and transcript else None,
                    job_key,
                ),
            )
            _touch_conversation(conn, conversation_id)
        return tid
    finally:
        try:
            conn.close()
        except Exception:
            pass


def remove_turn(turn_id: str) -> None:
    """Delete one turn; invalidates summaries referencing it; bumps revision. Never raises."""
    if not isinstance(turn_id, str) or not turn_id:
        return
    conn = _connect()
    if conn is None:
        return
    try:
        with conn:
            row = conn.execute(
                "SELECT conversation_id FROM turns WHERE id=?", (turn_id,)
            ).fetchone()
            if row is None:
                return
            conv_id = str(row[0])
            conn.execute("DELETE FROM turns WHERE id=?", (turn_id,))
            _invalidate_summaries_locked(conn, conv_id, {turn_id})
            _touch_conversation(conn, conv_id, bump_revision=True)
    except Exception as exc:
        logger.warning("prompt_memory remove_turn failed: %s", type(exc).__name__)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _invalidate_summaries_locked(
    conn: sqlite3.Connection, conversation_id: str, removed_ids: set[str]
) -> int:
    """Delete summaries whose source_ids intersect removed_ids. Returns count."""
    if not removed_ids:
        return 0
    rows = conn.execute(
        "SELECT id, source_ids_json FROM summaries WHERE conversation_id=?",
        (conversation_id,),
    ).fetchall()
    doomed: list[str] = []
    for sid, raw in rows:
        try:
            srcs = json.loads(raw) if raw else []
        except Exception:
            # Unparseable cache entry: invalidate conservatively.
            doomed.append(str(sid))
            continue
        if isinstance(srcs, list) and any(
            str(s) in removed_ids for s in srcs if isinstance(s, str)
        ):
            doomed.append(str(sid))
    for sid in doomed:
        conn.execute("DELETE FROM summaries WHERE id=?", (sid,))
    return len(doomed)


def clear_conversation(conversation_id: str) -> None:
    """Delete all turns + summaries of one conversation; keep the shell; bumps revision. Never raises."""
    if not isinstance(conversation_id, str) or not conversation_id:
        return
    conn = _connect()
    if conn is None:
        return
    try:
        with conn:
            if not _conv_exists(conn, conversation_id):
                return
            conn.execute("DELETE FROM turns WHERE conversation_id=?", (conversation_id,))
            conn.execute(
                "DELETE FROM summaries WHERE conversation_id=?", (conversation_id,)
            )
            _touch_conversation(conn, conversation_id, bump_revision=True)
    except Exception as exc:
        logger.warning("prompt_memory clear failed: %s", type(exc).__name__)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_conversation(conversation_id: str) -> None:
    """Delete a conversation and all its turns/summaries; fixes active pointer. Never raises."""
    if not isinstance(conversation_id, str) or not conversation_id:
        return
    conn = _connect()
    if conn is None:
        return
    try:
        with conn:
            conn.execute("DELETE FROM turns WHERE conversation_id=?", (conversation_id,))
            conn.execute(
                "DELETE FROM summaries WHERE conversation_id=?", (conversation_id,)
            )
            conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            row = conn.execute(
                "SELECT value FROM meta WHERE key='active_conversation_id'"
            ).fetchone()
            if row is not None and str(row[0]) == conversation_id:
                nxt = conn.execute(
                    "SELECT id FROM conversations ORDER BY updated_at DESC, rowid DESC LIMIT 1"
                ).fetchone()
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('active_conversation_id', ?)",
                    (str(nxt[0]) if nxt else "",),
                )
    except Exception as exc:
        logger.warning("prompt_memory delete failed: %s", type(exc).__name__)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    """Return conversation metadata including current revision, or None. Never raises."""
    if not isinstance(conversation_id, str) or not conversation_id:
        return None
    conn = _connect()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at, revision FROM conversations WHERE id=?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "revision": row[4],
        }
    except Exception as exc:
        logger.warning("prompt_memory get_conversation failed: %s", type(exc).__name__)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Back-compat alias for "Remove selected" conversation action.
remove_conversation = delete_conversation


# ------------------------------------------------------------------ summaries

def save_summary(
    conversation_id: str,
    text: str,
    source_ids: list[str] | tuple[str, ...] | None = None,
    version: int = 1,
    expected_revision: int | None = None,
) -> str:
    """Cache a derived summary over exact source turn IDs. Returns summary id.

    Validation of source IDs and revision check happen atomically within the same transaction.
    Raises ValueError on bad input / unknown conversation / foreign / deleted source IDs / revision mismatch.
    Empty ``source_ids`` is allowed (summary over no turns) but must be a list when given.
    """
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id required")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    srcs = list(source_ids) if source_ids is not None else []
    if not all(isinstance(s, str) and s for s in srcs):
        raise ValueError("source_ids must be a list of non-empty strings")
    try:
        ver = int(version)
    except Exception:
        raise ValueError("version must be an int")
    conn = _connect()
    if conn is None:
        raise ValueError("memory unavailable")
    try:
        with conn:
            row_conv = conn.execute(
                "SELECT revision FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            if row_conv is None:
                raise ValueError("unknown conversation id")
            current_rev = row_conv[0]
            if expected_revision is not None and current_rev != expected_revision:
                raise ValueError(
                    f"revision mismatch for conversation {conversation_id}: "
                    f"expected {expected_revision}, got {current_rev}"
                )

            if srcs:
                placeholders = ",".join("?" for _ in srcs)
                rows = conn.execute(
                    f"SELECT id FROM turns WHERE conversation_id=? AND id IN ({placeholders})",
                    [conversation_id] + srcs,
                ).fetchall()
                found_ids = {r[0] for r in rows}
                missing_ids = set(srcs) - found_ids
                if missing_ids:
                    raise ValueError(f"foreign or unknown source turn IDs for conversation: {missing_ids}")
            sid = _new_id()
            conn.execute(
                "INSERT INTO summaries (id, conversation_id, created_at, text, source_ids_json, version)"
                " VALUES (?,?,?,?,?,?)",
                (
                    sid,
                    conversation_id,
                    _now_iso(),
                    text,
                    json.dumps(srcs, ensure_ascii=False),
                    ver,
                ),
            )
            _touch_conversation(conn, conversation_id)
        return sid
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_summary(conversation_id: str) -> dict[str, Any] | None:
    """Return the latest summary for a conversation, or None. Never raises."""
    if not isinstance(conversation_id, str) or not conversation_id:
        return None
    conn = _connect()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT id, created_at, text, source_ids_json, version FROM summaries"
            " WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        sid, created_at, stext, raw_ids, ver = row
        try:
            srcs = json.loads(raw_ids) if raw_ids else []
        except Exception:
            srcs = []
        return {
            "id": str(sid),
            "conversation_id": conversation_id,
            "created_at": created_at,
            "text": stext,
            "source_ids": srcs if isinstance(srcs, list) else [],
            "version": ver,
        }
    except Exception as exc:
        logger.warning("prompt_memory get_summary failed: %s", type(exc).__name__)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_context(conversation_id: str | None = None) -> dict[str, Any]:
    """Return ``{"conversation_id", "revision", "turns", "summary", "ok", "error"}`` snapshot. Never raises.

    ``conversation_id=None`` resolves to the active conversation;
    when storage is unavailable or corrupt, returns ``{"conversation_id": ..., "revision": 0, "turns": [], "summary": None, "ok": False, "error": ...}``.
    A valid empty conversation returns ``{"conversation_id": cid, "revision": rev, "turns": [], "summary": None, "ok": True, "error": None}``.
    """
    cid = conversation_id or get_active_id()
    if not cid:
        return {"conversation_id": None, "revision": 0, "turns": [], "summary": None, "ok": True, "error": None}
    conn = _connect()
    if conn is None:
        return {"conversation_id": cid, "revision": 0, "turns": [], "summary": None, "ok": False, "error": "storage_unavailable"}
    try:
        crow = conn.execute("SELECT revision FROM conversations WHERE id=?", (cid,)).fetchone()
        if crow is None:
            return {"conversation_id": None, "revision": 0, "turns": [], "summary": None, "ok": True, "error": "unknown_conversation"}
        revision = int(crow[0])

        trows = conn.execute(
            "SELECT id, created_at, text, source, transcript, job_key FROM turns"
            " WHERE conversation_id=? ORDER BY created_at ASC, rowid ASC",
            (cid,),
        ).fetchall()
        turns = [
            {
                "id": str(tid),
                "created_at": created_at,
                "text": ttext,
                "source": src,
                "transcript": tr,
                "job_key": jk,
            }
            for tid, created_at, ttext, src, tr, jk in trows
        ]
        srow = conn.execute(
            "SELECT id, created_at, text, source_ids_json, version FROM summaries"
            " WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (cid,),
        ).fetchone()
        summary: dict[str, Any] | None = None
        if srow is not None:
            sid, sc, st, raw_ids, ver = srow
            try:
                srcs = json.loads(raw_ids) if raw_ids else []
            except Exception:
                srcs = []
            summary = {
                "id": str(sid),
                "conversation_id": cid,
                "created_at": sc,
                "text": st,
                "source_ids": srcs if isinstance(srcs, list) else [],
                "version": ver,
            }
        return {"conversation_id": cid, "revision": revision, "turns": turns, "summary": summary, "ok": True, "error": None}
    except sqlite3.DatabaseError as exc:
        logger.warning("prompt_memory context failed: %s", type(exc).__name__)
        try:
            conn.close()
        except Exception:
            pass
        if _is_corruption(exc):
            try:
                p = _db_path()
                if p.exists():
                    _quarantine_corrupt(p)
            except Exception:
                pass
        return {"conversation_id": cid, "revision": 0, "turns": [], "summary": None, "ok": False, "error": "db_error"}
    except Exception as exc:
        logger.warning("prompt_memory context failed: %s", type(exc).__name__)
        return {"conversation_id": cid, "revision": 0, "turns": [], "summary": None, "ok": False, "error": "unexpected_error"}
    finally:
        try:
            conn.close()
        except Exception:
            pass
