"""All configurable settings live here. Edit config to adapt your player, materials, frame source and model - no code changes needed."""

# ---------- Player position source ----------
# "mpv": mpv or IINA (recommended, most stable)
# "vlc": VLC (requires the HTTP interface enabled)
# "manual": no player; position fixed to MANUAL_POSITION (lets you try without a player)
PLAYER = "mpv"

# mpv example: mpv --input-ipc-server=/tmp/mpv-socket your.mp4
# IINA: Preferences -> Advanced -> enable "mpv IPC", then point the path below to the socket IINA shows
MPV_SOCKET = "/tmp/mpv-socket"

# VLC example: vlc --extraintf=http --http-port=8080 --http-password=1234 your.mp4
VLC_URL = "http://:1234@localhost:8080"

# Fixed playback position (seconds) in manual mode, for testing
MANUAL_POSITION = 65.0

# ---------- Script & transcript materials ----------
# Each element is a file path (relative to this project directory).
# Supported formats (see knowledge.py header): SRT/VTT subtitles, [mm:ss] timestamp lines, timed headings, plain text
MATERIAL_FILES = [
    "detailed transcriptmd.md",  # your detailed script (storyboard with [mm:ss-mm:ss] range markers)
]

# Total video duration (seconds). Only used when your materials have no timestamps at all:
# content is then spread across the timeline proportional to text length (marked "estimated"). None = don't estimate.
TOTAL_DURATION = None

# ---------- Gemini VLM (frames + text Q&A) ----------
# Create an API Key at Google AI Studio (aistudio.google.com).
# SECURITY: the public repo must NOT contain a real key. Put your real key in config_local.py
# (git-ignored) and it will override this placeholder automatically when present:
#   GEMINI_API_KEY = "AQ.xxxx"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-3.8-flash"

# Safety thresholds: relax the adjustable categories only (child sexual abuse stays at the platform hard-default).
# Shared by agent.py (Q&A) and attention.py (spot analysis).
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
]

# Input sanitization: the classifier occasionally misfires on "child + certain words" (here: "banana" in the script).
# Equivalent replacement is applied only to the text fed to the model; your script files are untouched.
SAFE_REPLACEMENTS = {"香蕉": "黄色水果"}

# ---------- Live frames (captured when the user asks) ----------
# "ffmpeg": extract frames from the original video at the current time (recommended: clean frames, no system permission; requires ffmpeg)
# "screen": capture the whole screen (requires macOS "Screen Recording" permission; may include other windows)
# "off"   : no frames, text-only Q&A (previous behavior)
FRAME_SOURCE = "ffmpeg"
VIDEO_PATH = "/Users/oy/Desktop/UIUC Projects/VSR/demo/gemini_generated_video.mp4"

# Original video (source material of the AI-generated version): the front-end can open a side-by-side comparison.
# Currently it points to the first 20 seconds (raw_20s.mp4, matching the script range); point back to raw video.mp4 for the full clip.
# If None or missing, the compare button is hidden.
RAW_VIDEO_PATH = "/Users/oy/Desktop/UIUC Projects/VSR/demo/raw_20s.mp4"

# ---------- Combined retrieval ----------
# When answering, besides "current paragraph + context", also provide:
# - SEARCH_TOP_K segments keyword-searched from the full script (for cross-paragraph questions)
# - optionally the full script text (enable when materials are small; lets the model reference globally)
SEARCH_TOP_K = 3
INCLUDE_FULL_SCRIPT = True
FULL_SCRIPT_MAX_CHARS = 20000

# ---------- Video frame window (the agent decides how many seconds of video are needed) ----------
# The agent first decides how many seconds of video the question needs (0 = script alone suffices; cap = MAX_VIDEO_SECONDS),
# then samples frames around the current position every FRAME_EVERY_SECONDS seconds within that window and sends them to Gemini.
MAX_VIDEO_SECONDS = 15      # Max frame window per answer (seconds)
FRAME_EVERY_SECONDS = 2.0   # Frame sampling interval within the window (seconds)
MAX_FRAMES_PER_ASK = 8      # Max frames per answer
DEFAULT_VIDEO_SECONDS = 4   # Fallback window (seconds) when duration judgment fails

# ---------- Context window ----------
# Current paragraph + N paragraphs before/after (the "current position" context fed to the model)
CTX_BEFORE = 2
CTX_AFTER = 2

# ---------- Local overrides (never committed) ----------
# Put your real API key (and any personal overrides) in config_local.py - it is git-ignored,
# so the public repository never ships secrets while the local server keeps working.
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass
