"""Collect a JoyVoice diagnostics bundle (.zip) on disk.

Headless-friendly: stdlib + app.storage only, no PySide6 / audio imports.

Usage:
    python tools/collect_logs.py [--output PATH] [--tail N]

Contents:
    joyvoice.log (+ rotated joyvoice.log* siblings)
    usage.jsonl (if present)
    settings-sanitized.json (api_key / secrets redacted — never raw)
    system_info.json, usage_summary.json, version.txt, log_tail_N.txt

Exit code 0 on success, 1 on zip-write failure. Per-file adds are
best-effort so one missing file never fails the bundle.
"""

from __future__ import annotations

import argparse
import glob
import json
import platform
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Allow `python tools/collect_logs.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SENSITIVE_SUBSTRINGS = ("api_key", "token", "secret", "password")


def sanitize_settings(settings: dict) -> dict:
    clean: dict = {}
    try:
        for key, value in dict(settings or {}).items():
            lowered = str(key).lower()
            if any(s in lowered for s in SENSITIVE_SUBSTRINGS):
                if isinstance(value, str) and value:
                    clean[key] = "***REDACTED*** (len=%d)" % len(value)
                elif value:
                    clean[key] = "***REDACTED***"
                else:
                    clean[key] = value
            else:
                clean[key] = value
    except Exception:
        return {"<sanitize failed>": True}
    return clean


def load_sanitized_settings() -> dict:
    try:
        from app.storage import settings_store

        return sanitize_settings(settings_store.load())
    except Exception:
        pass
    try:
        from app.storage import paths

        raw = json.loads(paths.settings_path().read_text(encoding="utf-8"))
        return sanitize_settings(raw if isinstance(raw, dict) else {})
    except Exception as exc:
        return {"error": f"could not load settings: {exc}"}


def get_version() -> str:
    try:
        from app import crash_guard

        return crash_guard.get_version()
    except Exception:
        pass
    # Fallback: parse pyproject.toml directly.
    try:
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8", errors="replace"
        )
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("version"):
                parts = s.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip("\"'")
    except Exception:
        pass
    return "unknown"


def tail_log(n: int) -> str:
    try:
        from app.storage import paths

        path = paths.log_path()
        if not path.exists():
            return f"(no log file yet at {path})"
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-n:])
    except Exception as exc:
        return f"(could not read log: {exc})"


def get_system_info() -> dict:
    info: dict = {}
    try:
        info["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        info["timestamp_utc"] = ""
    info["version"] = get_version()
    try:
        from app import crash_guard

        info["session_id"] = crash_guard.get_session_id()
    except Exception:
        info["session_id"] = "n/a (headless collect)"
    try:
        info["python"] = sys.version.replace("\n", " ")
        info["platform"] = platform.platform()
    except Exception:
        pass
    try:
        from app.storage import paths

        info["data_dir"] = str(paths.data_dir())
        info["log_path"] = str(paths.log_path())
        info["log_exists"] = paths.log_path().exists()
        info["usage_path"] = str(paths.usage_path())
    except Exception as exc:
        info["paths_error"] = str(exc)
    info["settings_sanitized"] = load_sanitized_settings()
    return info


def get_usage_summary() -> dict:
    try:
        from app.storage import usage_store

        s = usage_store.summarize()
        return s if isinstance(s, dict) else {"events": 0}
    except Exception as exc:
        return {"events": 0, "error": str(exc)}


def iter_log_candidates() -> list[Path]:
    try:
        from app.storage import paths

        base = paths.log_path()
    except Exception:
        return []
    found: list[Path] = []
    try:
        if base.exists():
            found.append(base)
        for match in glob.glob(str(base) + "*"):
            try:
                p = Path(match)
                if p.is_file() and p not in found:
                    found.append(p)
            except Exception:
                continue
    except Exception:
        pass
    return sorted(found)


def collect_bundle(zip_path: Path, tail_n: int = 200) -> Path:
    dest = Path(zip_path)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)

    system_info = get_system_info()
    usage_summary = get_usage_summary()

    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        added_any_log = False
        for log_file in iter_log_candidates():
            try:
                zf.write(log_file, arcname=log_file.name)
                added_any_log = True
            except Exception as exc:
                print(f"warn: skip log {log_file}: {exc}")
        if not added_any_log:
            zf.writestr("joyvoice.log.missing.txt", "No joyvoice.log found on disk.\n")

        zf.writestr(f"log_tail_{tail_n}.txt", tail_log(tail_n))

        try:
            from app.storage import paths

            usage_path = paths.usage_path()
            if usage_path.exists():
                zf.write(usage_path, arcname=usage_path.name)
            else:
                zf.writestr("usage.jsonl.missing.txt", "No usage.jsonl on disk yet.\n")
        except Exception as exc:
            print(f"warn: usage.jsonl skipped: {exc}")

        zf.writestr(
            "settings-sanitized.json",
            json.dumps(system_info.get("settings_sanitized", {}), indent=2, ensure_ascii=False),
        )
        zf.writestr(
            "system_info.json",
            json.dumps(system_info, indent=2, ensure_ascii=False, default=str),
        )
        zf.writestr(
            "usage_summary.json",
            json.dumps(usage_summary, indent=2, ensure_ascii=False, default=str),
        )
        zf.writestr("version.txt", str(system_info.get("version", "unknown")) + "\n")

    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect JoyVoice diagnostics bundle")
    parser.add_argument("--output", "-o", default=None, help="Output .zip path")
    parser.add_argument("--tail", type=int, default=200, help="Log tail lines to embed")
    args = parser.parse_args(argv)

    if args.output:
        out = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = Path.cwd() / f"joyvoice-diagnostics-{stamp}.zip"

    try:
        dest = collect_bundle(out, tail_n=args.tail)
    except Exception as exc:
        print(f"ERROR: could not write bundle: {exc}", file=sys.stderr)
        return 1

    print(f"Diagnostics bundle: {dest}")
    try:
        with zipfile.ZipFile(dest) as zf:
            for name in zf.namelist():
                info = zf.getinfo(name)
                print(f"  {name} ({info.file_size} bytes)")
    except Exception:
        pass
    # Safety reminder: raw api_key must never be in the bundle.
    print("Note: settings are sanitized (api_key redacted). Safe to attach to a bug report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
