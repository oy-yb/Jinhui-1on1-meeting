"""Q&A layer (Gemini VLM): sends "script/transcript context + video frames + user question" to Gemini and returns the answer.

Public interface:
    ask(question, text_context, frame_bytes=None) -> str
        legacy single-frame interface: frame_bytes is one JPEG at the current time; None = text-only Q&A.
    ask_video(question, text_context, frames=None) -> str
        multi-frame interface: frames is [(t, jpeg_bytes), ...] (sampled by the assembly layer after judging the window).
    judge_video_seconds(question, text_context) -> float
        stage one: lightweight call; Gemini decides "how many seconds of video are needed to answer" (0 = script suffices).

Assembly layer (server.py) order: judge_video_seconds -> sample frames by window -> ask_video.
To switch models, only edit the four GEMINI_* settings in config.py. Python standard library only.
"""

import base64
import json
import re
import urllib.request
import urllib.error

from config import (GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL,
                    MAX_VIDEO_SECONDS, DEFAULT_VIDEO_SECONDS,
                    SAFETY_SETTINGS, SAFE_REPLACEMENTS)
from prompts import SYSTEM_PROMPT, JUDGE_PROMPT, REGION_PROMPT

# Input sanitization: equivalent replacements applied only to the text fed to the model; your script files are untouched.
def _sanitize(text):
    for k, v in SAFE_REPLACEMENTS.items():
        text = text.replace(k, v)
    return text


def _generate(parts, extra_gen=None):
    """Send one generateContent request and return the full response dict; network errors raise RuntimeError."""
    url = (GEMINI_BASE_URL.rstrip("/") + f"/models/{GEMINI_MODEL}:generateContent"
           + f"?key={GEMINI_API_KEY}")
    body = {"contents": [{"role": "user", "parts": parts}],
            "safetySettings": SAFETY_SETTINGS}
    if extra_gen:
        body["generationConfig"] = extra_gen
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini request failed ({e.code}): "
                           + e.read()[:500].decode("utf-8", "ignore"))


def _text_of(data):
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Gemini returned an unexpected response: {json.dumps(data, ensure_ascii=False)[:500]}")


def _finish_reason(data):
    """Return the candidate's finishReason from the response; None if absent."""
    try:
        return data["candidates"][0].get("finishReason")
    except (KeyError, IndexError, TypeError):
        return None


# These finishReason values are all treated as content-safety blocks
_BLOCKED_REASONS = ("PROHIBITED_CONTENT", "SAFETY", "IMAGE_SAFETY",
                    "BLOCKLIST", "RECITATION")


class BlockedError(RuntimeError):
    """Gemini content-safety block (PROHIBITED_CONTENT etc.).

    Design decision: no automatic downgrade (no dropping frames, no rewording the question). When frames are needed, analyze with frames;
    when blocked, hand control back to the user - the front-end shows "blocked, retry" and the user resends manually (same input; the classifier is random enough that it usually passes).
    """


def _block_reason(data):
    """Structurally detect whether the response was blocked by content safety; None if not blocked.

    Covers both shapes: candidates[0].finishReason, and promptFeedback.blockReason
    (a 200 response with no candidate / empty body).
    """
    fr = _finish_reason(data)
    if fr in _BLOCKED_REASONS:
        return f"finishReason={fr}"
    try:
        br = data["promptFeedback"].get("blockReason")
        if br:
            return f"promptFeedback.blockReason={br}"
    except (KeyError, TypeError):
        pass
    return None


def _build_parts(question, text_context, frames):
    """Build the "text + multi-frame" request parts. frames: [(t, jpeg_bytes), ...] or None."""
    parts = [{"text": f"{SYSTEM_PROMPT}\n\n[Script/transcript context]\n{_sanitize(text_context)}\n\n[User question]\n{question}"}]
    for t, b in (frames or []):
        label = "current frame" if t is None else f"Frame @ {t:.1f}s"
        parts.append({"text": f"【{label}】"})
        parts.append({"inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(b).decode(),
        }})
    return parts


def _request(question, text_context, frames):
    """Send one request with frames (or text-only); raise BlockedError when blocked by content safety (no downgrade).

    Block detection covers three sources: finishReason, promptFeedback.blockReason, request-level 400.
    """
    parts = _build_parts(question, text_context, frames)
    why = None
    data = None
    try:
        data = _generate(parts)
    except RuntimeError as ex:
        # Request-level 400 block (blockReason inside the error body)
        if "PROHIBITED_CONTENT" in str(ex) or "blockReason" in str(ex):
            why = str(ex)[:120]
        else:
            raise
    if data is not None:
        why = _block_reason(data)          # Structured detection (finishReason / blockReason)
        if why is None:
            try:
                return _text_of(data)
            except RuntimeError as ex:     # Response error with no body text: treat as a block
                if "PROHIBITED_CONTENT" in str(ex) or "blockReason" in str(ex):
                    why = str(ex)[:120]
                else:
                    raise
    if why is not None:
        raise BlockedError(
            "Request blocked by Gemini's content-safety policy (" + why + "). "
            "This is Google's classifier randomly flagging the 'child in frame + long text' combination at the boundary; "
            "it is unrelated to the content - click 'Retry' below to ask again.")
    raise RuntimeError("Gemini returned an unexpected response: no usable reply")


def judge_video_seconds(question, text_context):
    """Stage one: ask Gemini to output "how many seconds of video to watch".

    Returns {"seconds": N}; on failure degrades to DEFAULT_VIDEO_SECONDS
    (still analyzes with frames - no downgrade to text-only). Annotations are no longer decided by the model:
    whether to annotate is explicitly controlled by the front-end toggle (attention_enabled).
    """
    if " " in GEMINI_API_KEY:
        return {"seconds": DEFAULT_VIDEO_SECONDS}
    parts = [{"text": f"{JUDGE_PROMPT}\n\n[Script/transcript context (excerpt)]\n{_sanitize(text_context[:2000])}\n\n[User question]\n{question}"}]
    n = DEFAULT_VIDEO_SECONDS
    for attempt in range(2):   # Retry once on a safety block (random); fall back to the default window on failure
        try:
            data = _generate(parts, extra_gen={"responseMimeType": "application/json", "temperature": 0})
            text = _text_of(data)
            try:
                obj = json.loads(text)
            except (ValueError, TypeError):
                m = re.search(r"\d+(\.\d+)?", text)
                obj = {"video_seconds": float(m.group(0)) if m else 0.0}
            try:
                n = float(obj.get("video_seconds", 0))
            except (TypeError, ValueError):
                n = 0.0
            break
        except Exception:
            n = DEFAULT_VIDEO_SECONDS
    n = max(0.0, min(n, MAX_VIDEO_SECONDS))
    return {"seconds": n}


def ask(question, text_context, frame_bytes=None):
    """Legacy interface (single frame): frame_bytes is one JPEG at the current time; None = text-only Q&A."""
    if " " in GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY in config.py is still a placeholder; paste your Google AI Studio API Key")
    frames = [(None, frame_bytes)] if frame_bytes else None
    return _request(question, text_context, frames)


def analyze_region(image_b64, text_context, question):
    """Manual region analysis: the user drew a box around part of the frame; the region's PNG base64 is sent
    to the Gemini VLM along with the script context, answering "what is this, what can I attend to".
    Raises BlockedError on content-safety blocks (same as ask_video - no downgrade).
    """
    if " " in GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY in config.py is still a placeholder; paste your Google AI Studio API Key")
    parts = [{"text": REGION_PROMPT.format(context=_sanitize(text_context), question=question)},
             {"inline_data": {"mime_type": "image/png", "data": image_b64}}]
    data = _generate(parts)
    why = _block_reason(data)
    if why is not None:
        raise BlockedError(
            "Request blocked by Gemini's content-safety policy (" + why + "). "
            "Click 'Retry' to analyze again.")
    try:
        return _text_of(data)
    except RuntimeError as ex:
        if "PROHIBITED_CONTENT" in str(ex) or "blockReason" in str(ex):
            raise BlockedError("Request blocked by Gemini's content-safety policy. Click 'Retry' to analyze again.")
        raise


def ask_video(question, text_context, frames=None):
    """Multi-frame interface: frames is [(t, jpeg_bytes), ...] sampled by the assembly layer, answered together with the text in time order.

    When frames is empty/None, it degrades to script-only Q&A. The window is decided by the assembly layer via
    judge_video_seconds first; this layer does not re-judge.
    """
    if " " in GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY in config.py is still a placeholder; paste your Google AI Studio API Key")
    return _request(question, text_context, frames)
