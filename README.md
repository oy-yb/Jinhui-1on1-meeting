# Video Companion — Real-time "Watch Together" Q&A Agent (Gemini VLM)

You own the **original video + a detailed storyboard script + transcript text**. When you **wake the agent with a question**, it does two things:

1. **Watch the frames**: it first decides "how many seconds of video are needed to answer", then samples multiple frames inside that window (the VLM looks at them directly)
2. **Query the script**: it takes the current paragraph from the script/transcript and keyword-searches the whole script for related paragraphs

Then **Gemini (a native multimodal VLM)** answers by combining the frames and the text — this is **Multimodal RAG**. Compute happens only at the moment you wake the agent; otherwise it costs nothing.

## How it works (web demo: server.py)

```
browser plays the video ──position report──▶ server.py
                                               │
you ask (right chat panel) ────────────────────┤
                                               ▼
        agent.py stage one: Gemini decides "how many seconds of video to watch"
                                               │
          ├─ 0s  → script-only answer (dialogue/pronunciation questions)
          └─ N s → frame.py samples multiple frames in the window (t ± N/2)
                                               ▼
          run in parallel: agent.py answers + (if enabled) attention.py annotates
                                               ▼
          the answer appears in the chat; the front-end jumps back to the question
          time and plays; highlight animations persist while inside each spot range
```

The status bar **live-shows the current playback time + current chapter + frame-window config** — that is the proof the agent "is watching".

## Quick start

```bash
cd video-companion
# 0. System dependency (once)
brew install ffmpeg
# 1. Start a player (mpv first, so the socket exists)
mpv --input-ipc-server=/tmp/mpv-socket your-video.mp4
# 2. Configure config.py:
#    - MATERIAL_FILES: your script/transcript
#    - VIDEO_PATH: original video path (used for frame sampling)
#    - GEMINI_API_KEY: Google AI Studio key (put the real one in config_local.py, git-ignored)
# 3. Run
python3 server.py        # web demo at http://localhost:8765
python3 main.py          # or the legacy tkinter overlay
```

You can try it without a player: set `PLAYER = "manual"` in `config.py` (position fixed to `MANUAL_POSITION`).
No frames? Set `FRAME_SOURCE = "off"` to fall back to text-only Q&A.

## Spot animations: the annotation skill toggle (annotate only when on)

Annotation is a **skill the user explicitly toggles**: with the **"🎯 Annotate"** switch in the chat bar on,
answers come with **expression/action** spot animations on the frame (analyzed in parallel, no extra wait);
**when off, answers are pure analysis, no annotation at all**. The switch state is stored in `localStorage` and survives a refresh.

Spots **accumulate and persist** (new chats never overwrite old ones) and are **auto-deduplicated**: a new spot whose
time range overlaps a saved one AND whose label is identical/similar is not saved again — instead the front-end shows
clickable guide chips ("These spots were already marked before - click to jump to them") that jump straight to the
existing spot. The animated box appears automatically when playback enters the spot's range and **keeps showing for 3
more seconds** after the range ends (`SPOT_EXTRA_SECONDS`, editable) so you can keep watching; multiple overlapping spots
are offset so they never fully overlap.
**Right-click a spot box** opens a menu: 🗑 Delete Spot / 🙈 Hide Spot / ✏️ Edit Spot.
"Edit Spot" opens a panel where you can change the caption title, caption content and the time range;
inside it you can also **ask the AI** (type your own prompt, e.g. "What mental pattern does this action suggest?"),
and the agent answers by combining the cropped region + script — the result can be filled into the caption in one click.
"Hide" only stops that spot from rendering (data is kept); it shows greyed out with "(hidden)" in the **📌 Spots (N)**
list in the top-left, and clicking the item restores it and jumps to it.
Above the video there is also the **👁 Hide Spots** global switch: one click hides/shows **all** spot animations
(state survives a refresh).

## Manual region-tagging skill (🖱 Region Tag)

With the **"🖱 Region Tag"** switch on, you can **drag a box** over any region of the frame; a panel opens:

- add a note → **save as a spot** (blue animated box, shares the same system as AI spots: visible when playback
  reaches its time, right-click to delete/hide/edit, viewable/restorable in the 📌 list);
- or click **"🤖 Ask Agent"** → in the prompt box **type your own question** (e.g. "Watch the child's hand movements
  over a few seconds - what mental pattern does it suggest?"; leave empty for the default "What is in this region?
  What can I attend to?") → the backend sends the cropped region + script context to the Gemini VLM → save
  **"Save Spot (with analysis)"** in one click; the analysis is also recorded into chat memory.

The switch state is stored in localStorage and survives a refresh; a selection defaults to the current playback position
±2 seconds. The **📌 Spots (N)** button in the top-left opens a menu listing every saved spot (sorted by time, showing
label / time / originating question / description); clicking an item jumps to that time and plays.

## Side-by-side comparison with the original video

After configuring `RAW_VIDEO_PATH` (the source material of the AI-generated version) in `config.py`, the
**"⧉ Compare Original"** button appears in the top-right of the video pane: it shows the original video on the right,
**synced by progress ratio** (different durations still align automatically) for frame-by-frame comparison.
Click again to go back to a single screen. When `RAW_VIDEO_PATH = None` the button is hidden.

## Module overview (change one block without touching the others)

| File | Responsibility | Public interface | Change this to… |
|---|---|---|---|
| `main.py` | Assembles every module (the only place that knows the whole graph) | — | swap player/frame source, rewire the Q&A chain |
| `config.py` | All configurable settings | — | paths, model, retrieval parameters |
| `position.py` | Playback-position source | `get_position() -> float \| None` | use another player / read position differently |
| `frame.py` | Live frame source | `get_frame(t)`, `get_window(t,secs,…)` | change frame capture (ffmpeg/screenshot/custom) |
| `knowledge.py` | Script+transcript → time index | `get_context(t,b,a)`, `chapter_at(t)`, `search(q,k)`, `full_text()` | add material formats, swap the retrieval algorithm |
| `agent.py` | Gemini VLM Q&A | `ask(…)` (single frame), `ask_video(question, ctx, frames)` (multi-frame), `judge_video_seconds(…)` (window), `analyze_region(…)` | swap model/prompts, tune window rules |
| `attention.py` | Spot analysis (VLM marks key behaviors in frames) | `analyze(question, frames) -> {points:…} \| None` | change annotation rules / add dimensions |
| `memory.py` | Chat memory (multi-turn coherence) | `add(q,a)`, `context_text(n,chars)`, `clear()` | change retained turns / persistence |
| `prompts.py` | All prompts in one place | `SYSTEM_PROMPT` / `JUDGE_PROMPT` / `ATTENTION_PROMPT` / `REGION_PROMPT` | edit prompts in this one file |
| `ui.py` | Legacy tkinter overlay | `CompanionUI(get_position, get_chapter, ask)` | restyle UI / add voice input |

**Convention**: every module is injected through `main.py`/`server.py`; modules never import each other. As long as
the public interface signature stays the same, you can rewrite the internals of any file without touching the others.

## Combined retrieval when woken (Multimodal RAG)

`server.py`'s `/ask` assembles the input for Gemini in three stages:

1. **Stage one (judge)**: a lightweight call asks Gemini "how many seconds of video are needed to answer"
   (0 = script alone suffices; cap = `MAX_VIDEO_SECONDS`). Whether to annotate is NOT decided by the model —
   it is explicitly controlled by the front-end toggle (`attention_enabled`).
2. **Stage two (answer)**: sample frames inside the judged window (`frame.py.get_window`, every `FRAME_EVERY_SECONDS`,
   at most `MAX_FRAMES_PER_ASK` frames), together with three text blocks sent to Gemini:
   - **current-position context**: current paragraph + `CTX_BEFORE`/`CTX_AFTER` neighbors (marked "(current)")
   - **script keyword search hits**: `kb.search(question)` → top `SEARCH_TOP_K` segments
   - **full script** (when `INCLUDE_FULL_SCRIPT` is on)
3. **Stage three (on-demand annotation)**: only when the annotation toggle is on and frames exist, `attention.py` asks
   Gemini to mark "behaviors worth attending to" (time range + position + label), **in parallel** with stage two to
   shorten the wait; the front-end overlays breathing highlight boxes, then automatically **jumps back to the question
   time and plays** — animations persist inside each spot range and disappear when playback leaves it.

## Player integration

- **mpv** (recommended): start with `--input-ipc-server=/tmp/mpv-socket`; the project reads it directly.
- **IINA**: Preferences → Advanced → enable "mpv IPC", point `MPV_SOCKET` at the path IINA shows.
- **VLC**: `vlc --extraintf=http --http-port=8080 --http-password=1234 video`, set `PLAYER = "vlc"`, and put your
  password in `VLC_URL`.
- **Other players**: write a class like `MpvSource` in `position.py`, and add one branch in `build_position_source()`.

## Frame sources (frame.py)

| FRAME_SOURCE | Description | Dependency |
|---|---|---|
| `ffmpeg` (recommended) | extracts frames from the original video file at the current time; the frame IS the video content | `brew install ffmpeg`; fill `VIDEO_PATH` in config |
| `screen` | captures the whole screen; sees "what is playing on your screen" | macOS asks for "Screen Recording" permission on first run |
| `off` | no frames, text-only Q&A (previous behavior) | none |

## Material formats (auto-detected by knowledge.py)

1. **SRT / VTT subtitles** (most common for transcripts)
2. **Lines with a leading timestamp**: `[mm:ss] text` or `mm:ss text`
3. **Section headings with a time**: `## Title [mm:ss]` (most common for detailed scripts)
4. **Plain-text script**: split by `# headings`; if the whole file has no timestamps, fill `TOTAL_DURATION` in
   `config.py` (total video seconds) and the system estimates by text length (marked "estimated").

To support a new format: add a `parse_xxx()` in `knowledge.py` and call it from `_load_file`.

## Gemini model configuration

Create an API key at [aistudio.google.com](https://aistudio.google.com).
**Security**: the public repo must not contain a real key. `config.py` ships with the placeholder
`GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"`; create `config_local.py` (git-ignored) with your real key and it
overrides the placeholder automatically:

```python
# config_local.py  (never commit this file)
GEMINI_API_KEY = "AQ.xxxx..."
```

- `gemini-3.8-flash`: fast and cheap, enough for daily use (default; the old 2.5-flash was decommissioned by Google)
- switch to a Gemini Pro model for stronger reasoning (edit `GEMINI_MODEL`)

## FAQ

- **Overlay says "no player connected"**: mpv is not running, or `MPV_SOCKET` is wrong.
- **Answer does not match the current frame**: first check whether the status-bar time is moving; if it is, check
  `VIDEO_PATH` (used by ffmpeg sampling).
- **No ffmpeg / don't want to install it**: use `FRAME_SOURCE = "screen"` (screenshot) or `"off"` (text only).
- **Script retrieval is weak**: `search()` is a naive keyword scorer; for large scripts, swap in embedding retrieval
  (e.g. `sentence-transformers`) — the `knowledge.search` interface stays the same.
