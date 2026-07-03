"""
One-shot builder: merges the three source KB markdown files into a single,
cleaned, consistently-numbered knowledge_base.md.

What it does:
  - Strips the "Deep Research continuation" prompt-instruction junk (file 2 header).
  - Drops File 1's brief overview subsections for topics that File 2 covers in
    full (Nakshatra, Dignity, Avastha, Drishti, Dasha, Divisional, Yogas) to
    remove duplicate information.
  - Drops meta appendices that are prompt-design leftovers, not KB knowledge
    (File 3 sections 17 "Appendix B" and 18 "Open questions").
  - Removes inline [^n] footnote markers and footnote-definition lines.
  - Inserts top-level section headers for File 2's flat topic groups.
  - Re-numbers every heading consistently: 1., 2., 2.1, 2.1.1, ...

Run once:  python build_knowledge_base.py
"""

import re
from pathlib import Path

DOWNLOADS = Path(r"C:\Users\reksn\Downloads")
SRC = {
    1: DOWNLOADS / "StarSage Knowledge Base 1.md",
    2: DOWNLOADS / "StarSage Knowledge Base 2.md",
    3: DOWNLOADS / "StarSage Knowledge Base 3.md",
}
OUT = Path(__file__).parent / "knowledge_base.md"

# (file, start_line, end_line_inclusive, inserted_section_header_or_None)
# Line numbers are 1-based and were verified against the source files.
SEGMENTS = [
    # ---- File 1: Purpose + Core Concepts (Graha, Bhava, Rashi) ----
    (1, 3, 239, None),
    # ---- File 1: Lordship + Placement (concept intros; File 2 has applied) ----
    (1, 272, 335, None),
    # ---- File 2: full standalone topic sections (junk header 1-247 dropped) ----
    (2, 248, 317, "## Nakshatras"),
    (2, 318, 432, "## Dignity (Strength and Weakness)"),
    (2, 433, 462, "## Avastha (Planetary States)"),
    (2, 463, 495, "## Drishti (Aspects)"),
    (2, 496, 543, "## House Lordship in Practice"),
    (2, 544, 628, "## Divisional Charts"),
    (2, 629, 732, "## Yogas (Planetary Combinations)"),
    (2, 733, 953, "## Dasha and Timing"),
    # ---- File 3: Psychological translation + safety + style + examples ----
    # (sections 17 "Appendix B" and 18 "Open questions" dropped as meta leftovers)
    (3, 3, 939, None),
]

FOOTNOTE_DEF = re.compile(r"^\s*\[\^[^\]]+\]:\s")
FOOTNOTE_INLINE = re.compile(r"\[\^[^\]]+\]")
HEADING = re.compile(r"^(#{2,4})\s+(.*)$")
# strip an existing leading number like "1. ", "11. ", "2.1 ", "3.2.1 "
LEADING_NUM = re.compile(r"^\d+(\.\d+)*\.?\s+")


def load(fp: Path) -> list[str]:
    return fp.read_text(encoding="utf-8").splitlines()


def clean_block(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if FOOTNOTE_DEF.match(ln):
            continue  # drop footnote definition lines
        ln = FOOTNOTE_INLINE.sub("", ln)  # strip inline [^n] markers
        out.append(ln.rstrip())
    return out


def assemble() -> list[str]:
    body: list[str] = []
    for file_idx, start, end, header in SEGMENTS:
        raw = load(SRC[file_idx])
        chunk = raw[start - 1:end]  # 1-based inclusive -> slice
        chunk = clean_block(chunk)
        if header:
            body.append("")
            body.append(header)
            body.append("")
        body.extend(chunk)
    return body


def renumber(lines: list[str]) -> list[str]:
    n2 = n3 = n4 = 0
    out = []
    for ln in lines:
        m = HEADING.match(ln)
        if not m:
            out.append(ln)
            continue
        hashes, text = m.group(1), m.group(2).strip()
        text = LEADING_NUM.sub("", text)  # remove any existing numbering
        level = len(hashes)
        if level == 2:
            n2 += 1
            n3 = 0
            n4 = 0
            out.append(f"## {n2}. {text}")
        elif level == 3:
            n3 += 1
            n4 = 0
            out.append(f"### {n2}.{n3} {text}")
        elif level == 4:
            n4 += 1
            out.append(f"#### {n2}.{n3}.{n4} {text}")
    return out


def collapse_blanks(lines: list[str]) -> list[str]:
    out = []
    blanks = 0
    for ln in lines:
        if ln.strip() == "":
            blanks += 1
            if blanks <= 2:
                out.append("")
        else:
            blanks = 0
            out.append(ln)
    # trim leading/trailing blanks
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return out


def main():
    body = assemble()
    body = renumber(body)
    body = collapse_blanks(body)

    header = [
        "# StarSage Knowledge Base",
        "",
        "_Source-grounded Vedic astrology and psychological-translation guide._",
        "_Single canonical knowledge base for the StarSage assistant._",
        "",
    ]
    OUT.write_text("\n".join(header + body) + "\n", encoding="utf-8")

    # quick report
    text = "\n".join(body)
    top = [l for l in body if re.match(r"^## \d+\. ", l)]
    print(f"Wrote {OUT}")
    print(f"  Lines: {len(body) + len(header)}")
    print(f"  Top-level sections: {len(top)}")
    for t in top:
        print(f"    {t[3:]}")


if __name__ == "__main__":
    main()
