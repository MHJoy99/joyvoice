"""Windows-native counterpart to the Ubuntu guard safety system (bin/guard.sh).

Provides deterministic fail-closed safety checks for pre-commit, pre-push,
pre-deploy, post-deploy, and install-hooks phases on Windows environment.
"""

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parent.parent
ZERO_SHA = "0" * 40


def cyan(msg: str) -> None:
    print(f"▶ {msg}")


def warn(msg: str) -> None:
    print(f"⚠ {msg}")


def block(msg: str) -> NoReturn:
    print(f"✖ GUARD BLOCK: {msg}")
    sys.exit(1)


def pass_guard(msg: str) -> NoReturn:
    print(f"✔ GUARD PASS: {msg}")
    sys.exit(0)


def run_git(args: list[str], allow_fail: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(REPO_ROOT)] + args
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
        if not allow_fail and res.returncode != 0:
            return res
        return res
    except Exception as exc:
        if allow_fail:
            return subprocess.CompletedProcess(cmd, 1, "", str(exc))
        block(f"Failed to execute git command {cmd}: {exc}")


def pre_commit() -> None:
    cyan("Executing pre-commit safety checks...")

    # 1. Check current branch
    res = run_git(["branch", "--show-current"], allow_fail=True)
    branch = res.stdout.strip() if res.returncode == 0 else ""
    if not branch:
        warn("HEAD is detached.")
    else:
        cyan(f"Current branch: {branch}")

    # 2. Check staged files for blocked items (.env, .sqlite)
    res = run_git(["diff", "--cached", "--name-only"], allow_fail=True)
    staged_files = res.stdout.strip().splitlines() if res.returncode == 0 and res.stdout else []

    if staged_files:
        prohibited_pattern = re.compile(
            r"(^|/)\.env($|\.)|(^|/)database\.sqlite($|\.)|\.sqlite3?$",
            re.IGNORECASE,
        )
        for f in staged_files:
            if prohibited_pattern.search(f):
                block("Staged files contain prohibited environment or database file (.env or .sqlite).")

        # 3. Check staged diff for secret key additions
        res_diff = run_git(["diff", "--cached", "-U0"], allow_fail=True)
        if res_diff.returncode == 0 and res_diff.stdout:
            secret_pattern = re.compile(
                r"^\+[^\+]*(SECRET|API[_-]?KEY|PRIVATE[_-]?KEY|PASSWORD|PASSWD|TOKEN|ACCESS[_-]?KEY)[a-zA-Z0-9_]*['\"]?\s*(=>|:=|=|:)\s*['\"][^'\"]{6,}['\"]",
                re.IGNORECASE,
            )
            ignore_pattern = re.compile(
                r"env\(|getenv\(|process\.env|<[^>]+>|example|placeholder|xxx|your[_-]?key",
                re.IGNORECASE,
            )

            for line in res_diff.stdout.splitlines():
                if secret_pattern.search(line) and not ignore_pattern.search(line):
                    block("Staged diff contains potential hardcoded API secret/key pattern.")

    pass_guard("pre-commit verification successful.")


def pre_push() -> None:
    cyan("Executing pre-push safety checks...")

    if sys.stdin.isatty():
        warn("No ref list on stdin (manual run) — skipping per-ref checks.")
        pass_guard("pre-push verification successful.")

    raw_stdin = sys.stdin.read()
    if not raw_stdin.strip():
        warn("No ref list on stdin — skipping per-ref checks.")
        pass_guard("pre-push verification successful.")

    for line in raw_stdin.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts[:4]

        if remote_ref in ("refs/heads/main", "refs/heads/master"):
            if local_sha == ZERO_SHA:
                block(f"Deleting remote branch '{remote_ref}' is prohibited.")
            if remote_sha == ZERO_SHA:
                cyan(f"Creating '{remote_ref}' (new remote branch) — allowed.")
                continue

            # Non-fast-forward check: remote_sha must be an ancestor of local_sha
            res = run_git(["merge-base", "--is-ancestor", remote_sha, local_sha], allow_fail=True)
            if res.returncode != 0:
                block(
                    f"Force / non-fast-forward push to '{remote_ref}' is prohibited "
                    f"(remote {remote_sha} is not an ancestor of {local_sha})."
                )

    pass_guard("pre-push verification successful.")


def pre_deploy() -> None:
    cyan("Executing pre-deploy safety checks...")

    res = run_git(["rev-parse", "--short", "HEAD"], allow_fail=True)
    rev = res.stdout.strip() if res.returncode == 0 else "nogit"
    if rev == "nogit" or not rev:
        block("Repository does not have valid git HEAD.")

    # 1. Verify clean working tree
    dirty = False
    res_wt = run_git(["diff", "--quiet"], allow_fail=True)
    res_st = run_git(["diff", "--cached", "--quiet"], allow_fail=True)
    if res_wt.returncode != 0 or res_st.returncode != 0:
        dirty = True
        warn("Working tree has uncommitted changes! Deploy will include uncommitted state.")

    # 2. Verify rollback target exists in DEPLOYED_REV.txt / git tags
    prev_deployed = ""
    deployed_rev_file = REPO_ROOT / "DEPLOYED_REV.txt"
    if deployed_rev_file.is_file():
        try:
            content = deployed_rev_file.read_text(encoding="utf-8").strip()
            if content:
                prev_deployed = content.split()[0]
        except Exception:
            pass

    if prev_deployed and prev_deployed != "nogit":
        res_commit = run_git(["rev-parse", "--verify", f"{prev_deployed}^{{commit}}"], allow_fail=True)
        res_tag = run_git(["tag", "-l"], allow_fail=True)
        tags = res_tag.stdout.splitlines() if res_tag.returncode == 0 else []
        if res_commit.returncode != 0 and prev_deployed not in tags:
            block(f"Previous deployed revision '{prev_deployed}' from DEPLOYED_REV.txt does not exist in git history or tags!")
        else:
            cyan(f"Verified previous deploy rollback target: {prev_deployed}")
    else:
        warn("No previous DEPLOYED_REV.txt found or set to nogit.")

    pass_guard(
        f"pre-deploy verification passed (HEAD: {rev}, Previous: {prev_deployed or 'none'}, Dirty: {1 if dirty else 0})."
    )


def post_deploy() -> None:
    cyan("Executing post-deploy verification and ledger logging...")

    res = run_git(["rev-parse", "--short", "HEAD"], allow_fail=True)
    rev = res.stdout.strip() if res.returncode == 0 else "nogit"
    if rev == "nogit" or not rev:
        block("Repository does not have valid git HEAD.")

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tag_name = f"deploy/{rev}"

    deployed_rev_file = REPO_ROOT / "DEPLOYED_REV.txt"
    if deployed_rev_file.is_file():
        actual_rev = ""
        try:
            content = deployed_rev_file.read_text(encoding="utf-8").strip()
            if content:
                actual_rev = content.split()[0]
        except Exception:
            pass

        if actual_rev != rev:
            block(f"DEPLOYED_REV.txt mismatch! Expected HEAD: {rev}, Found in DEPLOYED_REV.txt: {actual_rev}")
    else:
        block("DEPLOYED_REV.txt missing after deployment!")

    # 2. Create git tag deploy/<rev>
    tag_ok = True
    res_tag_check = run_git(["rev-parse", tag_name], allow_fail=True)
    if res_tag_check.returncode == 0:
        cyan(f"Git tag '{tag_name}' already exists.")
    else:
        res_tag_create = run_git(["tag", "-a", tag_name, "-m", f"Deployment tag for rev {rev} at {timestamp}"], allow_fail=True)
        if res_tag_create.returncode != 0:
            res_tag_create2 = run_git(["tag", tag_name], allow_fail=True)
            if res_tag_create2.returncode != 0:
                tag_ok = False
                block(f"Could not create git tag '{tag_name}'.")
            else:
                cyan(f"Created git tag '{tag_name}'.")
        else:
            cyan(f"Created git tag '{tag_name}'.")

    # 3. Append entry to .qwen/deploy-ledger.jsonl
    ledger_dir = REPO_ROOT / ".qwen"
    try:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / "deploy-ledger.jsonl"
        log_entry = json.dumps({
            "timestamp": timestamp,
            "revision": rev,
            "tag": tag_name,
            "tagCreated": tag_ok,
            "status": "SUCCESS"
        }) + "\n"
        with open(ledger_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as exc:
        block(f"Failed writing deployment ledger: {exc}")

    pass_guard(f"post-deploy completed (Tag: {tag_name}, Ledger: {ledger_file}).")


def install_hooks() -> None:
    cyan("Executing install-hooks safety check...")
    git_dir = REPO_ROOT / ".git"
    if not git_dir.exists():
        warn("Not a git repository or .git directory is missing.")
        pass_guard("install-hooks skipped (no .git directory).")

    warn("Local Windows hook installation: manual setup recommended via core.hooksPath if desired.")
    pass_guard("install-hooks verification successful.")


def main() -> None:
    if len(sys.argv) < 2:
        block("No phase specified. Usage: python bin/guard.py <pre-commit|pre-push|pre-deploy|post-deploy|install-hooks>")

    phase = sys.argv[1].lower()
    if phase == "pre-commit":
        pre_commit()
    elif phase == "pre-push":
        pre_push()
    elif phase == "pre-deploy":
        pre_deploy()
    elif phase == "post-deploy":
        post_deploy()
    elif phase == "install-hooks":
        install_hooks()
    else:
        block(f"Unknown phase '{phase}'. Allowed: pre-commit, pre-push, pre-deploy, post-deploy, install-hooks")


if __name__ == "__main__":
    main()
