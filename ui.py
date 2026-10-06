"""Floating overlay UI: shows the live playback time / current chapter and lets you "wake" the agent by typing a question.

Public interface:
    CompanionUI(get_position, get_chapter, ask)
      - get_position() -> float | None      current playback seconds (refreshed once per second)
      - get_chapter(t) -> str               current chapter title
      - ask(question) -> str                full Q&A: position -> context -> LLM (runs on a background thread)

This module imports no other business module - it only depends on the three callbacks injected by main.py,
so restyling the UI, adding voice input or swapping the overlay implementation affects nothing else.
"""

import threading
import tkinter as tk
from tkinter import scrolledtext


def fmt_time(sec):
    if sec is None:
        return "--:--"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class CompanionUI:
    def __init__(self, get_position, get_chapter, ask):
        self.get_position = get_position
        self.get_chapter = get_chapter
        self.ask = ask

        self.root = tk.Tk()
        self.root.title("Video Companion")
        self.root.overrideredirect(True)        # frameless
        self.root.attributes("-topmost", True)  # always on top
        self.root.configure(bg="#1e293b")
        self.root.geometry("440x340+80+80")
        self._drag = None

        # Top status bar: time | current chapter (also serves as the drag handle)
        self.status = tk.Label(self.root, text="…", bg="#1e293b", fg="#e2e8f0",
                               font=("Helvetica", 11), anchor="w", padx=8, pady=4)
        self.status.pack(fill="x")
        self.status.bind("<Button-1>", self._start_drag)
        self.status.bind("<B1-Motion>", self._on_drag)

        # Close button
        close = tk.Label(self.root, text="✕", bg="#1e293b", fg="#94a3b8", padx=6)
        close.place(relx=1.0, x=-4, y=2)
        close.bind("<Button-1>", lambda e: self.root.destroy())

        # Input row
        row = tk.Frame(self.root, bg="#1e293b")
        row.pack(fill="x", padx=8, pady=(0, 6))
        self.entry = tk.Entry(row, bg="#0f172a", fg="#f1f5f9",
                              insertbackground="#f1f5f9", relief="flat", font=("Helvetica", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.entry.bind("<Return>", lambda e: self.ask_now())
        btn = tk.Label(row, text="Ask", bg="#2563eb", fg="white", padx=10, pady=4,
                       font=("Helvetica", 10, "bold"))
        btn.pack(side="left", padx=(6, 0))
        btn.bind("<Button-1>", lambda e: self.ask_now())

        # Answer area
        self.answer = scrolledtext.ScrolledText(self.root, wrap="word", height=12,
            bg="#0f172a", fg="#e2e8f0", relief="flat", font=("Helvetica", 10.5),
            insertbackground="#e2e8f0")
        self.answer.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.answer.configure(state="disabled")
        self.answer.tag_configure("user", foreground="#93c5fd")
        self.answer.tag_configure("agent", foreground="#a7f3d0")
        self.answer.tag_configure("dim", foreground="#94a3b8")
        self._append("Type a question and press Enter; the agent answers based on the current playback position.\n", "dim")

        self.root.after(500, self._tick)

    def run(self):
        self.root.mainloop()

    # ---------------- Window dragging ----------------
    def _start_drag(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _on_drag(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    # ---------------- Live status ----------------
    def _tick(self):
        t = self.get_position()
        if t is None:
            self.status.config(text="--:--  no player connected (check config.py / whether the player is running)")
        else:
            self.status.config(text=f"{fmt_time(t)}  |  {self.get_chapter(t)}")
        self.root.after(500, self._tick)

    # ---------------- Asking ----------------
    def ask_now(self):
        q = self.entry.get().strip()
        if not q:
            return
        self.entry.delete(0, "end")
        self._append(f"\nYou: {q}\n", "user")
        self._append("agent thinking…\n", "dim")
        threading.Thread(target=self._do_ask, args=(q,), daemon=True).start()

    def _do_ask(self, q):
        try:
            ans = self.ask(q)
        except Exception as ex:
            ans = f"Error: {ex}\n(check the LLM settings in config.py / whether the model service is running)"
        self.root.after(0, lambda: self._append(f"agent: {ans}\n", "agent"))

    def _append(self, text, tag=None):
        self.answer.configure(state="normal")
        self.answer.insert("end", text, tag or ())
        self.answer.configure(state="disabled")
        self.answer.see("end")
