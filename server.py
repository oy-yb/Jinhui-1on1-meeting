"""Web entry point: a local HTTP server with an embedded video + chat panel in the browser, for real interactive demos.

Usage: python3 server.py  -> opens http://localhost:8765 automatically

Reuses existing modules: knowledge (script index/search), agent (Gemini VLM), frame (frame sampling).
The "playback position" comes from the browser player reporting every second (web mode no longer depends on mpv).
"""

import json
import os
import re
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import (MATERIAL_FILES, TOTAL_DURATION, VIDEO_PATH, RAW_VIDEO_PATH,
                    FRAME_SOURCE, CTX_BEFORE, CTX_AFTER, SEARCH_TOP_K,
                    INCLUDE_FULL_SCRIPT, FULL_SCRIPT_MAX_CHARS,
                    FRAME_EVERY_SECONDS, MAX_FRAMES_PER_ASK, MAX_VIDEO_SECONDS)
import knowledge
import agent
import attention
import memory as memory_mod
import frame as frame_mod

PORT = 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

latest_position = 0.0   # latest playback position reported by the browser (seconds)
kb = None
frame_src = None
mem = memory_mod.Memory()   # chat memory: multi-turn coherence


def build_frame_source():
    if FRAME_SOURCE == "ffmpeg":
        return frame_mod.FfmpegFrameSource()
    if FRAME_SOURCE == "screen":
        return frame_mod.ScreenFrameSource()
    return None


def compose_context(question, t):
    """Assemble the text context fed to the model: chat history + current segment + script search + optional full text."""
    text = kb.get_context(t, before=CTX_BEFORE, after=CTX_AFTER)
    hits = kb.search(question, top_k=SEARCH_TOP_K)
    if hits:
        text += "\n\n[Script keyword search hits]\n" + "\n\n".join(hits)
    if INCLUDE_FULL_SCRIPT:
        text += "\n\n[Full script]\n" + kb.full_text(max_chars=FULL_SCRIPT_MAX_CHARS)
    mem_text = mem.context_text(max_turns=3, max_chars=2000)
    if mem_text:
        text = (f"[Chat history] (previous Q&A turns; refer to them when answering, "
                f"especially when the user uses references like 'that/she/just now/this') "
                f"\n{mem_text}\n\n" + text)
    return text


def current_snippet(t, n=120):
    """A short excerpt of the current segment, for the front-end status bar."""
    seg = kb.get_context(t, before=0, after=0)
    body = seg.split("\n", 1)[1] if "\n" in seg else seg
    body = body.strip()
    return body[:n] + ("…" if len(body) > n else "")


# Annotation triggering is now explicitly controlled by the front-end toggle (attention_enabled); no keyword fallback

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet mode

    # ---------- Helpers ----------
    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _q(self, name):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return q.get(name, [None])[0]

    # ---------- GET ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(BASE_DIR, "web", "index.html"), encoding="utf-8") as f:
                self._send(200, f.read())
        elif path == "/video":
            self._serve_video(VIDEO_PATH)
        elif path == "/raw-video":
            self._serve_video(RAW_VIDEO_PATH)
        elif path == "/now":
            t = float(self._q("t") or latest_position)
            self._send(200, json.dumps({
                "t": round(t, 2),
                "chapter": kb.chapter_at(t),
                "snippet": current_snippet(t),
            }, ensure_ascii=False), "application/json")
        elif path == "/status":
            self._send(200, json.dumps({
                "position": round(latest_position, 2),
                "entries": len(kb.entries),
                "model": agent.GEMINI_MODEL,
                "frame": frame_src.name if frame_src else "off",
                "window_max": MAX_VIDEO_SECONDS,
                "video": os.path.basename(VIDEO_PATH) if VIDEO_PATH else None,
                "memory_turns": mem.turns,
                "raw_video": (os.path.basename(RAW_VIDEO_PATH)
                              if RAW_VIDEO_PATH and os.path.exists(RAW_VIDEO_PATH) else None),
            }, ensure_ascii=False), "application/json")
        elif path == "/favicon.ico":
            self.send_response(204)  # no icon; avoid noisy 404s in the console
            self.end_headers()
        else:
            self._send(404, "not found")

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")

        if path == "/position":
            global latest_position
            latest_position = float(body.get("t", latest_position))
            self._send(200, json.dumps({"ok": True}), "application/json")

        elif path == "/ask":
            question = str(body.get("question", "")).strip()
            t = float(body.get("t", latest_position))
            att_on = bool(body.get("attention_enabled", False))   # annotation skill toggle: annotate only when on
            if not question:
                self._send(400, json.dumps({"ok": False, "error": "empty question"}), "application/json")
                return
            try:
                text = compose_context(question, t)
                # Assembly: judge window -> sample frames -> answer in parallel; annotate only when toggled on and frames exist
                seconds = agent.judge_video_seconds(question, text)["seconds"]
                frames = []
                if frame_src and seconds > 0:
                    frames = frame_src.get_window(
                        t, seconds, every=FRAME_EVERY_SECONDS, max_frames=MAX_FRAMES_PER_ASK) or []
                do_att = att_on and bool(frames)
                with ThreadPoolExecutor(max_workers=2) as ex:
                    f_answer = ex.submit(agent.ask_video, question, text, frames or None)
                    f_att = (ex.submit(attention.analyze, question, frames)
                             if do_att else None)
                    answer = f_answer.result()
                    att = f_att.result() if f_att else None
                print(f"[server] window {seconds}s · frames {len(frames)} · annotate toggle: {'on' if att_on else 'off'}"
                      f" · annotate run: {do_att} · memory {mem.turns} turns")
                mem.add(question, answer)   # record this round for later reference
                self._send(200, json.dumps({
                    "ok": True, "answer": answer, "t": round(t, 2),
                    "window": {"seconds": seconds, "frames": len(frames)},
                    "attention": att,
                }, ensure_ascii=False), "application/json")
            except agent.BlockedError as ex:
                # Content-safety block: no downgrade; hand control back to the user (front-end shows retry)
                print(f"[server] blocked: {ex}")
                self._send(200, json.dumps(
                    {"ok": False, "blocked": True, "error": str(ex)},
                    ensure_ascii=False), "application/json")
            except Exception as ex:
                self._send(500, json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False),
                           "application/json")

        elif path == "/analyze-region":
            # Manual region-tagging skill: the front-end sends the cropped region (PNG base64);
            # the assembly layer attaches the script context and hands it to agent.analyze_region (Gemini VLM single-image analysis)
            image_b64 = str(body.get("image", ""))
            question = str(body.get("question", "What is in this region? What can I attend to?")).strip()
            t = float(body.get("t", latest_position))
            if not image_b64:
                self._send(400, json.dumps({"ok": False, "error": "missing region image"}), "application/json")
                return
            try:
                text = compose_context(question, t)
                answer = agent.analyze_region(image_b64, text, question)
                mem.add(question, answer)   # also record into chat memory for later reference
                print(f"[server] region analysis t={t:.1f}s · memory {mem.turns} turns")
                self._send(200, json.dumps({"ok": True, "answer": answer}, ensure_ascii=False),
                           "application/json")
            except agent.BlockedError as ex:
                print(f"[server] region analysis blocked: {ex}")
                self._send(200, json.dumps({"ok": False, "blocked": True, "error": str(ex)},
                                           ensure_ascii=False), "application/json")
            except Exception as ex:
                self._send(500, json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False),
                           "application/json")
        else:
            self._send(404, "not found")

    # ---------- Video streaming (with Range seek support) ----------
    def _serve_video(self, vpath):
        if not vpath or not os.path.exists(vpath):
            self._send(404, "video file not found; set VIDEO_PATH / RAW_VIDEO_PATH in config.py")
            return
        size = os.path.getsize(vpath)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng) if rng else None
        if m:
            start = int(m.group(1)) if m.group(1) else 0
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
        with open(vpath, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        self.wfile.write(data)


def main():
    global kb, frame_src
    kb = knowledge.KnowledgeBase(MATERIAL_FILES, total_duration=TOTAL_DURATION)
    frame_src = build_frame_source()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"loaded {len(kb.entries)} script segments; frame source: {frame_src.name if frame_src else 'off'}")
    print(f"demo at: http://localhost:{PORT}  (Ctrl+C to stop)")
    webbrowser.open(f"http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
