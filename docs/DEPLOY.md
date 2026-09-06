# JoyVoice Deploy Guide — commit nicely, make it live

> Short operator version. Canonical checklist: `docs/RELEASE.md`. Order is always: verify → commit → push → tag → build exact tag → release.

## 1. Prerequisites

```cmd
.venv\Scripts\python.exe --version
:: must be Python 3.11
git status --short
git log --oneline -n 5
python bin/guard.py pre-commit
```

## 2. Commit nicely

- Scope: only the requested files. Preserve unrelated work. Never `git add -A` / `git add .`.
- Message style (matches log: lowercase scope, imperative summary):
  - `docs: v2.4.1 release notes and status evidence`
  - `logging: fix crash-bundle path on frozen build`
  - `growth: polish README community section`
- Order: verify → commit → push. Never commit before `git diff --check` + guard pass.
- Inspect before every commit:

```cmd
git status --short
git diff --check
git diff
python bin/guard.py pre-commit
git add <requested-file-1> <requested-file-2>
git status --short
git commit -m "<scope>: <imperative summary>"
python bin/guard.py pre-push
git push origin master
```

- Never stage (per `.gitignore`): `.env`, `.env.*`, `secrets.json`, `keys.json`, `private/`, `*.pem`, `*.key`, `usage.jsonl`, `*.exe`, `dist/`, `build/`, `release/`, `*.log`, `logs/`, `models/`, `data/`, `portable.txt`, `__pycache__/`.
- Never force-push. Never publish from a dirty tree. Never include secrets/credentials/API keys.

## 3. Version surfaces + SEO (before tagging)

- Sync every surface to the new version: `pyproject.toml`, `schema.json`, `README.md`, `CHANGELOG.md`, `AGENTS.md`, `AI_STATUS.md`.
- SEO/discovery check: `llms.txt`, `llms-full.txt`, `schema.json`, `index.html`, `README.md`, `robots.txt`, plus repo topics + GitHub homepage/description reference canonical URLs and the new version.

```cmd
git status --short
git diff --stat
git diff --check
python bin/guard.py pre-commit
```

## 4. Tag, build the exact tag, release

```cmd
git push origin master
git fetch --tags
git tag -a vX.Y.Z -m "JoyVoice vX.Y.Z"
git push origin vX.Y.Z
git checkout vX.Y.Z
git describe --exact-match HEAD
git status --short
:: must be clean before building
build_exe.bat
:: authoritative spec: JoyVoice.spec (build_exe.bat runs PyInstaller --clean on it)
dir dist\JoyVoice.exe
:: smoke-check: launch, version, expected packaged behavior
gh release create vX.Y.Z "dist\JoyVoice.exe" --title "JoyVoice vX.Y.Z"
gh release view vX.Y.Z
:: confirm: tag, title/version, published state, asset name, availability, download URL
```

- Close out `AI_STATUS.md` with release evidence (tag, asset, verification output), then commit/push that update on `master` per §2.

## 5. Stop and report (do not bypass)

- Stop and report if any gate fails: guard (`pre-commit`/`pre-push`), tests, `git diff --check`, auth (`gh`), or GitHub capability (push/tag/release).
- Never work around a failed gate silently. Never force-push, never rebuild from a dirty tree, never ship secrets.
