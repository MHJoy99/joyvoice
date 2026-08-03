# JoyVoice Bug-Fix-to-Public-Release Checklist

This is the canonical checklist for moving a reported JoyVoice bug from a fix to a public release. Follow the sequence automatically after an issue is reported; do not skip a gate or silently work around a failure.

## 1. Fix the reported issue

- When a user reports an issue, fix it.
- Keep the change scoped to the requested fix and preserve unrelated work.

## 2. Verify the fix and prepare the tree

- Run the project tests and the isolated import checks required by `AGENTS.md`.
- Run `git diff --check`.
- Run `python bin/guard.py pre-commit`.
- Confirm that `.env`, credentials, EXEs, `dist`, and release artifacts are not staged.
- Update all version surfaces: `pyproject.toml`, `schema.json`, `README.md`, `CHANGELOG.md`, `AGENTS.md`, and `AI_STATUS.md`.
- Inspect `git status`, `git diff`, and `git log`.
- Commit only the requested files.

## 3. Synchronize the release commit

- Run the guard pre-push check: `python bin/guard.py pre-push`.
- Push the release commit to the required branch: `git push origin master`.
- Fetch tags: `git fetch --tags`.
- Create and push the annotated version tag:

  ```cmd
  git tag -a vX.Y.Z -m "JoyVoice vX.Y.Z"
  git push origin vX.Y.Z
  ```

## 4. Build and verify the exact tag

- Check out the exact tagged commit, and confirm `HEAD` resolves to `vX.Y.Z` before building.
- Build from that exact tag with `build_exe.bat` and the authoritative `JoyVoice.spec`.
- Verify `dist\JoyVoice.exe` exists and passes the release smoke checks, including launch/version and the expected packaged behavior.

## 5. Publish and confirm the public release

- Publish the tagged release with `gh release create`, attaching `dist\JoyVoice.exe`.
- Verify the public release metadata and asset: tag, title/version, published state, asset name, asset availability, and download URL.
- Update `AI_STATUS.md` with the release closeout and machine-verified evidence.

## Release safety rules

- Never force-push.
- Never publish from a dirty tree.
- Never include secrets, including `.env` files, credentials, API keys, or private release material.
- Stop and report if a guard, test, authentication step, or GitHub capability blocks the sequence. Do not bypass the failed gate.
