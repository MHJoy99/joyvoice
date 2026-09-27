# JoyVoice v2.5.3

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
- Binary build and artifact digests are recorded in the published release
  after building from the exact annotated tag.
- An unlocked offscreen startup smoke test is required on the exact-tag EXE;
  single-instance lock rejection alone is not a startup test.

## Limits

The guard remains lexical: quoted instructions, complex roles and pronouns,
or a semantic paraphrase without shared high-risk verbs can evade it. Review
destructive commands before acting. Live multi-minute failed-tail recovery
has not been reproduced; that path is covered by deterministic mocks only.
