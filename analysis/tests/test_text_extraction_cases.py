"""文字の読み取りの細かな規則（語の端の記号、見分けにくい字、表の欄、表題欄、読み直し）。"""

from __future__ import annotations

from collections import Counter

from PIL import Image

from analysis import text_extraction as tx
from analysis.tests.test_text_extraction import box, item


def test_token_cleanup_and_confusable_corrections():
    assert tx._clean_token("CN211:") == "CN211"
    assert tx._clean_token("(ER1)") == "(ER1)"
    assert tx.correct_token("12", Counter({"1Z": 9})) == "12"
    assert tx.correct_token("33L8", Counter({"33LB": 3, "33L8": 1})) == "33LB"
    assert tx.best_label("XQ9", "33HB", Counter({"33HB": 2})) == "33HB"
    assert tx.best_label("33HB", "XQ9", Counter({"33HB": 1})) == "33HB"
    assert [i["text"] for i in tx.drop_glyph_marks([item("CN1本-3", 0, 0, 10, 10)])] == ["CN1 -3"]


def test_vertical_text_is_split_top_to_bottom():
    chars = tx._char_boxes(item("AB", 0, 0, 10, 40))
    assert chars == [("A", (0, 0.0, 10, 20.0)), ("B", (0, 20.0, 10, 40.0))]


def test_other_label_drops_single_letters():
    items = [item("A", 105, 105, 110, 112), item("CARD", 120, 105, 150, 112)]
    assert tx.symbol_label(items, box(100, 100, 200, 140), "other") == "CARD"


def test_relay_table_cells_and_boxes():
    assert tx._position("12") == ""
    assert tx._position("120") == "12D"
    single = [("5-9-1", item("5-9-1", 100, 700, 130, 712))]
    blocks = tx._column_blocks(single)
    assert blocks[0][1] == 12
    assert tx.relay_table_box([], 1000, 1000) is None


def test_title_block_and_destinations_inside_frames():
    items = [item("シート番号", 800, 900, 860, 912), item("名称", 900, 920, 930, 932)]
    assert tx.title_block_box(items, 1000, 1000) == (790.0, 800.0, 1000.0, 1000.0)
    assert tx.title_block_box([], 1000, 1000) is None
    frame = [item("(ER21212)", 105, 105, 160, 115)]
    assert tx.classify_other(frame, box(100, 100, 300, 200), "ABC") == "destination"


def test_page_tiles_overlap_and_each_text_is_kept_once():
    image = Image.new("RGB", (1500, 300), "white")
    tiles = tx.page_tiles(image)
    assert [t.origin for t in tiles] == [(0, 0), (1000, 0)]

    # 重なりの部分の文字（ページの x=1240 px、2 倍で 2480）は、どちらのタイルにも写る
    origins = iter(t.origin[0] for t in tiles)

    def tile_reader(_pixels, **_kwargs):
        origin = next(origins)
        return [([[2480 - origin, 10], [2500 - origin, 10], [2500 - origin, 30], [2480 - origin, 30]], "33HB", 0.9)], None

    texts = tx.read_page_text(image, tile_reader)
    assert [t["text"] for t in texts] == ["33HB"] and texts[0]["box"][0] == 1240


def test_extract_reads_crops_rereads_doubtful_labels_and_types_other_frames():
    image = Image.new("RGB", (800, 600), "white")
    symbols = [
        {"id": "s1", "class_name": "contact_a", "bbox": box(100, 100, 160, 140)},
        {"id": "s2", "class_name": "other", "bbox": box(300, 100, 500, 200)},
    ]

    def page_reader(_pixels, **_kwargs):
        return [([[620, 220], [740, 220], [740, 240], [620, 240]], "MCCB", 0.9)], None

    def crop_reader(_pixels, **_kwargs):
        # 切り出しの左上は (92, 92)、4 倍。ラベルの位置 (110, 110)-(150, 125)
        return [([[72, 72], [232, 72], [232, 132], [72, 132]], "??", 0.5)], None

    asked = []

    def llm(_image, symbol_box):
        asked.append(symbol_box)
        return "33HB"

    out = tx.extract_text_structure(image, symbols, [], page_reader, label_reader=llm, crop_reader=crop_reader)
    contact, frame = symbols
    assert contact["label"] == "33HB" and contact["label_source"] == "llm" and contact["ocr_label"] == ""  # 「??」は記号だけなので消える
    assert frame["other_kind"] == "breaker"
    assert out["relay_table"]["rows"] == []
