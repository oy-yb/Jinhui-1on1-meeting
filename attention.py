"""Spot-analysis module: after the user asks, Gemini analyzes the "worth attending to" behaviors/actions
in the frames and outputs structured annotations (time range + position + label) for the front-end
highlight animation on the player.

Public interface:
    analyze(question, frames) -> dict | None
      frames: [(t, jpeg_bytes), ...] (frames sampled by the assembly layer within the judged window)
      returns {"points": [{"start", "end", "label", "box", "desc"}, ...]}:
        - start / end: video time range covered by the spot (seconds, absolute timeline)
        - label: 2-8 char label (e.g. "therapist mouth", "boy's hand")
        - box: approximate position, normalized [x1,y1,x2,y2] (0~1, origin top-left)
        - desc: one sentence on why it is worth attending to
      Returns None on failure or when there are no frames (front-end skips the animation; the answer is unaffected).

Standard library + GEMINI_* / SAFETY_SETTINGS from config only.
"""

import base64
import json
import urllib.request
import urllib.error

from config import GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL, SAFETY_SETTINGS
from prompts import ATTENTION_PROMPT


def _generate(parts, extra_gen=None):
    url = (GEMINI_BASE_URL.rstrip("/") + f"/models/{GEMINI_MODEL}:generateContent"
           + f"?key={GEMINI_API_KEY}")
    body = {"contents": [{"role": "user", "parts": parts}],
            "safetySettings": SAFETY_SETTINGS}
    if extra_gen:
        body["generationConfig"] = extra_gen
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def analyze(question, frames):
    """Analyze the frame sequence and return spot annotations; any failure returns None (the main Q&A path is unaffected)."""
    if not frames or " " in GEMINI_API_KEY:
        return None
    parts = [{"text": f"{ATTENTION_PROMPT}\n\n[User question]\n{question}"}]
    for t, b in frames:
        parts.append({"text": f"[Frame @ {t:.1f}s]"})
        parts.append({"inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(b).decode(),
        }})
    try:
        data = _generate(parts, extra_gen={"responseMimeType": "application/json", "temperature": 0})
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        plan = json.loads(text)
        raw = plan.get("points") or []
    except Exception:
        return None

    t_lo = min(t for t, _ in frames)
    t_hi = max(t for t, _ in frames)
    points = []
    for p in raw[:4]:
        try:
            box = [float(x) for x in (p.get("box") or [0.25, 0.25, 0.75, 0.75])]
        except (TypeError, ValueError):
            box = [0.25, 0.25, 0.75, 0.75]
        start = max(t_lo, min(t_hi, float(p.get("start", t_lo))))
        end = max(start, min(t_hi, float(p.get("end", t_hi))))
        points.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "label": str(p.get("label", "spot"))[:12],
            "box": box,
            "desc": str(p.get("desc", ""))[:80],
        })
    print(f"[attention] {len(points)} spot(s) marked (window {t_lo:.1f}s-{t_hi:.1f}s)")
    return {"points": points}
