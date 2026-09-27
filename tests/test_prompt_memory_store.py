"""Focused storage-behavior tests for docs/sol-prompt-memory-plan.md.

Scope: ONLY this file is owned by this task. No production files are edited
here. Tests target the storage agent's ``app/storage/prompt_memory_store.py``
API exactly as it appears (see that module's "Public API contract" docstring):

- create_conversation / list_conversations
- get_active_id / set_active_id (persisted, survives restart)
- get_context(conversation_id) -> {conversation_id, turns, summary}
- add_user_turn(conversation_id, text, job_key) idempotent on job_key
- remove_turn -> invalidates referencing summaries
- clear_conversation (empties turns+summaries, keeps shell) vs
  create_conversation (New: empty, keeps old selectable) vs
  delete_conversation/remove_conversation (Remove selected)
- save_summary / get_summary
- corruption -> quarantine (damaged bytes preserved aside, fresh DB, no raise)
- DB under app.storage.paths.prompt_memory_db_path() (APPDATA / portable)

Isolation: DB redirected per-test via JV_PROMPT_MEMORY_DB env + monkeypatched
paths.prompt_memory_db_path. No network.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import paths  # noqa: E402
from app.storage import prompt_memory_store as store  # noqa: E402


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "prompt_memory.sqlite"
    monkeypatch.setenv("JV_PROMPT_MEMORY_DB", str(target))
    if hasattr(paths, "prompt_memory_db_path"):
        monkeypatch.setattr(paths, "prompt_memory_db_path", lambda: target)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    return target


# ── contract surface ─────────────────────────────────────────────────────

def test_store_module_and_db_path_contract():
    assert hasattr(store, "create_conversation")
    assert hasattr(store, "get_context")
    assert hasattr(store, "add_user_turn")
    assert hasattr(paths, "prompt_memory_db_path"), (
        "missing behavior [db path]: paths.prompt_memory_db_path required "
        "by plan step 2."
    )


def test_db_path_lives_under_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("JV_PROMPT_MEMORY_DB", raising=False)
    fake_appdata = tmp_path / "APPDATA"
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setattr(paths, "is_portable", lambda: False)
    resolved = Path(paths.prompt_memory_db_path())
    assert str(resolved).startswith(str(fake_appdata / "JoyVoice")), (
        f"DB path {resolved} not under fake %APPDATA%/JoyVoice"
    )


def test_db_path_respects_portable_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("JV_PROMPT_MEMORY_DB", raising=False)
    monkeypatch.setattr(paths, "is_portable", lambda: True)
    monkeypatch.setattr(paths, "app_root", lambda: tmp_path)
    resolved = Path(paths.prompt_memory_db_path())
    assert str(resolved).startswith(str(tmp_path / "data")), (
        f"portable DB path {resolved} not under <app>/data"
    )


# ── behavior: isolation ──────────────────────────────────────────────────

def test_conversation_isolation(isolated_db: Path):
    conv_a = store.create_conversation("A")
    conv_b = store.create_conversation("B")
    store.add_user_turn(conv_a, "keep normal F8 dictation unchanged", "job-a1")
    store.add_user_turn(conv_b, "unrelated other conversation", "job-b1")

    store.set_active_id(conv_a)
    ctx = store.get_context(store.get_active_id())
    texts = [t["text"] for t in ctx["turns"]]
    assert any("F8 dictation" in t for t in texts)
    assert not any("unrelated" in t for t in texts)

    store.set_active_id(conv_b)
    ctx2 = store.get_context(store.get_active_id())
    texts2 = [t["text"] for t in ctx2["turns"]]
    assert any("unrelated" in t for t in texts2)
    assert not any("F8 dictation" in t for t in texts2)


# ── behavior: active selection survives restart ──────────────────────────

def test_active_selection_survives_restart(isolated_db: Path):
    conv_a = store.create_conversation("A")
    conv_b = store.create_conversation("B")
    store.set_active_id(conv_b)
    assert store.get_active_id() == conv_b
    # "Restart": fresh connections against the same file must agree.
    assert store.get_active_id() == conv_b
    ids = {c["id"] for c in store.list_conversations()}
    assert {conv_a, conv_b} <= ids


# ── behavior: idempotent turns ───────────────────────────────────────────

def test_turn_record_is_idempotent_per_job(isolated_db: Path):
    conv = store.create_conversation("idem")
    store.set_active_id(conv)
    tid1 = store.add_user_turn(conv, "prepare the deployment", "job-123")
    tid2 = store.add_user_turn(conv, "prepare the deployment", "job-123")  # retry
    assert tid1 == tid2
    ctx = store.get_context(conv)
    matching = [t for t in ctx["turns"] if t["text"] == "prepare the deployment"]
    assert len(matching) == 1


def test_generated_prompt_must_not_be_stored_as_turn(isolated_db: Path):
    """Only add_user_turn writes turns: no generated-prompt writer exists."""
    assert hasattr(store, "add_user_turn")
    for name in ("add_prompt", "add_generated", "save_prompt", "record_prompt"):
        assert not hasattr(store, name), (
            f"contract violation: {name} would allow storing generated prompts "
            "as user turns (plan forbids this)."
        )


# ── behavior: summary invalidation after removal ─────────────────────────

def test_summary_invalidated_after_source_removed(isolated_db: Path):
    conv = store.create_conversation("summ")
    store.add_user_turn(conv, "use flag X", "job-x")
    turn_y = store.add_user_turn(conv, "replace X with Y", "job-y")
    ctx = store.get_context(conv)
    source_ids = [t["id"] for t in ctx["turns"]]
    store.save_summary(conv, "user corrected X to Y", source_ids)

    store.remove_turn(turn_y)
    assert store.get_summary(conv) is None, (
        "removed source turn still served via summary"
    )
    ctx2 = store.get_context(conv)
    assert ctx2["summary"] is None
    assert all(t["id"] != turn_y for t in ctx2["turns"])


# ── behavior: New vs Clear vs Remove semantics ───────────────────────────

def test_new_keeps_old_conversations_selectable(isolated_db: Path):
    old = store.create_conversation("old")
    store.add_user_turn(old, "old statement", "job-old")
    new = store.create_conversation("new")
    assert new != old
    assert store.get_context(new)["turns"] == []
    assert len(store.get_context(old)["turns"]) == 1
    ids = {c["id"] for c in store.list_conversations()}
    assert old in ids and new in ids


def test_clear_empties_active_memory_but_keeps_shell(isolated_db: Path):
    conv = store.create_conversation("to-clear")
    store.set_active_id(conv)
    store.add_user_turn(conv, "do not move the live tag", "job-c1")
    ctx = store.get_context(conv)
    store.save_summary(conv, "live-tag rule", [t["id"] for t in ctx["turns"]])
    store.clear_conversation(conv)
    ctx2 = store.get_context(conv)
    assert ctx2["turns"] == []
    assert ctx2["summary"] is None
    assert store.get_summary(conv) is None
    # Shell conversation itself survives Clear (Remove is the delete path).
    assert conv in {c["id"] for c in store.list_conversations()}


def test_remove_selected_deletes_conversation(isolated_db: Path):
    conv = store.create_conversation("doomed")
    store.add_user_turn(conv, "ephemeral", "job-d1")
    store.remove_conversation(conv)
    assert conv not in {c["id"] for c in store.list_conversations()}
    assert store.get_context(conv)["turns"] == []


# ── behavior: corruption quarantine, no overwrite, no raise ──────────────

def test_corrupt_db_quarantined_not_overwritten(isolated_db: Path):
    garbage = b"\x00\x01CORRUPT-sqlite-body\xff\xfe"
    isolated_db.parent.mkdir(parents=True, exist_ok=True)
    isolated_db.write_bytes(garbage)

    # Any entry point must survive corruption without raising and without
    # destroying the damaged bytes: they are moved aside (quarantine), and a
    # fresh working DB starts at the original path.
    convs = store.list_conversations()
    assert convs == []
    assert store.get_active_id() is None

    backups = [
        p for p in isolated_db.parent.iterdir()
        if p.name.startswith(isolated_db.stem + ".corrupt-") and p.suffix == ".db"
    ]
    assert backups, "corrupt DB was not quarantined aside"
    assert any(p.read_bytes() == garbage for p in backups), (
        "damaged bytes were not preserved in the quarantine backup "
        "(plan forbids silent overwrite/data loss)."
    )
    # Fresh DB at the original path is usable.
    fresh = store.create_conversation("after-corruption")
    assert store.get_context(fresh)["turns"] == []
    # NOTE: files shorter than the 100-byte SQLite header are treated by
    # SQLite as empty DBs, so no DatabaseError is asserted on the backup;
    # byte-preservation above is the data-loss guard.


def test_env_override_selects_temporary_db(isolated_db: Path):
    assert os.environ.get("JV_PROMPT_MEMORY_DB") == str(isolated_db)
    conv = store.create_conversation("tmp-check")
    assert isolated_db.exists()
    assert conv in {c["id"] for c in store.list_conversations()}


# ── storage coordinator findings (peer review follow-ups) ────────────────

def test_lock_or_permission_operational_error_must_not_quarantine(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    """Lock/permission OperationalError must NOT quarantine or move a healthy DB."""
    conv = store.create_conversation("healthy")
    store.add_user_turn(conv, "critical data", "job-lock-1")
    assert isolated_db.exists()
    original_bytes = isolated_db.read_bytes()

    # Simulate a transient lock / permission error during connect
    real_connect = sqlite3.connect

    def fake_connect(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", fake_connect)
    # The call fails safely without raising to the caller
    res = store.list_conversations()
    assert res == []

    # Restore real connect: verify NO quarantine file was created and original file was untouched
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    quarantine_files = [
        p for p in isolated_db.parent.iterdir()
        if p.name.startswith(isolated_db.stem + ".corrupt-") and p.suffix == ".db"
    ]
    assert len(quarantine_files) == 0, (
        f"transient lock error caused healthy DB to be quarantined! {quarantine_files}"
    )
    assert isolated_db.exists()
    assert isolated_db.read_bytes() == original_bytes
    # Data is still intact
    ctx = store.get_context(conv)
    assert len(ctx["turns"]) == 1
    assert ctx["turns"][0]["text"] == "critical data"


def test_create_conversation_returns_none_or_raises_when_unavailable(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    """When DB is unavailable, create_conversation must NOT return a fake successful ID."""
    def fake_connect(*args, **kwargs):
        return None

    monkeypatch.setattr(store, "_connect", fake_connect)
    # If storage is completely down, create_conversation returning a fake uuid implies success
    # while nothing was persisted. It should return None (or empty str / raise), not a valid ID
    # that succeeds silently.
    cid = store.create_conversation("will-fail")
    assert cid is None or cid == "", (
        f"create_conversation returned a fake success ID {cid!r} when storage was unavailable"
    )


def test_save_summary_rejects_foreign_or_removed_turn_ids(isolated_db: Path):
    """save_summary rejects source IDs not in current conversation (or removed / racing IDs)."""
    conv_a = store.create_conversation("A")
    conv_b = store.create_conversation("B")
    tid_a = store.add_user_turn(conv_a, "turn in A", "job-a-sum")
    tid_b = store.add_user_turn(conv_b, "turn in B", "job-b-sum")

    # Trying to attach turn from conversation B to a summary in conversation A must be rejected
    with pytest.raises(ValueError, match="foreign|unknown|invalid|not found"):
        store.save_summary(conv_a, "summary of A", [tid_b])

    # Trying to attach a non-existent / deleted turn ID must also be rejected
    with pytest.raises(ValueError, match="foreign|unknown|invalid|not found"):
        store.save_summary(conv_a, "summary of A", ["non-existent-id"])

    # Valid turn ID in conv_a succeeds
    sid = store.save_summary(conv_a, "valid summary", [tid_a])
    assert sid is not None


def test_idempotency_job_key_does_not_leak_or_return_turn_from_other_conv(
    isolated_db: Path,
):
    """Idempotency job_key must be scoped or reject collisions across different conversations."""
    conv_a = store.create_conversation("A")
    conv_b = store.create_conversation("B")
    tid_a = store.add_user_turn(conv_a, "turn in A", "shared-job-key")

    # If the same job_key is reused in conversation B:
    # It must NOT return tid_a in conversation B! Either raise ValueError or not associate with conv_a.
    try:
        tid_b = store.add_user_turn(conv_b, "turn in B", "shared-job-key")
        # If it returned a turn, it MUST NOT be tid_a, and conv_b's context must not leak conv_a's turn
        assert tid_b != tid_a, "add_user_turn in conv_b returned turn ID from conv_a due to global job_key collision!"
        ctx_b = store.get_context(conv_b)
        assert all(t["id"] != tid_a for t in ctx_b["turns"])
    except (ValueError, sqlite3.IntegrityError):
        # Rejecting a reused job_key across different conversations is also valid
        pass


def test_get_context_differentiates_failure_from_empty(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    """get_context must differentiate DB failure (error/None) from a valid empty conversation."""
    conv = store.create_conversation("empty-conv")
    # Valid empty conversation
    valid_ctx = store.get_context(conv)
    assert valid_ctx["conversation_id"] == conv
    assert valid_ctx["turns"] == []
    assert valid_ctx["summary"] is None
    assert valid_ctx.get("error") is None or valid_ctx.get("ok") is not False

    # Simulate DB failure
    def fake_connect(*args, **kwargs):
        return None

    monkeypatch.setattr(store, "_connect", fake_connect)
    fail_ctx = store.get_context(conv)
    # The client needs to know whether the context failed to load vs was genuinely empty,
    # e.g., error flag or ok is False, or returns an explicit error indication
    has_error_flag = fail_ctx.get("error") is not None or fail_ctx.get("ok") is False or fail_ctx.get("status") == "error"
    assert has_error_flag, (
        f"get_context returned indistinguishable output on failure: {fail_ctx}"
    )


# ── revision guards & atomic transaction integrity ────────────────────────

def test_clear_or_remove_revision_guard_on_save_summary(isolated_db: Path):
    """Clearing or removing turns bumps revision, rejecting stale summaries with expected_revision."""
    conv = store.create_conversation("rev-conv")
    t1 = store.add_user_turn(conv, "first turn", "j1")
    t2 = store.add_user_turn(conv, "second turn", "j2")

    # Read context and capture revision
    ctx = store.get_context(conv)
    initial_rev = ctx.get("revision")
    assert initial_rev is not None and initial_rev >= 1

    # Simulate race: user removes turn t2 while summarizer was generating summary
    store.remove_turn(t2)
    after_remove_ctx = store.get_context(conv)
    assert after_remove_ctx["revision"] > initial_rev, "remove_turn did not bump conversation revision"

    # Summarizer attempts to save summary using stale expected_revision
    with pytest.raises(ValueError, match="revision mismatch"):
        store.save_summary(conv, "stale summary", [t1], expected_revision=initial_rev)

    # Summarizer attempts with current revision and valid remaining turn t1 succeeds
    current_rev = after_remove_ctx["revision"]
    sid = store.save_summary(conv, "fresh summary", [t1], expected_revision=current_rev)
    assert sid is not None

    # Now clear_conversation is called concurrently
    store.clear_conversation(conv)
    after_clear_ctx = store.get_context(conv)
    assert after_clear_ctx["revision"] > current_rev, "clear_conversation did not bump conversation revision"

    # Attempting to save summary against the pre-clear revision fails
    with pytest.raises(ValueError, match="revision mismatch"):
        store.save_summary(conv, "post-clear stale summary", [t1], expected_revision=current_rev)


def test_clear_or_remove_revision_guard_on_add_user_turn(isolated_db: Path):
    """add_user_turn rejects append if expected_revision does not match (detects concurrent clear)."""
    conv = store.create_conversation("rev-turn-conv")
    store.add_user_turn(conv, "initial turn", "j1")
    ctx = store.get_context(conv)
    rev = ctx["revision"]

    # Clear happens concurrently
    store.clear_conversation(conv)

    # Attempting to add a turn with expected_revision matching pre-clear revision fails
    with pytest.raises(ValueError, match="revision mismatch"):
        store.add_user_turn(conv, "racing turn", "j2", expected_revision=rev)


def test_phantom_or_cross_conversation_summary_rejection_in_same_transaction(isolated_db: Path):
    """Summary referencing mixed turns (valid local turn + foreign turn) rolls back completely."""
    conv_a = store.create_conversation("A")
    conv_b = store.create_conversation("B")
    t_a = store.add_user_turn(conv_a, "turn A", "j-a")
    t_b = store.add_user_turn(conv_b, "turn B", "j-b")

    # Mixed source IDs: t_a is valid for A, but t_b belongs to B
    with pytest.raises(ValueError, match="foreign|unknown|invalid|not found"):
        store.save_summary(conv_a, "mixed summary", [t_a, t_b])

    # Verify atomic rollback: no summary was inserted for conv_a
    assert store.get_summary(conv_a) is None
    ctx_a = store.get_context(conv_a)
    assert ctx_a["summary"] is None


def test_locked_db_does_not_quarantine_during_writes(isolated_db: Path, monkeypatch: pytest.MonkeyPatch):
    """Lock OperationalError during active writes (add_turn / save_summary) must NEVER quarantine."""
    conv = store.create_conversation("healthy-writes")
    store.add_user_turn(conv, "initial text", "j-init")

    # Hold an EXCLUSIVE write lock from another connection to genuinely trigger SQLITE_BUSY
    locking_conn = sqlite3.connect(str(isolated_db), timeout=0.1)
    locking_conn.execute("BEGIN EXCLUSIVE")

    # Temporarily monkeypatch connect timeout to 0.05s so the test runs fast
    orig_connect = store._connect

    def fast_timeout_connect(*args, **kwargs):
        path = store._db_path()
        try:
            return sqlite3.connect(str(path), timeout=0.05, check_same_thread=False)
        except Exception:
            return None

    monkeypatch.setattr(store, "_connect", fast_timeout_connect)

    try:
        # With active exclusive lock, add_user_turn will fail due to operational error / locked
        with pytest.raises((sqlite3.OperationalError, ValueError)):
            store.add_user_turn(conv, "blocked turn", "j-blocked")
    finally:
        locking_conn.rollback()
        locking_conn.close()

    # Verify NO quarantine files exist
    quarantine_files = [
        p for p in isolated_db.parent.iterdir()
        if p.name.startswith(isolated_db.stem + ".corrupt-") and p.suffix == ".db"
    ]
    assert len(quarantine_files) == 0, (
        f"Write lock error triggered corruption quarantine! {quarantine_files}"
    )


# ── plan spec & privacy invariants ────────────────────────────────────────

def test_source_types_restricted_to_spoken_and_user_note(isolated_db: Path):
    """Source must be 'spoken' or 'user_note' only; invalid sources rejected."""
    conv = store.create_conversation("sources")
    # Valid sources succeed
    tid1 = store.add_user_turn(conv, "voice input", "j-src-1", source="spoken")
    tid2 = store.add_user_turn(conv, "user typed note", "j-src-2", source="user_note")
    assert tid1 is not None and tid2 is not None

    # Invalid sources must be rejected with ValueError
    for bad_source in ("assistant", "system", "generated", "bot", "ai", "", None):
        with pytest.raises(ValueError, match="source must be 'spoken' or 'user_note'"):
            store.add_user_turn(conv, "bad source text", "j-bad", source=bad_source)  # type: ignore[arg-type]


def test_raw_turns_preserved_on_compression_summary(isolated_db: Path):
    """Compression retains raw turns: saving a summary does not delete or prune raw turns."""
    conv = store.create_conversation("compaction-retention")
    t1 = store.add_user_turn(conv, "turn 1 raw content", "j-c1")
    t2 = store.add_user_turn(conv, "turn 2 raw content", "j-c2")
    t3 = store.add_user_turn(conv, "turn 3 raw content", "j-c3")

    ctx_before = store.get_context(conv)
    assert len(ctx_before["turns"]) == 3

    # Generate a derived summary over t1 and t2
    sid = store.save_summary(conv, "summary of first two turns", [t1, t2])
    assert sid is not None

    # Verify ALL 3 raw turns remain intact in context
    ctx_after = store.get_context(conv)
    assert len(ctx_after["turns"]) == 3
    turn_ids = [t["id"] for t in ctx_after["turns"]]
    assert turn_ids == [t1, t2, t3]
    assert ctx_after["summary"] is not None
    assert ctx_after["summary"]["id"] == sid
    assert ctx_after["summary"]["source_ids"] == [t1, t2]


def test_privacy_no_user_content_in_exceptions_or_logs(
    isolated_db: Path, caplog: pytest.LogCaptureFixture
):
    """Privacy rule: no user text, transcripts, or summaries are ever in errors or logs."""
    conv = store.create_conversation("privacy")
    secret_text = "SECRET_USER_PASSWORD_ALPHA_BRAVO_987654"
    secret_transcript = "SECRET_TRANSCRIPT_RAW_AUDIO_TOKENS"

    import logging
    caplog.set_level(logging.DEBUG)

    # 1. Validation failure on bad source
    with pytest.raises(ValueError) as excinfo:
        store.add_user_turn(
            conv,
            secret_text,
            "j-priv-1",
            source="invalid_source",  # triggers ValueError
            transcript=secret_transcript,
        )
    err_str = str(excinfo.value)
    assert secret_text not in err_str, "secret user text leaked in ValueError message"
    assert secret_transcript not in err_str, "secret transcript leaked in ValueError message"

    # 2. Validation failure on unknown conversation
    with pytest.raises(ValueError) as excinfo2:
        store.add_user_turn(
            "non-existent-conv-id",
            secret_text,
            "j-priv-2",
            transcript=secret_transcript,
        )
    err_str2 = str(excinfo2.value)
    assert secret_text not in err_str2, "secret user text leaked in unknown conv error"
    assert secret_transcript not in err_str2, "secret transcript leaked in unknown conv error"

    # 3. Check logs to ensure secret text was not logged
    log_text = caplog.text
    assert secret_text not in log_text, "secret user text leaked in logger output"
    assert secret_transcript not in log_text, "secret transcript leaked in logger output"


def test_job_key_param_name_exact_contract(isolated_db: Path):
    """Explicit test that job_key parameter is supported as both positional and keyword argument."""
    conv = store.create_conversation("param-check")
    # Positional
    tid1 = store.add_user_turn(conv, "text 1", "key-pos")
    # Keyword
    tid2 = store.add_user_turn(conv, "text 2", job_key="key-kw")
    assert tid1 is not None and tid2 is not None



