"""Entry point: assembles every module and runs the floating overlay window.

main.py is the only place that knows all modules. Change a module's internals freely:
as long as its public interface stays the same, no other module needs to change.
"""

from config import (PLAYER, MATERIAL_FILES, TOTAL_DURATION,
                    CTX_BEFORE, CTX_AFTER, FRAME_SOURCE,
                    SEARCH_TOP_K, INCLUDE_FULL_SCRIPT, FULL_SCRIPT_MAX_CHARS)
import position
import knowledge
import agent
import frame as frame_mod
from ui import CompanionUI


def build_position_source():
    if PLAYER == "mpv":
        return position.MpvSource()
    if PLAYER == "vlc":
        return position.VlcSource()
    if PLAYER == "manual":
        return position.ManualSource()
    raise ValueError(f"unknown player: {PLAYER} (choose mpv / vlc / manual)")


def build_frame_source():
    if FRAME_SOURCE == "ffmpeg":
        return frame_mod.FfmpegFrameSource()
    if FRAME_SOURCE == "screen":
        return frame_mod.ScreenFrameSource()
    if FRAME_SOURCE == "off":
        return None
    raise ValueError(f"unknown frame source: {FRAME_SOURCE} (choose ffmpeg / screen / off)")


def main():
    kb = knowledge.KnowledgeBase(MATERIAL_FILES, total_duration=TOTAL_DURATION)
    src = build_position_source()
    frame_src = build_frame_source()

    def asker(question: str) -> str:
        """Full Q&A chain: current position -> text context (current segment + script search + optional full text) -> frames -> Gemini."""
        t = src.get_position()

        text = kb.get_context(t, before=CTX_BEFORE, after=CTX_AFTER)
        hits = kb.search(question, top_k=SEARCH_TOP_K)
        if hits:
            text += "\n\n[Script keyword search hits]\n" + "\n\n".join(hits)
        if INCLUDE_FULL_SCRIPT:
            text += "\n\n[Full script]\n" + kb.full_text(max_chars=FULL_SCRIPT_MAX_CHARS)

        frame_bytes = frame_src.get_frame(t) if frame_src else None
        return agent.ask(question, text, frame_bytes)

    print(f"loaded {len(kb.entries)} segments; player source: {src.name}")
    print(f"frame source: {frame_src.name if frame_src else 'off (text only)'}; model: {agent.GEMINI_MODEL}")
    print("if the context looks wrong when asking, first check the player position source.")

    ui = CompanionUI(get_position=src.get_position, get_chapter=kb.chapter_at, ask=asker)
    ui.run()


if __name__ == "__main__":
    main()
