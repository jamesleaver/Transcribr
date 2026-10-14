"""Write the installer's licence page: docs/licence/EULA.md as plain
text.

    python3 tools/eula_text.py OUT.txt

One source for the EULA, so the installers cannot drift from it.
"""

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "docs" / "licence" / "EULA.md"


def eula_text(md):
    # The Markdown title becomes the page's first line; bold can wrap
    # across lines, so it comes off the whole text at once.
    body = re.sub(r"\*\*(.+?)\*\*", r"\1", md, flags=re.S)
    out = []
    for line in body.splitlines():
        m = re.match(r"^#+ (.*)", line)
        if m:
            line = m.group(1) if line.startswith("# ") else m.group(1).upper()
        out.append(line)
    return "\n".join(out).strip() + "\n"


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: eula_text.py OUT.txt")
    Path(argv[1]).write_text(eula_text(SRC.read_text(encoding="utf-8")),
                             encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
