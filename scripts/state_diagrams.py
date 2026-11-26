"""状態の移り方（services/lifecycles.py）を Mermaid の状態図にして docs/state-machines.md に書き出す（DD-0047）。

    python scripts/state_diagrams.py          # 書き出す
    python scripts/state_diagrams.py --check  # 文書が定義と同じか確かめる（違えば 1 で終わる）

状態図は定義から作るので、移り方を変えたらこのスクリプトを回して文書も直す（tests/test_lifecycles.py が確かめる）。
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.lifecycles import MACHINES  # noqa: E402

DOC = ROOT / "docs" / "state-machines.md"

HEADER = """# 状態の移り方（状態図）

このファイルは `python scripts/state_diagrams.py` が `services/lifecycles.py` から作る。手で直さない（DD-0047）。

状態の値は DB・API の文字列そのまま。図の矢印に無い移り方は `TransitionNotAllowed` になる。
"""


def render() -> str:
    parts = [HEADER]
    for name, machine in MACHINES.items():
        doc = inspect.cleandoc(machine.__doc__ or "")  # 3.13 未満は docstring の字下げが残る
        values = "、".join(f"`{s.value!r}`（{s.name}）" if s.value == "" else f"`{s.value}`（{s.name}）"
                          for s in machine.states)
        parts.append(f"## {name}（`{machine.__name__}`）\n\n{doc}\n\n値: {values}\n\n"
                     f"```mermaid\n{format(machine, 'mermaid').rstrip()}\n```\n")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="書き出さずに食い違いだけを確かめる")
    args = parser.parse_args()
    text = render()
    if args.check:
        if not DOC.is_file() or DOC.read_text(encoding="utf-8") != text:
            print(f"{DOC.relative_to(ROOT)} が定義と違います。python scripts/state_diagrams.py を回してください", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(text, encoding="utf-8")
    print(f"wrote {DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
