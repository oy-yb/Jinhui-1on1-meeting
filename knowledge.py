"""Script + transcript -> time index -> context around the current playback position.

Public interface:
    KnowledgeBase(files, total_duration=None)      load materials
    kb.get_context(t, before, after) -> str        current segment + neighbors (fed to the LLM)
    kb.chapter_at(t) -> str                        current chapter title (for live display)

Supported material formats (multiple files can be mixed):
1. SRT / VTT subtitles                     -- most common for transcripts
2. Lines with a leading timestamp: [mm:ss] text / mm:ss text
3. Markdown headings with time: # Title [mm:ss]   -- most common for detailed scripts
4. Plain text: treated as one segment; if there are no timestamps, time is estimated from config.TOTAL_DURATION (marked "estimated")

To support a new format: add a parse_xxx() here that returns a list of entries and call it
in _load_file; other modules are unaffected.
"""

import bisect
import re

_TIME = r"(\d{1,2}):(\d{2})(?::(\d{2}))?"  # mm:ss or hh:mm:ss
_SRT = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)
_TS_LINE = re.compile(rf"^\s*\[?{_TIME}\]?\s+(.*)$")
_HEADING = re.compile(rf"^#{{1,3}}\s+(.+?)(?:\s*\[{_TIME}\])?\s*$")
_RANGE = re.compile(r"\[(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})\]")  # inline time range [00:00-00:02]


def _tokens(s):
    """Split a query into matchable tokens: English by word, Chinese by adjacent bigrams (raises hit rate)."""
    s = s.lower()
    toks = []
    for chunk in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", s):
        if chunk.isascii():
            toks.append(chunk)
        else:
            toks.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return toks


# ---------------- Parsers (each returns a list of entries) ----------------

def _to_sec(a, b, c=None):
    """Two numbers -> m:ss; three numbers -> h:mm:ss."""
    if c is None:
        return int(a) * 60 + int(b)
    return int(a) * 3600 + int(b) * 60 + int(c)


def _parse_srt(text, tag="transcript"):
    """SRT/VTT: index / timeline / content, repeated."""
    entries, cur = [], None
    for line in text.splitlines():
        m = _SRT.search(line)
        if m:
            start = _to_sec(m.group(1), m.group(2), m.group(3))
            end = _to_sec(m.group(5), m.group(6), m.group(7))
            cur = {"start": start, "end": end, "text": "", "tag": tag}
            continue
        if cur is not None:
            if line.strip() == "":
                if cur["text"]:
                    entries.append(cur)
                cur = None
            else:
                cur["text"] += line.strip() + " "
    if cur and cur["text"]:
        entries.append(cur)
    return entries


def _parse_ts_lines(text, tag="transcript"):
    """Lines starting with [mm:ss] / mm:ss; each is tentatively 60s and corrected by the next timestamp."""
    entries = []
    for line in text.splitlines():
        m = _TS_LINE.match(line)
        if m:
            t = _to_sec(m.group(1), m.group(2), m.group(3))
            entries.append({"start": t, "end": t + 60, "text": m.group(4).strip(), "tag": tag})
    return entries


def _parse_time_ranges(text, tag="script"):
    """Storyboard script with inline time ranges: [00:00-00:02] or **[00:00-00:02]**.
    Text after a marker belongs to that range; text before the first marker (intro) is attached at 0s."""
    entries, cur, head = [], None, []
    for line in text.splitlines():
        m = _RANGE.search(line)
        if m:
            start, end = _to_sec(m.group(1), m.group(2)), _to_sec(m.group(3), m.group(4))
            if head and cur is None:  # intro text -> attach at 0s
                entries.append({"start": 0, "end": start, "text": "\n".join(head).strip(),
                                "tag": tag, "title": "intro"})
                head = []
            if cur:
                entries.append(cur)
            rest = line[m.end():].strip().replace("**", "")  # strip Markdown bold markers
            cur = {"start": start, "end": end, "text": rest, "tag": tag}
        elif cur is not None:
            cur["text"] += "\n" + line.strip().replace("**", "")
        else:
            head.append(line)
    if cur:
        entries.append(cur)
    return entries


def _parse_md_sections(text, tag="script"):
    """Markdown script: sections split by #/## headings, body accumulates until the next heading.
    A heading with [mm:ss] gets a real time; without one, start=None (estimated later).
    The first timestamp-less H1 is treated as the document title and skipped."""
    entries, cur, first_heading = [], None, True
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            if cur and cur["text"]:
                entries.append(cur)
            title = m.group(1).strip()
            has_time = m.group(2) is not None
            if first_heading and not has_time and line.strip().startswith("# "):
                cur = None  # document title; skip
                first_heading = False
                continue
            first_heading = False
            start = _to_sec(m.group(2), m.group(3), m.group(4)) if has_time else None
            cur = {"start": start, "end": None, "text": title, "tag": tag,
                   "est": start is None, "title": title}
        elif cur is not None:
            cur["text"] += line.strip() + " "
    if cur and cur["text"]:
        entries.append(cur)
    return entries


# ---------------- Time index ----------------

class KnowledgeBase:
    def __init__(self, files, total_duration=None):
        self.entries = []
        for path in files:
            self.entries += self._load_file(path)
        if not self.entries:
            raise ValueError(f"No parseable content found in materials, please check the format: {files}")

        # Entries with time first, timestamp-less (to estimate) last
        self.entries.sort(key=lambda e: (e["start"] is None, e["start"] or 0))
        self._assign_estimated_times(total_duration)

        # Fill end: use the next entry's start when missing/too short (last one gets start+60)
        for i, e in enumerate(self.entries):
            nxt = self.entries[i + 1]["start"] if i + 1 < len(self.entries) else e["start"] + 60
            if not e.get("end") or e["end"] <= e["start"]:
                e["end"] = max(nxt, e["start"] + 1)

        self.chapters = [e for e in self.entries if e.get("title")]

    def _load_file(self, path):
        text = self._read(path)
        lines = text.splitlines()
        if _SRT.search(text):
            return _parse_srt(text)
        if _RANGE.search(text):
            return _parse_time_ranges(text)
        if sum(1 for ln in lines[:100] if _TS_LINE.match(ln)) >= 3:
            return _parse_ts_lines(text)
        if any(_HEADING.match(ln) for ln in lines):
            entries = _parse_md_sections(text)
            return entries or [{"start": None, "end": None, "text": text.strip(), "tag": "script"}]
        # Plain text: treat the whole file as one segment
        return [{"start": None, "end": None, "text": text.strip(), "tag": "script"}]

    def _assign_estimated_times(self, total):
        """Assign times to entries with start=None, after the last real time.
        With total: distribute by remaining duration and text length; without: 60s each."""
        est = [e for e in self.entries if e["start"] is None]
        if not est:
            return
        real_end = 0.0
        for e in self.entries:
            if e["start"] is not None:
                real_end = max(real_end, e.get("end") or e["start"])
        if total and total > real_end:
            span = total - real_end
        else:
            span = 60.0 * len(est)  # no duration info: fall back to 60s per segment
        weights = [max(len(e["text"]), 1) for e in est]
        acc = real_end
        for e, w in zip(est, weights):
            e["start"], e["end"] = round(acc, 1), round(acc + span * w / sum(weights), 1)
            e["est"] = True
            acc = e["end"]

    # ---------------- Query ----------------

    def get_context(self, t, before=2, after=2):
        """Return text around the current playback position (with time labels) as LLM context."""
        if not self.entries:
            return "(no script/transcript content loaded)"
        idx = self._index_at(t)
        lo, hi = max(0, idx - before), min(len(self.entries), idx + after + 1)
        return "\n\n".join(self._format(self.entries[i], i == idx) for i in range(lo, hi))

    def chapter_at(self, t):
        """Return the current chapter title for display."""
        if not self.chapters:
            return "(no chapter info)"
        idx = max(0, bisect.bisect_right([c["start"] for c in self.chapters], t or 0) - 1)
        c = self.chapters[idx]
        return f"{self._fmt(c['start'])} {c['title']}"

    def search(self, query, top_k=3):
        """Keyword search across the whole script/transcript, returning the top_k most relevant segments (formatted text list).

        Used for questions like "what was said earlier/elsewhere" - combined with the current paragraph for full retrieval.
        Naive implementation (English word tokens + Chinese bigram scoring); fine for small scripts.
        For more precision, swap the scoring for embeddings - the interface stays the same."""
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        scored = []
        for e in self.entries:
            text = e["text"].lower()
            score = sum(text.count(tok) for tok in q_tokens)
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        return [self._format(e, False) for e in (s[1] for s in scored[:top_k])]

    def full_text(self, max_chars=20000):
        """Concatenate all entries in time order (attach the full script during combined retrieval so the model can reference globally)."""
        out, size = [], 0
        for e in self.entries:
            s = self._format(e, False)
            size += len(s)
            if size > max_chars:
                break
            out.append(s)
        return "\n\n".join(out)

    def _index_at(self, t):
        if t is None:
            return 0
        starts = [e["start"] for e in self.entries]
        return max(0, bisect.bisect_right(starts, t) - 1)

    def _format(self, e, is_current):
        t = self._fmt(e["start"]) + (f"–{self._fmt(e['end'])}" if e.get("end") else "")
        meta = " · ".join(x for x in (e.get("tag", ""), "est. by length" if e.get("est") else "") if x)
        body = e["text"].strip()
        if e.get("title"):
            title = e["title"]
            body = body[len(title):].strip() if body.startswith(title) else body
            body = f"[{title}] {body}"
        mark = "(current)" if is_current else ""
        head = f"[{t}] {meta}" if meta else f"[{t}]"
        return f"{head} {mark}\n{body}".strip()

    @staticmethod
    def _read(path):
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                with open(path, encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise IOError(f"cannot decode file: {path}")

    @staticmethod
    def _fmt(sec):
        if sec is None:
            return "?:??"
        m, s = divmod(int(sec), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
