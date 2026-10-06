"""Chat memory: keeps the Q&A history so multi-turn interactions stay coherent
(e.g. when the user asks "what did the child do?" or "which sound did she model?",
the agent can resolve the referents from previous answers).

Public interface:
    add(question, answer)                    record one Q&A round
    context_text(max_turns=3, max_chars=2000) -> str
        format the most recent rounds as text, for the assembly layer to prepend
    clear()                                  wipe

Pure in-memory (per-process; restarts clear it). To persist: write history to a file in add()
- interface unchanged, other modules unaffected. Python standard library only.
"""


class Memory:
    def __init__(self, max_rounds=20):
        self.max_rounds = max_rounds
        self.history = []   # [{"q": question, "a": answer}, ...] in chronological order

    def add(self, question, answer):
        self.history.append({"q": question, "a": answer})
        if len(self.history) > self.max_rounds:
            self.history = self.history[-self.max_rounds:]

    def context_text(self, max_turns=3, max_chars=2000):
        """Plain text of the most recent max_turns rounds; empty string when there is no history."""
        if not self.history:
            return ""
        turns = self.history[-max_turns:]
        parts = [f"Q: {t['q']}\nA: {t['a']}" for t in turns]
        text = "\n\n".join(parts)
        return text[:max_chars]

    def clear(self):
        self.history = []

    @property
    def turns(self):
        return len(self.history)
