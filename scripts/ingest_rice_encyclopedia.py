"""
One-shot: chunk rice_paddy_cultivation_encyclopedia.md into section JSON for
universal_kb. Each chunk is one ## or ### heading + its body. Output goes to
data/seed/rice_encyclopedia_sections.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "reference" / "rice_paddy_cultivation_encyclopedia.md"
DST = ROOT / "data" / "seed" / "rice_encyclopedia_sections.json"

HEAD_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:80] or "section"


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    matches = list(HEAD_RE.finditer(text))
    sections: list[dict] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        if level == 1:
            continue  # skip top-level document title
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body or len(body) < 40:
            continue
        # Cap body to keep prompt budget sane
        if len(body) > 1800:
            body = body[:1800].rstrip() + "…"
        sections.append({
            "id": f"rice_enc_{slug(heading)}_{i}",
            "crop": "rice",
            "heading": heading,
            "level": level,
            "text": body,
            "source": "rice_paddy_cultivation_encyclopedia.md",
        })
    DST.write_text(
        json.dumps({"rice_encyclopedia_sections": sections}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(sections)} sections to {DST}")


if __name__ == "__main__":
    main()
