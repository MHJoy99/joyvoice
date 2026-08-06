#!/usr/bin/env bash
set -euo pipefail

key="$(/root/.bdx-ai/get-api-key)"

echo '-- non-stream gpt-5.6-sol-low --'
curl -fsS --max-time 120 https://gpt.bdx.market/v1/chat/completions \
  -H "Authorization: Bearer ${key}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-sol-low","messages":[{"role":"user","content":"Reply with one short sentence."}],"stream":false}' \
| python3 -c '
import sys, json
d = json.load(sys.stdin)
m = d["choices"][0]["message"]
c = m.get("content") or ""
r = m.get("reasoning_content") or ""
low = c.lower()
print("content_has_think_tag=" + str(("<think" in low) or ("</think" in low)))
print("content_preview=" + repr(c[:300]))
print("reasoning_preview=" + repr(r[:300]))
'

echo '-- stream gpt-5.6-sol-low --'
curl -fsS --max-time 120 https://gpt.bdx.market/v1/chat/completions \
  -H "Authorization: Bearer ${key}" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-sol-low","messages":[{"role":"user","content":"Reply with one short sentence."}],"stream":true,"stream_options":{"include_usage":true}}' \
| python3 -c '
import sys, json
content = []
reasoning = []
for line in sys.stdin:
    line = line.strip()
    if not line.startswith("data:"):
        continue
    payload = line[5:].strip()
    if payload == "[DONE]":
        break
    try:
        chunk = json.loads(payload)
    except Exception:
        continue
    choices = chunk.get("choices") or []
    if not choices:
        continue
    delta = choices[0].get("delta") or {}
    if isinstance(delta.get("content"), str):
        content.append(delta["content"])
    if isinstance(delta.get("reasoning_content"), str):
        reasoning.append(delta["reasoning_content"])
c = "".join(content)
r = "".join(reasoning)
low = c.lower()
print("stream_content_has_think_tag=" + str(("<think" in low) or ("</think" in low)))
print("stream_content_preview=" + repr(c[:300]))
print("stream_reasoning_preview=" + repr(r[:300]))
'
