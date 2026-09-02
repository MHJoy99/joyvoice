"""Join-key + retention tests for app/storage/usage_store.py.

Covers: join-key presence/backfill, corrupt-line resilience, prune behavior.
No network. usage.jsonl is redirected to tmp_path via monkeypatched paths.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import paths
from app.storage import usage_store


@pytest.fixture()
def usage_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "usage.jsonl"
    monkeypatch.setattr(paths, "usage_path", lambda: target)
    # Reset process session id so session backfill is deterministic per-test.
    monkeypatch.setattr(usage_store, "_SESSION_ID", None)
    return target


def _read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── join-key presence ──────────────────────────────────────────────────────

def test_append_persists_full_join_keys(usage_file: Path):
    event = usage_store.make_event(
        "llm",
        job_id=42,
        model="gemini-3.6-flash",
        latency_s=1.234,
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        target_language="en",
        engine_mode="cloud",
        style="translate_to_target",
        finish_reason="stop",
    )
    usage_store.append(event)

    rows = _read_rows(usage_file)
    assert len(rows) == 1
    row = rows[0]
    # Required join keys + telemetry fields all present.
    assert row["kind"] == "llm"
    assert row["job_id"] == 42
    assert row["session_id"]  # auto session backfill via make_event=None -> append
    assert row["model"] == "gemini-3.6-flash"
    assert row["latency_s"] == 1.234
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 20
    assert row["total_tokens"] == 120
    assert row["target_language"] == "en"
    assert row["engine_mode"] == "cloud"
    assert row["ts"]  # ISO-8601 present
    datetime.fromisoformat(row["ts"])  # parses
    # Extra fields pass through; None values are dropped.
    assert row["style"] == "translate_to_target"


def test_append_backfills_ts_and_session_id(usage_file: Path):
    usage_store.append({"kind": "asr", "job_id": "7", "model": "m"})
    row = _read_rows(usage_file)[0]
    assert row["session_id"] == usage_store.get_session_id()
    assert row["job_id"] == "7"
    assert "ts" in row
    # Same process reuses the same session id (join key stability).
    usage_store.append({"kind": "paste", "job_id": "7"})
    rows = _read_rows(usage_file)
    assert rows[0]["session_id"] == rows[1]["session_id"]


def test_append_legacy_event_without_join_keys_still_works(usage_file: Path):
    # Backward compat: old callers without job_id/session_id must not raise.
    usage_store.append({"kind": "audio", "model": "m", "latency_s": 0.5})
    usage_store.append({"kind": "text_rewrite", "style": "x"})
    rows = _read_rows(usage_file)
    assert len(rows) == 2
    # Backfilled so even legacy rows are joinable to the session.
    assert all(r["session_id"] for r in rows)
    assert all(r["ts"] for r in rows)
    # Canonical mapping groups legacy aliases with new kinds.
    assert usage_store.canonical_kind("audio") == "asr"
    assert usage_store.canonical_kind("text_rewrite") == "llm"
    summary = usage_store.summarize()
    assert summary["by_kind_canonical"]["asr"] == 1
    assert summary["by_kind_canonical"]["llm"] == 1


def test_append_never_raises_and_thread_safe(usage_file: Path):
    # Non-dict + unserializable + concurrent appends must never raise.
    usage_store.append(None)  # type: ignore[arg-type]
    usage_store.append({"kind": "llm", "bad": object()})
    errors: list[BaseException] = []

    def worker(n: int):
        try:
            for i in range(25):
                usage_store.append({"kind": "llm", "job_id": f"{n}-{i}"})
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert usage_store.verify()["events"] == 100


# ── corrupt-line resilience ────────────────────────────────────────────────

def test_verify_reports_corrupt_lines(usage_file: Path):
    usage_file.write_text(
        '{"kind": "llm", "job_id": 1}\n'
        "NOT-JSON\n"
        '{"kind": "asr", "job_id": 2}\n'
        "[1, 2, 3]\n"
        "\n"
        '{"kind": "paste", "job_id": 3}\n',
        encoding="utf-8",
    )
    report = usage_store.verify()
    assert report["events"] == 3
    assert report["corrupt"] == 2
    assert report["corrupt_lines"] == [2, 4]
    assert report["ok"] is False


def test_summarize_skips_corrupt_lines(usage_file: Path):
    usage_file.write_text(
        '{"kind": "llm", "prompt_tokens": 10, "completion_tokens": 5, '
        '"total_tokens": 15, "latency_s": 1.0, "job_id": 1, "session_id": "s1"}\n'
        "!!!corrupt!!!\n"
        '{"kind": "asr", "prompt_tokens": 4, "latency_s": 2.0, "job_id": 2, '
        '"session_id": "s1"}\n',
        encoding="utf-8",
    )
    summary = usage_store.summarize()
    assert summary["events"] == 2
    assert summary["corrupt"] == 1
    assert summary["prompt_tokens"] == 14
    assert summary["with_job_id"] == 2
    assert summary["unique_job_ids"] == 2


# ── prune behavior ─────────────────────────────────────────────────────────

def test_prune_max_events_keeps_newest_and_drops_corrupt(usage_file: Path):
    for i in range(10):
        usage_store.append({"kind": "llm", "job_id": i})
    with open(usage_file, "a", encoding="utf-8") as fh:
        fh.write("CORRUPT\n")
    stats = usage_store.prune(max_age_days=30, max_events=5)
    assert stats["before"] == 11
    assert stats["dropped_corrupt"] == 1
    assert stats["dropped_overflow"] == 5
    assert stats["after"] == 5
    rows = _read_rows(usage_file)
    assert [r["job_id"] for r in rows] == [5, 6, 7, 8, 9]
    assert usage_store.verify()["ok"] is True


def test_prune_max_age_drops_expired_but_keeps_missing_ts(usage_file: Path):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    now_ts = datetime.now(timezone.utc).isoformat()
    usage_file.write_text(
        json.dumps({"kind": "llm", "job_id": "old", "ts": old_ts}) + "\n"
        + json.dumps({"kind": "llm", "job_id": "new", "ts": now_ts}) + "\n"
        + json.dumps({"kind": "llm", "job_id": "no-ts"}) + "\n",
        encoding="utf-8",
    )
    stats = usage_store.prune(max_age_days=30, max_events=5000)
    assert stats["dropped_expired"] == 1
    rows = _read_rows(usage_file)
    assert {r["job_id"] for r in rows} == {"new", "no-ts"}


def test_prune_missing_file_is_noop(usage_file: Path):
    assert not usage_file.exists()
    stats = usage_store.prune()
    assert stats == {
        "before": 0,
        "after": 0,
        "dropped_corrupt": 0,
        "dropped_expired": 0,
        "dropped_overflow": 0,
        "path": str(usage_file),
    }
