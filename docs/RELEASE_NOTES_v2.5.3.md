# JoyVoice v2.5.3

This tag was not published as a GitHub Release: the tagged EXE started under an
isolated profile, but its packaged app version displayed as `unknown`. The
version-bundling correction is included in v2.5.4.

## Safety Patch

The v2.5.2 lexical guard could accept a bare destructive command when its
negation appeared more than six words before the verb. For example,
`Shut down production` passed against `Do not please now today here there
then also just shut down production`. A public advisory is attached to
v2.5.2. This patch treats any negation in a lexical clause as applying to
high-risk verbs in that clause; explicit `but` clauses remain separate.
Ambiguous mixed-action clauses may require manual review rather than pass.

## Production Coverage

New mocked, no-network tests run the actual `CloudASRWorker.run()` chunked
path. They verify translation-only prefix salvage, failed-tail-only Google
recovery, and malformed `None` chunk handling without falsely reporting a
complete result. The standalone `transcribe_chunks_resilient` helper has no
production caller; its tests do not substitute for live-worker coverage.

The live worker sends nonzero PCM (including near-zero amplitudes); only
all-zero PCM is skipped before a native request. The standalone helper uses
a different near-zero silence threshold. This distinction corrects an
overbroad earlier claim of production parity.

## Verification

- Isolated full suite: 294 passed, 3 skipped, with a dummy API key, temporary
  app profile, Qt offscreen, and sanitized evidence fixture.
- Exact-tag EXE: 202058138 bytes, SHA-256
  `b674d13d9f3bd95a802baadc89a4719b308a63f90f1ecd37ead6aa7fb9bf5113`.
- An unlocked offscreen startup reached tray initialization under an isolated
  profile, but logged `app=unknown`; this fails version verification.

## Limits

The guard remains lexical: quoted instructions, complex roles and pronouns,
or a semantic paraphrase without shared high-risk verbs can evade it. Review
destructive commands before acting. Live multi-minute failed-tail recovery
has not been reproduced; that path is covered by deterministic mocks only.
