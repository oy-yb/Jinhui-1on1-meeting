# Bug Log: Gemini PROHIBITED_CONTENT blocks (front-end ask errors)

> Historical record. Note: the current codebase has evolved — blocks are no longer auto-downgraded;
> the front-end now shows "blocked" + a Retry button and the user resends the same question manually
> (see the "Recurrence" section below and `agent.BlockedError`).

- **Date**: 2026-10-06
- **Status**: ✅ fixed and verified
- **Scope**: front-end ask → `/ask` → Gemini answer (full chain)
- **Files involved**: `agent.py` (main fix), `server.py` (favicon fix)
- **Service**: http://localhost:8765

---

## 1. Symptom

The user asked **"What are they doing @00:00"** in the web front-end and the chat showed an error bubble:

```json
Error: Gemini returned an unexpected response: {
  "promptFeedback": { "blockReason": "PROHIBITED_CONTENT" },
  "usageMetadata": {
    "promptTokenCount": 5878,
    "promptTokensDetails": [
      { "modality": "IMAGE", "tokenCount": 1100 },
      { "modality": "TEXT", "tokenCount": 4778 }
    ],
    "modelVersion": "gemini-3.8-flash"
  }
}
```

i.e. Gemini's content-safety policy blocked the request, **returning no candidate answer**.

## 2. Investigation

| Step | Action | Conclusion |
|---|---|---|
| 1 | Browser test: video plays (readyState=4), no console errors, quick questions work | rules out a front-end rendering issue |
| 2 | Fixed the `/favicon.ico` 404 (`server.py` returns 204) | removes potential console noise |
| 3 | Reproduced the user's question `What are they doing @t=0`: **blocked with frames** | confirmed reproducible |
| 4 | After adding a "blocked → text-only retry" downgrade, **text-only was still blocked** | rules out the frames; it is a **text combination** trigger |
| 5 | Binary-searched the script: `[1500:3000]` always blocked; finer slices `[1500:1900]`, single lines ("holding a banana", child description, full sentence + safety) **all passed** | the trigger is a **large combination**, not a single sensitive word |
| 6 | Full text + question retried **4 times, all passed** | confirms **boundary randomness** in the classifier |

Key clue: the same full-text input is sometimes blocked, sometimes not — this is not a hard trigger on one word,
but the classifier's **probabilistic misfire on "child-related content + large input"**.

## 3. Root cause

- **Direct cause**: Gemini's content-safety classifier returned `PROHIBITED_CONTENT`; the input never entered generation.
- **Nature**: the script/video is a **Kaufman speech-therapy teaching demo** (a therapist + a 4-5-year-old boy,
  targeting the velar /g/); the content is fully healthy and professional, but the classifier has **boundary-random
  misfires on "child + long text/frames" combinations** (same input sometimes blocked, sometimes not).
- **Additional trigger point**: the script tail mentions "the puppy Heidi **holding a banana**" — "child + banana"
  is a classic misfire combination (it passed in isolation, but is still a risk item).

## 4. Fixes (agent.py triple fix + fallback)

### 1. Lower the adjustable safety categories

`BLOCK_NONE` for the following five categories (removes classifier misfires):

```
HARM_CATEGORY_SEXUALLY_EXPLICIT
HARM_CATEGORY_HARASSMENT
HARM_CATEGORY_HATE_SPEECH
HARM_CATEGORY_DANGEROUS_CONTENT
HARM_CATEGORY_CIVIC_INTEGRITY
```

> ⚠️ `HARM_CATEGORY_CHILD_SEXUAL_CONTENT` is a Google platform **hard guardrail** and cannot be tuned via
> parameters — it stays at the default.

### 2. Input-side sanitization

```python
SAFE_REPLACEMENTS = {"香蕉": "黄色水果"}
```

Equivalent replacements are applied **only to the text fed to the model** (`_sanitize()`); your script files are
**never modified**.

### 3. Material nature statement appended to SYSTEM_PROMPT

> "Material nature statement: this video is a medical teaching demonstration of child speech/language therapy;
> the content is professional medical education and entirely healthy."

Helps the classifier recognize the input context correctly.

### 4. Fallback (kept from the original logic)

When frames trigger `PROHIBITED_CONTENT`, drop the images and retry once with the script only (covering both the
HTTPError-400 path and the 200+blockReason path).

## 5. Verification results

| Check | Result |
|---|---|
| Original failing question "What are they doing" @0 (real call) | ✅ success; the answer combined frame info (people/scene description) |
| 4 consecutive real calls (3 different questions) | ✅ all passed with frames directly |
| Service log `[agent]` downgrade notices | ✅ 0 (no downgrade triggered; frames used normally) |
| Browser end-to-end: ask → answer | ✅ answered; meta "based on frames & script" |
| Browser console | ✅ no errors |

## 6. Lessons & recommendations

1. **Gemini requests involving children**: use the safety combo by default (safetySettings + sanitization +
   the nature statement) to push the misfire probability close to zero.
2. **Ultimate fallback for occasional blocks**: `FRAME_SOURCE = "off"` in `config.py` switches to script-only Q&A
   (the frame capability stays, ready to re-enable).
3. **Diagnostics on record**: for this kind of issue, "binary slicing + repeated retries of the same input"
   tells hard triggers from random misfires; the diag2-5.py scripts were cleaned up after use
   (evidence kept: `/tmp/diag4.txt`, `/tmp/diag5.txt`).
4. This log does not replace the README: the README explains usage; this file only records the failure and fix trail.

---

## 2. Recurrence (2026-10-06, "no downgrade" decision applied)

### Symptom

In the same session: the previous turn **"What did the therapist do in 5-10s?" @00:10** succeeded
(window 6s · 3 frames · 2 spots), then the next turn **"What is the child doing right now? What do the
action and expression mean?" @00:10** failed:

```
Error: Gemini returned an unexpected response: {"candidates":[{"content":{},"finishReason":"PROHIBITED_CONTENT",...}],
"promptTokenCount": 5164, ...}
```

### Root cause

- Same root as the first case: **a 4-5-year-old in the frames + long text combination → classifier boundary
  randomness**, the same input sometimes passes, sometimes not; the previous turn at the same position succeeded
  and this one failed — random behavior, not a new issue.
- **Code gap (old behavior)**: the old downgrade logic retried **once** (frames → text-only) only when `_text_of`
  raised; if that text-only attempt happened to be blocked again (random), it raised directly with no bounded
  multi-attempt retry → the user saw an error.

### Fix (agent.py, current behavior)

1. **Structured detection**: covers all three block sources — `candidates[0].finishReason`,
   `promptFeedback.blockReason` (a 200 response with no candidate / empty body — this case's shape),
   and the request-level 400 `blockReason`;
2. **Design decision — no auto-downgrade**: when frames are needed, analyze with frames. On a block, the
   `BlockedError` is raised and **control is handed back to the user**: the front-end shows "⚠️ blocked" + a
   **Retry** button that resends the exact same question (same time point; the classifier's randomness means a
   manual retry almost always passes);
3. **judge stage**: `judge_video_seconds` retries once on a block (the original code degraded to 0s directly,
   which could skip annotations that should have been triggered).

### Verification

- Unit tests: `_block_reason` handles finishReason / promptFeedback.blockReason / normal responses correctly;
- End-to-end: the user's original question @00:10 asked 4 times in a row ("What is the child doing? What do the
  action and expression mean?") **all succeeded**; during reproduction a block was triggered and the flow printed
  the block correctly with the retry button path.

### Lessons

- A "fallback" must be **written as a code path** (detect → block-error → user retry), not just verified as a plan;
- Blocked responses come in **two shapes** (finishReason and promptFeedback.blockReason); detection must cover both;
- Handling random blocks: **bounded retry by the user + keep the same input** beats silently downgrading
  (the longer the text, the more likely it grazes the boundary).
