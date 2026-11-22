"""AI の結果（構造解析・推論）をエディタに足す候補にする細かい場合（services/ai_candidates）。"""

from services.ai_candidates import AiCandidates, AiSymbol, AiTerminal, _free_ref, _greedy, from_structure, plan_import

KEYS = ["junction", "contact_a", "other", "relay_coil"]


def _box(x0, y0, x1, y1):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _port(ref, x, y):
    return {"ref": ref, "point": [x, y]}


def test_structure_skips_unknown_classes_and_places_bare_junctions_at_the_box_center():
    structure = {"image_size": {"width": 1000, "height": 1000}, "symbols": [
        {"id": "U", "class_name": "mystery", "bbox": _box(0, 0, 10, 10)},
        {"id": "J", "class_name": "junction", "bbox": _box(100, 200, 110, 210)},
    ]}
    found = from_structure(structure, KEYS)
    [junction] = found.symbols
    assert junction.class_key == "junction"
    assert (junction.terminals[0].tx, junction.terminals[0].ty) == (0.105, 0.205)


def test_card_frame_links_only_ports_drawn_at_the_same_point():
    # 2 つの接点を囲む細い枠（カード）。枠の中の「internal」のつながりは、同じ点の端子どうしだけ導線にする
    structure = {"image_size": {"width": 1000, "height": 1000}, "symbols": [
        {"id": "F", "class_name": "other", "bbox": _box(100, 100, 140, 300),
         "terminals": [_port("a", 120, 110), _port("b", 120, 112), _port("c", 120, 290)]},
        {"id": "C1", "class_name": "contact_a", "bbox": _box(110, 130, 130, 150),
         "terminals": [_port("t1", 120, 130), _port("t2", 120, 150)]},
        {"id": "C2", "class_name": "contact_a", "bbox": _box(110, 230, 130, 250),
         "terminals": [_port("t1", 120, 230), _port("t2", 120, 250)]},
    ], "from_to": {"connections": [
        {"from": {"symbol_id": "F", "terminal_ref": "a"}, "to": {"symbol_id": "F", "terminal_ref": "b"}, "type": "internal"},
        {"from": {"symbol_id": "F", "terminal_ref": "a"}, "to": {"symbol_id": "F", "terminal_ref": "c"}, "type": "internal"},
    ]}}
    found = from_structure(structure, KEYS)
    assert found.links == (((0, 0), (0, 1)),)


def test_greedy_matching_and_free_refs():
    assert _greedy([(0.9, 0, 0), (0.8, 0, 1), (0.7, 1, 0), (0.6, 1, 1)]) == {0: 0, 1: 1}
    used = {"SYM-0001", "SYM-0002"}
    assert _free_ref(lambda n: f"SYM-{n:04d}", 1, used) == "SYM-0003" and "SYM-0003" in used


def test_links_onto_one_existing_terminal_are_kept_or_merged():
    # 既存の記号 A（端子 1 個）に、AI の 2 端子の記号が重なる。2 つの端子はどちらも既存の端子に寄る
    existing = [{"ref": "A", "class_key": "relay_coil", "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1,
                 "terminals": [{"ref": "A-T1", "tx": 0.5, "ty": 0.45}]}]
    coil = AiSymbol("relay_coil", 0.5, 0.5, 0.1, 0.1, 0.5, 0.5,
                    terminals=(AiTerminal(0.5, 0.45, None), AiTerminal(0.5, 0.451, None)))
    dot = AiSymbol("junction", 0.8, 0.45, 0.01, 0.01, 0.8, 0.45, terminals=(AiTerminal(0.8, 0.45, None),))
    plan = plan_import(AiCandidates("structure", (coil, dot), (((0, 0), (0, 1)), ((0, 0), (1, 0)), ((0, 1), (1, 0)))),
                       existing, [], 1000, 1000)
    assert plan.kept_connections == 1                    # 端子どうしが同じ点になった線は残すだけ
    assert len(plan.connections) == 1                    # A-T1 と黒丸の線は 1 本にまとめる
    assert plan.connections[0]["from_terminal_ref"] == "A-T1"
