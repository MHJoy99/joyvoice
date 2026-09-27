# Sol Plan: Conversation-Aware Prompt for AI

## The Outcome

JoyVoice remains a dictation app. When the existing **Prompt for AI** style is
selected, it uses what the user said earlier in the active conversation to
write a better command and paste it into the focused agent. That agent, not
JoyVoice, investigates the live project and performs the work.

The command must preserve the new request, carry forward relevant *stated*
constraints, and distinguish old statements from verified current facts. When
the target is unclear, it tells the receiving agent to ask, not to guess.

Normal dictation, translation, paste, and all other styles must keep their
existing behavior and latency. This is an upgrade to an existing style, not a
new agent runtime or a replacement for the voice pipeline.

## What "Aware" Means in Version 1

- JoyVoice can remember prior speech made in **Prompt for AI** and notes the
  user explicitly adds to that conversation. It can use a large supported
  model context when useful.
- It cannot know what the receiving agent actually did, read an entire PC, or
  know the current VPS state merely because it remembers a conversation.
  Work results enter memory only if the user supplies them. Importing agent
  outcomes, files, or remote sources is a later, separate integration.
- Earlier speech is evidence of what the user *said*, not proof that a server,
  file, or deployment still has that state. Generated prompts are never
  evidence that a task was performed or approved.

For example, after the user says "Do not move the live tag until health
passes," a later "prepare the deployment" can carry that rule forward. It
must not manufacture a candidate tag or claim that health has already passed.

## User Experience

1. The user selects the existing **Prompt for AI** style and enables
   **Remember this conversation** once. Existing users remain stateless until
   they opt in. Other text styles remain unchanged.
2. On each Prompt-for-AI dictation, JoyVoice captures the recognized request,
   loads the *selected* conversation, composes a grounded command, and pastes
   automatically as it does today. No mandatory review dialog interrupts
   every dictation.
3. A **Prompt memory** view in the tray exposes conversation selection,
   **New**, **Compress now**, **Remove selected**, **Clear**, and an optional
   **Review before paste** switch. The view shows the exact user statements
   and derived summary that the next compilation would use. Switching
   windows never silently switches or clears a conversation.
4. **New** creates an empty conversation while keeping old ones selectable.
   **Compress now** rewrites the working summary but retains source turns.
   **Remove selected** excludes and deletes those turns, then invalidates any
   summary based on them. **Clear** deletes the active conversation's turns
   and summaries after confirmation; it is not secretly an archive action.
   App-visible deletion does not guarantee physical erasure from backups or
   SSD storage.
5. A **Use memory for this request** control permits a one-off stateless
   Prompt-for-AI dictation without clearing the saved conversation. If memory
   is unavailable or too large, JoyVoice visibly says it pasted without
   memory; it never implies that lost context was consulted.

## Data And Privacy Contract

Use a separate SQLite file under `app.storage.paths.data_dir()` (normal
`%APPDATA%/JoyVoice`, or the portable data directory). SQLite is in Python's
standard library and gives atomic updates without rewriting a growing JSON
array. It does not change `history.json` or the existing settings file.

- `conversations`: id, title, creation/update times.
- `turns`: id, conversation id, time, recognized user text, source type
  (`spoken` or `user_note`), and optional original transcript. Store only
  Prompt-for-AI input after a successful paste/copy for an active job;
  cancelled or stale jobs do not enter memory. Never store the generated
  prompt as a user turn. Use a per-job idempotency key so a retry cannot
  duplicate a turn.
- `summaries`: derived text, time, exact source turn IDs, and version. A
  summary is a cache over original turns, never a source of authority. Editing
  or removing a source invalidates summaries that refer to it.
- `active_conversation_id` and the memory-enabled switch are persisted, with
  a safe empty-conversation default on first use. Raw turns are retained until
  the user removes or clears them; compression does not discard originals.
- Do not silently collect normal dictation, clipboard contents, focused-window
  titles, or downstream agent replies. Clearly disclose that text from the
  active conversation is sent to the configured cloud text model for
  Prompt-for-AI requests. Local SQLite data is not encrypted by this feature;
  OS account permissions still matter. A dictated secret may reach the model,
  and a regex secret filter cannot guarantee its removal. Keep a stateless
  option and explicit delete/export controls.
- Do not log speech, context, assembled prompts, or the first characters of
  generated output. The existing `_single_llm_call()` log of `output[:80]`
  must be removed/redacted for this mode. Telemetry can retain counts,
  timings, model, and whether memory was used, but no content.

Treat imported or pasted material as *data*, not instructions to JoyVoice.
An explicit user note can state a preference; a quoted web page cannot.

## How One Request Is Compiled

```text
F8 speech -> existing ASR/translation -> existing style selection
                                  |
                       Prompt for AI only
                                  v
                 read active conversation in worker
                                  v
                select recent turns + relevant older
                  statements + derived summary
                                  v
                   one grounded text-model call
                                  v
              validate provenance / missing details
                                  v
                existing history -> clipboard paste
```

The current request is always included in full, clearly marked as the
current user's words. Prior turns are tagged with ID and date. The model may
organize the task, but it may add a requirement, path, flag, version, number,
or purported outcome only when the current request or a cited earlier user
turn supports it. If a current-state check is needed, the command instructs
the receiving agent to perform that check. If the target is missing, it asks
the receiving agent to clarify *before acting*.

Ask the model for structured output: composed prompt, used turn IDs, and
unresolved details. Validate that IDs exist and that quoted identifiers and
numbers come from those turns or the current request. The app renders the
pasteable text; it does not paste model-internal provenance JSON. This cannot
prove the absence of every semantic hallucination, so also test representative
ambiguous requests and keep a conservative fallback to the original request
plus an instruction to verify current state.

The result should be useful, not artificially long. Do not turn an eight-word
request into a 400-word essay when two verified constraints suffice.

## Context Window And Compression

Do not assume that the gateway's selected text model accepts one million
tokens. A model listing alone may not establish the effective gateway limit.
Confirm the configured model and usable input/output budget with its actual
provider/gateway and representative test requests. Reserve room for the
system instruction, full current request, and output. Report actual usage
and latency. Make the working input budget configurable.

Within that budget, use the most recent raw turns plus relevant older turns
and a compact summary of older material. When raw history exceeds the budget,
use a local text search over older turns to find candidates, but keep their
original dates and source IDs; do not treat search ranking as truth. A large
verified window can include more raw context; there is **no arbitrary
4,000-character cutoff**. When the assembled request approaches the
configured budget or latency ceiling, compress older turns in a separate
background operation:

- Preserve exact names, numbers, quoted constraints, corrections,
  unresolved questions, dates, and source turn IDs.
- Mark older project-state assertions as historical, not live facts.
- Retain the raw source turns locally. Manual **Compress now** uses the same
  process; manual removal invalidates the affected summary.
- If compression or budget checks fail, never silently truncate the latest
  request. Fall back to a visibly stateless prompt or ask the user to shorten
  supplied context.

The current `cloud_llm_rewrite()` splits Prompt-for-AI *text* at 4,000
characters and joins independent rewrites. A conversation-aware compilation
must be one coherent call after budgeting; it must not duplicate context for
every chunk or stitch together inconsistent partial prompts. Leave chunking
for the other existing styles unchanged.

## Isolation From Normal Dictation

The existing branch in `app/main.py` around `_on_asr_done()` and
`_run_llm()` is the boundary. Load conversation data and assemble context
**only** for `style == "prompt_for_ai"` when memory is enabled. Never put a
memory read in audio capture, transcription, `_style_text()`, generic
`_finish_paste()`, or the `raw`/`clean_english` paths. Keep the audio model's
three-field JSON contract unchanged.

All database work, compression, and text-model calls must run off the Qt UI
thread. Snapshot the conversation ID and memory setting when recording starts,
not after ASR returns; changing conversations while speech is being processed
cannot redirect that job into a different conversation. Honor the existing
job-ID cancellation/stale-result guards before displaying or pasting. Preserve
history-before-paste and the current clipboard recovery behavior. On a memory
failure, notify the user, keep their spoken request, and use the current
stateless Prompt-for-AI path; on a text-model failure, preserve the existing
pre-rewrite salvage behavior.

## Implementation Order

1. **Baseline:** record current `raw`, `clean_english`, and stateless
   `prompt_for_ai` request payloads, paste behavior, and latency using a fake
   gateway/clipboard. Add focused regression tests before changing the path.
2. **Store and controls:** add a `prompt_memory_db_path()` in
   `app/storage/paths.py`, a conversation store module, and a single Prompt
   memory management view. Prove New, select, remove, clear, persistence,
   and corruption recovery without wiring the model yet.
3. **Compile:** pass an immutable context snapshot through
   `_run_llm -> CloudLLMWorker -> cloud_llm_rewrite -> _single_llm_call`
   only for Prompt-for-AI. Budget a single call; validate its structured
   response; paste the rendered command. Keep legacy stateless mode available
   behind the memory switch.
4. **Compress and observe:** add manual/background compression, source-ID
   invalidation, optional review-before-paste, and content-free usage/latency
   telemetry. Test against the actual configured gateway without sending
   private archives as test data.
5. **Roll out:** memory defaults off for upgrades, then the user enables it
   once. Compare real dictations with and without memory. Do not connect
   files, Kilo sessions, or VPS logs until an explicit later design says what
   data is permitted and how freshness is checked.

## Acceptance Gates

| Situation | Required behavior |
| --- | --- |
| `raw` or `clean_english`, memory file full or corrupt | Same payload and paste path as baseline; no memory I/O or extra network request |
| Prompt-for-AI memory switch off | Same rewrite request shape and salvage behavior as baseline |
| Two separate conversations | Only active conversation's turns appear; switching is deliberate and survives restart |
| Prior statement corrected later | Latest correction is used; old claim is not presented as current |
| "Deploy it" with no identified target | Prompt tells receiving agent to ask which target; no invented project, host, tag, or health result |
| Old remembered deploy rule | Prompt attributes it to the user's earlier statement and directs agent to check live state |
| Compress / remove / clear | Raw turns survive compress, removed turn cannot return via summary, Clear empties active memory |
| Gateway timeout, corrupt DB, cancel, or stale job | No crash, no duplicate turns, no stale paste; user sees when context was unavailable |
| Large verified input budget | Current request remains intact; no silent context truncation or chunk-join |
| Privacy check | No secrets or prompt text in logs/telemetry; stateless use and deletion controls work |

Use unit tests with a temporary data directory and mocked network, an
offscreen Qt smoke test for controls, and a small set of real voice cases.
Compare normal-mode request payloads byte-for-byte and latency distributions,
not a brittle single-run 50 ms promise. Run the repository import check and
`git diff --check`; do not require live API calls for routine tests.

## Concrete Examples

**Useful carryover.** In conversation "JoyVoice", the user says: "Keep
normal F8 dictation unchanged; only improve Prompt for AI." Tomorrow they
say: "Write the command for conversation memory." JoyVoice pastes:

> Add continuing conversation memory to JoyVoice's existing Prompt-for-AI
> style. I previously specified that ordinary F8 dictation must remain
> unchanged. Inspect the current implementation before editing, preserve that
> constraint, and verify the ordinary path after the change. Do not claim an
> existing memory implementation or a verified model context limit without
> checking it.

It does not assert that code has already changed or claim that the agent
finished.

**Honest uncertainty.** In a new conversation the user says: "Deploy the
latest version." JoyVoice has no deployment target or current build state.
It pastes:

> I want to deploy the latest version. Before acting, ask me which project and
> destination I mean. Then inspect the actual candidate and current deployment
> state. Do not assume a VPS, image tag, or successful health check.

**Changed rule.** The user previously said "use flag X," then later said
"replace X with Y." A future prompt can state that the user corrected X to Y
on that date and instruct the working agent to check the current runbook. It
must not present both as simultaneous instructions or claim Y is already live.

## Decision

Build a durable, user-controlled conversation for the *existing* prompt
style. Use a large context when the configured model actually supports it,
but retain raw history and compact only as needed. Keep normal dictation
outside this feature and never mistake remembered speech for verified world
state. This delivers the requested improvement without building a second
task-executing agent inside JoyVoice.
