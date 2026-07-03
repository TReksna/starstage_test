"""
Knowledge base accessor.

The StarSage knowledge base lives in a single markdown file (`knowledge_base.md`)
and is NOT stored in the database. This module parses that file into navigable,
numbered sections and provides:

  - tree()              hierarchical section tree for the Knowledge Base tab
  - section(number)     full content of one section (e.g. "3", "3.1", "4.3.1")
  - resolve(term)       map a [[kb:term]] keyword to the best-matching section
  - search(query, n)    keyword retrieval used by the LLM pipeline

The file is cached in memory and reloaded automatically when it changes on disk.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Optional

KB_PATH = Path(__file__).parent / "knowledge_base.md"

# Matches a numbered heading: "## 3. Nakshatras", "### 3.1 What ...", "#### 4.3.1 ..."
_HEADING = re.compile(r"^(#{2,4})\s+(\d+(?:\.\d+)*)\.?\s+(.*)$")

_cache: dict = {"mtime": None, "sections": [], "by_number": {}}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Alias map: [[kb:term]] keyword (lowercased) -> section number
# ---------------------------------------------------------------------------

ALIAS_TO_SECTION: dict[str, str] = {
    # Nakshatras
    "nakshatra": "3", "nakshatras": "3", "janma nakshatra": "3",
    "lunar mansion": "3", "pada": "3",
    # Dignity
    "dignity": "4", "exaltation": "4", "exalted": "4", "debilitation": "4",
    "debilitated": "4", "moolatrikona": "4", "own sign": "4",
    "combustion": "4.6", "combust": "4.6", "retrograde": "4.6", "vargottama": "4.6",
    # Avastha
    "avastha": "5", "planetary state": "5", "baladi": "5",
    # Aspects
    "aspect": "6", "aspects": "6", "drishti": "6",
    # Lordship
    "lordship": "7", "house lord": "7", "lord": "7", "dispositor": "7",
    "dusthana": "7", "kendra": "7", "trikona": "7", "upachaya": "7",
    # Divisional charts
    "divisional chart": "8", "divisional charts": "8", "varga": "8",
    "navamsa": "8", "navamsha": "8", "d9": "8", "d10": "8", "dashamsha": "8",
    "hora": "8", "drekkana": "8", "saptamsha": "8",
    # Yogas
    "yoga": "9", "yogas": "9", "raja yoga": "9", "dhana yoga": "9",
    "gajakesari": "9", "neechabhanga": "9", "neecha bhanga": "9",
    "pancha mahapurusha": "9", "kemadruma": "9", "viparita": "9",
    "parivartana": "9",
    # Dasha and timing
    "dasha": "10", "dasa": "10", "mahadasha": "10", "antardasha": "10",
    "pratyantardasha": "10", "vimshottari": "10", "vimsottari": "10",
    "timing": "10", "transit": "10",
    # Core concepts
    "graha": "2.1", "planet": "2.1", "planets": "2.1",
    "bhava": "2.2", "house": "2.2", "houses": "2.2",
    "rashi": "2.3", "rasi": "2.3", "sign": "2.3", "zodiac": "2.3",
    "placement": "2.5",
    # Psychological / safety
    "psychological translation": "11", "jungian": "11", "archetype": "11",
    "cbt": "11", "reframing": "11", "myers-briggs": "11", "myers briggs": "11",
    "person-centered": "11", "person centered": "11", "coaching": "11",
    "reflective mode": "12", "coaching mode": "12",
    "safety": "13", "crisis": "13", "boundary": "13", "referral": "13",
    "memory": "14", "writing style": "15",
}

# Planet names -> Graha section
for _p in ["sun", "moon", "mars", "mercury", "jupiter", "venus", "saturn",
           "rahu", "ketu"]:
    ALIAS_TO_SECTION.setdefault(_p, "2.1")

# Ordinal house names -> Bhava section
for _ord in ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th",
             "10th", "11th", "12th"]:
    ALIAS_TO_SECTION.setdefault(f"{_ord} house", "2.2")

# Planet + period combos (e.g. "mercury antardasha") -> Dasha section
for _p in ["sun", "moon", "mars", "mercury", "jupiter", "venus", "saturn",
           "rahu", "ketu"]:
    for _period in ["mahadasha", "antardasha", "dasha"]:
        ALIAS_TO_SECTION.setdefault(f"{_p} {_period}", "10")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse(md_text: str) -> tuple[list[dict], dict]:
    """Parse markdown into a flat list of sections + a number->section index."""
    lines = md_text.splitlines()
    sections: list[dict] = []
    current: Optional[dict] = None
    for ln in lines:
        m = _HEADING.match(ln)
        if m:
            if current:
                current["content"] = "\n".join(current["_buf"]).strip()
                del current["_buf"]
            number = m.group(2)
            current = {
                "number": number,
                "depth": number.count(".") + 1,
                "title": m.group(3).strip(),
                "content": "",
                "_buf": [],
            }
            sections.append(current)
        elif current is not None:
            current["_buf"].append(ln)
    if current:
        current["content"] = "\n".join(current["_buf"]).strip()
        del current["_buf"]

    by_number = {s["number"]: s for s in sections}
    return sections, by_number


def _load() -> tuple[list[dict], dict]:
    """Load + cache the KB, reloading if the file changed on disk."""
    with _lock:
        if not KB_PATH.exists():
            return [], {}
        mtime = KB_PATH.stat().st_mtime
        if _cache["mtime"] != mtime:
            sections, by_number = _parse(KB_PATH.read_text(encoding="utf-8"))
            _cache.update({"mtime": mtime, "sections": sections, "by_number": by_number})
        return _cache["sections"], _cache["by_number"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def tree() -> list[dict]:
    """Return the section list with parent_number for hierarchical rendering."""
    sections, _ = _load()
    out = []
    for s in sections:
        parent = s["number"].rsplit(".", 1)[0] if "." in s["number"] else None
        out.append({
            "number": s["number"],
            "title": s["title"],
            "depth": s["depth"],
            "parent_number": parent,
            "preview": s["content"][:160].replace("\n", " "),
        })
    return out


def section(number: str) -> Optional[dict]:
    """Return one section by its number (e.g. '3.1'), with its content."""
    _, by_number = _load()
    s = by_number.get(number)
    if not s:
        return None
    return {"number": s["number"], "title": s["title"], "content": s["content"]}


def resolve(term: str) -> Optional[dict]:
    """Map a [[kb:term]] keyword to its best-matching section."""
    _, by_number = _load()
    if not by_number:
        return None
    key = term.strip().lower()

    # 1. Exact alias hit
    num = ALIAS_TO_SECTION.get(key)
    # 2. Longest alias that appears as a substring of the term
    if not num:
        candidates = [a for a in ALIAS_TO_SECTION if a in key]
        if candidates:
            num = ALIAS_TO_SECTION[max(candidates, key=len)]
    # 3. Title contains the term
    if not num:
        for s in by_number.values():
            if key in s["title"].lower():
                num = s["number"]
                break
    if not num or num not in by_number:
        return None
    s = by_number[num]
    return {"number": s["number"], "title": s["title"], "matched": term}


def search(query: str, limit: int = 3) -> str:
    """Keyword retrieval over sections — returns formatted text for the pipeline."""
    sections, _ = _load()
    if not sections:
        return ""
    q_words = {w for w in re.findall(r"[a-z]{4,}", query.lower())}
    if not q_words:
        return ""
    scored = []
    for s in sections:
        title_l = s["title"].lower()
        content_l = s["content"].lower()
        score = 0
        for w in q_words:
            if w in title_l:
                score += 3
            if w in content_l:
                score += 1
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]
    if not top:
        return ""
    return "\n\n".join(
        f"### {s['number']} {s['title']}\n{s['content'][:1200]}" for _, s in top
    )
