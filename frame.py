"""Live frame source: grabs the video frame at the "current playback position" (JPEG bytes) when the user asks.

Public interface:
    get_frame(t) -> bytes | None   frame JPEG at time t; None on failure or when disabled
    get_window(t, seconds, every, max_frames) -> [(t, jpeg_bytes)]
        sample multiple frames every `every` seconds within a window of `seconds` around t (ascending),
        clipped to the video bounds; degrades to a single frame for short videos / on failure

FRAME_SOURCE = "ffmpeg": extract frames from the original video by time (recommended: clean frames, no system permission)
FRAME_SOURCE = "screen": capture the whole screen (requires macOS "Screen Recording" permission; may include other windows)
FRAME_SOURCE = "off"   : no frames (text-only Q&A)

To add a source: write a class with get_frame / get_window like below and add a branch in build_frame_source.
"""

import subprocess

from config import FRAME_SOURCE, VIDEO_PATH

FRAME_PATH = "/tmp/video_companion_frame.jpg"


class FfmpegFrameSource:
    """Extract frames from the original video with ffmpeg at the given time. Requires ffmpeg installed (brew install ffmpeg)."""

    name = "ffmpeg frames (original video)"

    def __init__(self):
        self.duration = self._probe_duration()

    def _probe_duration(self):
        """Probe total video duration (seconds); None on failure."""
        if not VIDEO_PATH:
            return None
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", VIDEO_PATH],
                capture_output=True, timeout=15)
            return float(out.stdout.strip())
        except Exception:
            return None

    def get_frame(self, t):
        if not VIDEO_PATH or t is None:
            return None
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", VIDEO_PATH,
                 "-frames:v", "1", "-q:v", "2", FRAME_PATH],
                capture_output=True, timeout=30)
            with open(FRAME_PATH, "rb") as f:
                return f.read()
        except Exception:
            return None

    def get_window(self, t_center, seconds, every=2.0, max_frames=8):
        """Sample frames within a window of `seconds` around t_center, every `every` seconds.

        Returns [(t, jpeg_bytes), ...] (t rounded to 0.01s, ascending).
        The window is clipped to [0, video duration]; on extraction failure it degrades to a single frame at the center.
        """
        if not VIDEO_PATH or t_center is None:
            return []
        if seconds <= 0:
            b = self.get_frame(t_center)
            return [(round(t_center, 2), b)] if b else []
        half = seconds / 2.0
        lo = max(0.0, t_center - half)
        hi = min(t_center + half, self.duration) if self.duration else t_center + half
        ts, f = [], lo
        while f <= hi + 1e-6 and len(ts) < max_frames:
            ts.append(f)
            f += every
        if ts and ts[-1] < hi - 0.01 and len(ts) < max_frames:
            ts.append(hi)
        out = []
        for tt in ts:
            b = self.get_frame(tt)
            if b:
                out.append((round(tt, 2), b))
        return out or [(round(t_center, 2), self.get_frame(t_center))]


class ScreenFrameSource:
    """macOS screencapture. The system asks for "Screen Recording" permission on first run."""

    name = "screen capture (macOS screencapture)"

    def get_frame(self, t):
        try:
            subprocess.run(["screencapture", "-x", "-t", "jpg", FRAME_PATH],
                           capture_output=True, timeout=10)
            with open(FRAME_PATH, "rb") as f:
                return f.read()
        except Exception:
            return None

    def get_window(self, t_center, seconds, every=2.0, max_frames=8):
        """A screenshot only captures the present moment and cannot go back in time: degrade to a single frame."""
        b = self.get_frame(t_center)
        return [(round(t_center, 2), b)] if b else []
