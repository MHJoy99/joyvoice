# JoyVoice v2.5.4

## Safety Patch

Ships the clause-level negation guard from the tagged but unpublished v2.5.3
source. The public v2.5.2 lexical guard could accept a destructive command
when its source negation appeared more than six words before the verb. Negation
now applies across the lexical clause; explicit `but` clauses remain separate.
Ambiguous mixed-action clauses may fall back to manual review.

## Production Coverage

New mocked, no-network tests exercise the actual `CloudASRWorker.run()` chunked
path: translation-only prefix salvage, failed-tail-only Google recovery, and
malformed `None` chunk handling without falsely marking output complete. The
standalone `transcribe_chunks_resilient` helper has no production caller. The
live worker skips only all-zero PCM before a native request; the helper uses a
different near-zero silence threshold.

## Packaged Version

The v2.5.3 tagged EXE started with an isolated profile but logged
`app=unknown`; no public v2.5.3 release was created. v2.5.4 includes
`pyproject.toml` in the PyInstaller one-file bundle for the existing version
resolver. Exact-tag build and startup/version smoke are required before
publication.

## Limits

The guard remains lexical: quoted instructions, complex roles and pronouns,
or semantic paraphrase without shared high-risk verbs can evade it. Review
destructive commands before acting. Live multi-minute failed-tail recovery
has not been reproduced; that path is covered by deterministic mocks only.

## Verification

The isolated full test suite passed 294 tests with 3 skipped and 2 subtests
passed. Core/app imports and the pre-commit guard passed. The public release
will be published only after the exact-tag bundled EXE reports `2.5.4` during
an unlocked startup under a separate temporary profile.
