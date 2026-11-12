"""記号の種類（検出のクラス名）が配線解析でどんな役割を持つか。

クラス名の集合はここだけで決め、各段は用途に合わせた名前でこれを組み合わせて使う。種類を足すときはここを直す。
"""

from __future__ import annotations

# 端子をすべて同じ点として扱う記号（配線の通り道）。
PASS_THROUGH = frozenset({"connector", "junction", "terminal", "power_bus"})
JUNCTION = "junction"
# 画像上で確認した黒丸の中心をそのまま端子にしたことを表す source 語彙。
EXPLICIT_NODE_CENTER_SOURCE = "explicit_node_center"
# 2 端子で開閉する接点。
CONTACTS = frozenset({"contact_a", "contact_b"})
# コイル: 配線がその中で途切れて描かれる。途切れの橋渡しはその中央を通さない。
COILS = frozenset({"relay_coil", "solenoid"})
# 2 端子の機器（両端の間が機器そのもの）。
TWO_TERMINAL_DEVICES = CONTACTS | COILS | frozenset({"lamp", "push_button", "diode"})

