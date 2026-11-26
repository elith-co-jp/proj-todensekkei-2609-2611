# データ交換フォーマット

エクスポートで出力される ZIP は 1 つで 2 つの役割を兼ねます。

```
<root>/
├── bundle.json                     完全復元用（本ツールの正本）
├── classes.txt                     クラス名（yolo_index 順・0 始まり）
├── data.yaml                       ultralytics 用の設定
├── dataset/
│   ├── images/train/*.png          全画像（val 分割はしない）
│   └── labels/train/*.txt
├── connections/
│   ├── connections.csv             配線 (from-to) の一覧
│   └── netlist.json                from-to を連結したネット
└── README.txt
```

ファイル名は `p<project_id>_<元のファイル名>` に正規化されます（`images` と `labels` で対応）。

---

## 1. YOLO 形式（学習用）

### labels/*.txt

1 行 1 シンボル。区切りは半角スペース。座標は画像サイズで割った正規化値、小数 6 桁。

```
class_id cx cy w h
```

例:

```
0 0.382353 0.499653 0.058824 0.083187
3 0.546078 0.315255 0.033333 0.047139
```

`class_id` は `classes.txt` の行番号（0 始まり）と一致します。

### classes.txt

`yolo_index` の昇順にクラスキーを 1 行ずつ並べます。

```
relay_coil
contact_a
contact_b
terminal
...
```

### data.yaml

```yaml
path: ./dataset
train: images/train
val: images/train
nc: 12
names:
  0: relay_coil
  1: contact_a
  ...
```

エクスポートは train/val 分割をせず、全画像を `dataset/images/train` に出力します。
`val` は ultralytics が必須とするため `images/train` と同じ場所を指します
（学習・検証を分けたい場合は出力後に手動で分割してください）。

---

## 2. 配線（from-to）

YOLO の物体検出フォーマットは矩形しか表現できないため、配線は別ファイルに出力します。

### connections/connections.csv

BOM 付き UTF-8。Excel でそのまま開けます。

| 列 | 内容 |
|----|------|
| `project_id` / `sheet_no` | 図面の識別 |
| `from_symbol_ref` / `from_class` / `from_label` / `from_terminal` | 始点 |
| `to_symbol_ref` / `to_class` / `to_label` / `to_terminal` | 終点 |
| `wire_no` | 電線番号（610 等） |
| `net_id` | 同一ネットのグルーピング（任意） |
| `kind` | `wire`（配線）／ `sheet_ref`（シート間参照） |
| `external_ref` | `(610-2F-9)` 等の参照先原文 |
| `note` | 備考 |

端子を指定していない配線は `from_terminal` / `to_terminal` が空になります（シンボル単位の登録）。

### connections/netlist.json

from-to を Union-Find で連結し、導通する端子集合（ネット）にまとめたものです。

```json
{
  "schema_version": "1.0",
  "projects": [
    {
      "project_id": 1,
      "sheet_no": "ER21216",
      "connection_count": 2,
      "nets": [
        { "id": "NET-0001", "wire_no": "610",
          "members": ["SYM-0001:SYM-0001-T2", "SYM-0002:SYM-0002-T1"] }
      ]
    }
  ]
}
```

`members` は端子を指定した場合 `シンボルref:端子ref`、指定しない場合は `シンボルref` になります。

---

## 3. bundle.json（完全復元用）

本ツールの正本です。これがあればシンボル・端子・配線・図面情報・クラス定義まで復元できます。

```json
{
  "schema_version": "1.0",
  "tool": "seq-annotator",
  "exported_at": "2026-08-05T07:51:38+09:00",
  "classes": [
    { "key": "relay_coil", "label": "リレーコイル", "yolo_index": 0,
      "color": "#0055a4", "sort_order": 0, "is_active": true }
  ],
  "projects": [
    {
      "id": 1,
      "name": "ER21216",
      "sheet_no": "ER21216", "page_no": "133", "revision": "0",
      "source_file": "ER21216.png",
      "image_width": 1755, "image_height": 1241,
      "status": "draft", "assignee": null, "note": null,
      "images": [
        { "filename": "ER21216.png", "sha256": "…", "width": 1755, "height": 1241 }
      ],
      "symbols": [
        {
          "ref": "SYM-0001",
          "class_key": "relay_coil", "class_label": "リレーコイル", "yolo_index": 0,
          "label": "33HB",
          "label_box": { "cx": 0.401, "cy": 0.47, "w": 0.02, "h": 0.012 },
          "cx": 0.382353, "cy": 0.499653, "w": 0.058824, "h": 0.083187,
          "note": null,
          "terminals": [
            { "ref": "SYM-0001-T1", "name": "13", "tx": 0.3824, "ty": 0.5411, "origin": "manual" }
          ]
        }
      ],
      "connections": [
        {
          "from_symbol_ref": "SYM-0001", "from_terminal_ref": "SYM-0001-T1",
          "to_symbol_ref": "SYM-0002", "to_terminal_ref": null,
          "wire_no": "610", "net_id": null,
          "kind": "wire", "external_ref": null, "note": null, "origin": "manual"
        }
      ]
    }
  ]
}
```

### 参照の解決規則

- `ref` はプロジェクト内で一意な文字列。DB の主キーではないため、別環境に取り込んでも一致します。
- 端子の `ref` は `<シンボルref>-T<連番>` を既定とします。
- 配線の `*_terminal_ref` が `null` の場合はシンボル単位の接続を意味します。
- 記号の `label_box` は、ラベル（器具番号）の文字が図面に書かれた位置（正規化の中心と幅・高さ）。記号の枠は印だけを囲み、
  文字は枠の外にあるので、どの文字をラベルにしたかをここで示す（DD-0049）。付けていなければ `null`。YOLO のラベルには入りません。
- インポート時、同じ `key` のクラスは既存の `yolo_index` を再利用し、未知のクラスは末尾に採番します。
  **class id を厳密に一致させたい場合は、取り込み先を空の状態にしてからインポートしてください。**

---

## 4. インポートの挙動

| 入力 | 判定 | 復元される内容 |
|------|------|----------------|
| `bundle.json` を含む ZIP | bundle 形式 | シンボル・端子・配線・図面情報・クラス定義（完全復元） |
| `bundle.json` が無い ZIP | YOLO 形式 | 矩形のみ（配線・端子は含まれない） |

YOLO 形式で受け付けるレイアウト:

- `images/*.png` + `labels/*.txt` + `classes.txt`
- `project_<id>/images/*.png` + `project_<id>/labels/*.txt` + `classes.txt`
- 上記が複数並んだ一括エクスポート

インポートは常に**新しい図面として追加**され、既存データを上書きしません。
同じ ZIP を 2 回取り込むと重複します。

### 推論結果 ZIP（`/api/ml/inference/import`）

エクスポートとは別経路。外部環境で `yolo predict --save-txt --save-conf` を実行した
出力を、既存図面への**推論結果**として取り込む（新規図面は作らない）。

- `p<図面ID>_*.txt` — `cls cx cy w h [conf]` の YOLO ラベル形式。`conf` は任意の 6 列目。
- `classes.txt` — 任意。ある場合は行番号 → クラス key の対応に使う（無い場合は
  `symbol_classes.yolo_index` で直接解決）。
- 図面 ID はエクスポート画像名（`p<図面ID>_*.png`）と同じ規則でステムから取り出す。
- 取り込みは図面ごとに**置き換え**（predictions は最新のみ保持）。存在しない図面 ID と
  `p<ID>_` 形式でないファイルは結果の `missing_project_ids` / `unmatched_files` に返す。

### セーフガード

| 対策 | 上限 |
|------|------|
| パストラバーサル（絶対パス・`..`） | 拒否 |
| `__MACOSX` / `.DS_Store` / `._*` | 無視 |
| ファイル数 | 20,000 |
| 展開後の合計サイズ | 2 GiB |
| 圧縮率（10 MB 超のとき） | 25 倍まで |
| 画像 1 枚あたり | 64 MiB |

## 5. 構造解析結果（structure.json）

AI 改善サイクル画面の構造解析をまとめて書き出したファイル（`POST /api/ml/analysis/export`）。AI の予測だけで作り、人が入力したアノテーションは使わない。

```json
{
  "schema_version": "todensekkei.document_structure.v1",
  "summary": {
    "page_count": 2, "symbol_count": 92, "wire_count": 140, "from_to_connection_count": 480,
    "sheet_reference_count": 6, "resolved_sheet_reference_count": 2,
    "cross_sheet_relay_count": 4, "linked_cross_sheet_relay_count": 3,
    "cross_sheet_relay_row_count": 2, "linked_cross_sheet_relay_row_count": 2
  },
  "pages": [
    { "schema_version": "todensekkei.sequence_extraction.v1", "sheet_no": "ER21216", "symbols": [], "texts": [] }
  ]
}
```

ページ（`pages[]`）の主な項目は次のとおり。座標は 150 dpi で画像化したページのピクセル。

| 項目 | 内容 |
|------|------|
| `sheet_no` | 図面情報で確定したシート番号（未入力なら null） |
| `symbols[]` | 検出したシンボル。`label` は器具番号のラベル。`status` はリレー表・表題欄の中の検出なら `suppressed_text_zone`（配線に使わない） |
| `symbols[].terminals[].name` | 端子番号。コネクタは `CN229-6 TB16-102` のように器具名と番号の組。接点は両端の番号（9・5 など。読めなければ P1・P2）。リレー表から補った番号は `name_source: "relay_table"` |
| `symbols[].terminal_numbers[]` | コネクタの器具名（`device`）と番号（`number`）の組 |
| `symbols[].dot_role` | 黒丸の役割。`terminal`（左右に文字がある端子）、`junction`（3 方向以上に配線が分かれる分岐点）、`review`（判定できない）。同じ黒丸の重複検出は `suppressed: true` |
| `symbols[].relay_row` | リレー表の行と行列の位置で結び付いた接点。`row`、`device`、`terminal`、`contact`（a / b）、`position`、`table_terminal_numbers`（a は [COM, NO]、b は [COM, NC]）、`terminal_check`（一致・不一致・未読）、`class_check`（一致・不一致・表に合わせて変更）、`status`（linked / 要確認）、`reasons`。別シートの行は書き出し時に付き `project_id` を持つ |
| `symbols[].detected_class` / `class_source` | 種別を直したときだけ付く。元の検出の種別と、直した根拠（`relay_table` など） |
| `symbols[].symbol_review` | 円形の記号（リレーコイル・表示灯）の確認。`class_suggestion`、`cues`（× 印・表示灯の語・リレー表・同じ器具の接点）、`status`（確定 / 要確認） |
| `symbols[].other_kind` | 「その他」の区分（暫定）。`destination`（行先・来先）、`card`、`terminal_unit`（端子台ユニット）、`equipment`（盤・装置）、`breaker`（遮断器）、`unknown` |
| `symbols[].relay_link` | 書き出し時だけ付く。ラベルに別のシート番号がある接点・コイルについて、その図面（`project_id`）の同じ器具番号のコイル（`coil_symbol_ids`）とリレー表の行番号（`relay_table_rows`）。参照先の図面を同時に書き出していなければ付かない |
| `symbols[].label_source` | `llm` のときローカル LLM が読み直したラベル。元の OCR の読みは `ocr_label` |
| `symbols[].label_box` / `label_link` | 枠の中に器具番号が無い記号（印だけの枠）に、近くの文字のまとまりを結んだとき（DD-0049）。`label_box` はその文字の位置 `[x0, y0, x1, y1]`。`label_link` は `status`（linked / 要確認 / 未検出）、`direction`（right・above など）、`gap`（枠どうしの隙間 px）、`candidates`（近い順の候補 3 つ） |
| `symbols[].attributes` / `attribute_inference` | 図面の文字から推定した属性の候補（DD-0050）。`attributes` は `timer_mode`（on_delay / off_delay）・`delay_s`（秒）・`latch_coil`（set / reset）・`signal_name`・`external_io`（yes）。`attribute_inference` は `evidence[]`（`attribute`・`value`・根拠の文字 `text` と位置 `box`・`review`）と `review[]`（人が確かめる理由）。AI 取込で記号の属性に入り、注記に理由が付く |
| `unlinked_captions[]` | 母線の上の縦書きの名前のうち、どの記号にも結べなかったもの（`text`・`box`。DD-0050） |
| `texts[]` | 読めた文字。`kind` は `device_number`（器具番号）、`number`（端子番号・電線番号）、`destination`（行先・来先）、`relay_terminals`（リレー端子 5-9-1）、`name`（装置名・信号名称。`symbol_id` の記号に結び付く）、`note`（注記）、`other`。`zone` はリレー表・表題欄の中なら `relay_table` / `title_block`。読み直したものは `source`（`name_ocr`: 同梱の日本語対応 OCR、`llm`: ローカル LLM）と元の読み `ocr_text` を持つ |
| `relay_table.rows[]` | リレー表の行。`device`（器具番号）、`terminal`（5-9-1）、`contact`（a / b）、`sheet`（「本」またはシート番号）、`position`（行列 10D） |
| `relay_table.links[]` | 器具番号ごとに、表の行（`rows`）と同じ図面のコイル（`coil_symbol_ids`）・接点（`contact_symbol_ids`）。コイルが無い、または「本」の接点が足りないときは `status: "要確認"`。`row_links[]` は行ごとの行列の位置での紐付け（`row`、`position`、`sheet`、`symbol_id`、`status`、`reasons`） |
| `drawing_grid` | 図面の枠の列番号・行の文字から求めた行列の位置（`columns`、`rows`） |
| `sheet_references[]` | 括弧つき・「へ」「より」つきのシート番号。`kind` は `wire`（配線の行先・来先）か `relay`（接点のコイルがあるシート。`symbol_id` に接点・コイルの記号）。書き出し時に `target_project_id`（同時に書き出した図面のうちシート番号が一致する図面。無ければ null）を付ける |
| `sheet_number_candidates[]` | シート番号の候補（`text`、`score`、`region`） |
| `regions[]` | 領域。`region_type` は `drawing_area`（作図領域）、`title_block`（表題欄）、`relay_table`（リレー表）、`panel`（盤・カード）、`loc`、`cloud`（雲マーク、`arc_count` つき）。`source` は `detection`（検出した大枠）、`text`（文字から求めた範囲）、`image`（画像から求めた雲マーク） |
| `from_to` | 端子と端子の接続 |
| `dot_roles` | 黒丸の判定件数と判定に使った値 |
| `quality.symbol_label_ocr` | ラベルを読んだ件数。`llm_reread_count` / `llm_text_reread_count` はローカル LLM、`name_ocr_reread_count` は日本語対応 OCR が読み直した件数。`quality.relay_rows_by_position`（行列で結び付いた行数）、`quality.circle_review`（円形の記号の確認件数） |

アプリの DB では、図面ごとの `texts[]` を `structure_predictions.texts_json` にも写して持ち、回路の組み立てはそちらを読む（DD-0042。書き出す structure.json の内容は変わらない）。

参照先の図面は、図面情報のシート番号（未入力ならシート番号の候補の 1 位）で照合する。枝番つきの参照（`ER21212-1`）は枝番なしの図面にも合わせる。


## 6. 記号・配線の属性（object_attributes.json）

記号と配線の 1 つ 1 つに、メーカー・型式・長さなどの属性を持たせる（DD-0010）。属性は bundle.json の
`symbols[].attributes`・`connections[].attributes` にも入り、ZIP の書き出し・取り込みで引き継がれる。
配線には図面内で一意な参照名 `connections[].ref`（W-0001 形式）があり、保存し直しても変わらない。

データ受け渡しの画面（`POST /api/attributes/export`）で、全オブジェクトをオブジェクト ID と 1 対 1 で並べた JSON を書き出す。

```json
{
  "schema": "todenyolo.object_attributes.v1",
  "generated_at": "2026-11-07T15:00:00+09:00",
  "template": true,
  "attribute_definitions": { "symbol": { "common": [], "by_class": {} }, "wire": [] },
  "projects": [{ "project_id": 1, "key": "ER21216", "sheet_no": "ER21216", "name": "ER21216.png" }],
  "objects": [
    { "object_id": "ER21216/SYM-0001", "project_id": 1, "sheet_no": "ER21216", "type": "symbol",
      "ref": "SYM-0001", "class_key": "relay_coil", "label": "33HB FV2306",
      "attributes": { "maker": "オムロン", "model": null, "part_no": null, "rated_voltage_v": null,
                      "location": null, "coil_resistance_ohm": 4400, "pickup_voltage_v": null } },
    { "object_id": "ER21216/W-0001", "project_id": 1, "sheet_no": "ER21216", "type": "wire",
      "ref": "W-0001", "from": "SYM-0002/SYM-0002-T1", "to": "SYM-0001/SYM-0001-T1", "wire_no": null,
      "attributes": { "length_m": 12.5, "wire_type": "CVV 2sq", "cross_section_mm2": null, "color": null,
                      "cable_no": null, "core_no": null, "maker": null } }
  ]
}
```

| 項目 | 説明 |
| --- | --- |
| `object_id` | 「図面の鍵/参照名」。図面の鍵はシート番号（無ければ `P<図面ID>`、書き出す図面の間で重なれば `シート番号#図面ID`） |
| `type` | `symbol`（記号）か `wire`（配線） |
| `attributes` | キー → 値（文字・数値・真偽）。`template: true` では定義済みの項目を null で並べる（入力用） |

標準の属性（`GET /api/attributes/definitions`）。ここに無い項目も自由に足せる。

| 対象 | 項目（キー・単位） |
| --- | --- |
| 記号（共通） | メーカー `maker`、型式 `model`、品番 `part_no`、定格電圧 `rated_voltage_v`（V）、取付位置 `location` |
| リレーコイル | コイル抵抗 `coil_resistance_ohm`（Ω）、動作電圧 `pickup_voltage_v`（V）、タイマーの種類 `timer_mode`（on_delay / off_delay。オンディレー・オフディレー・限時動作・限時復帰も可）、タイマーの時間 `delay_s`（s）（DD-0033） |
| 電磁弁 | コイル抵抗 `coil_resistance_ohm`（Ω）、定格電流 `rated_current_a`（A） |
| a 接点・b 接点 | 接点定格 `contact_rating_a`（A）、タイマーの接点 `timer_contact`（瞬時なら `instant`。DD-0033）、外部入力・I/O `external_io`（yes なら図面範囲外の入力。DD-0035） |
| 押しボタン | 接点の種類 `contact_kind`（a / b。空なら名前で決める: 停止は b・起動は a。DD-0037） |
| ダイオード | アノード側の端子名 `anode_terminal`（空なら端子名 A・K で決める。DD-0037） |
| コネクタ・端子 | ピン数 `pin_count` |
| 表示灯 | 定格電力 `rated_power_w`（W） |
| 配線 | 長さ `length_m`（m）、電線種別 `wire_type`、断面積 `cross_section_mm2`（mm²）、色 `color`、ケーブル番号 `cable_no`、心線番号 `core_no`、メーカー `maker` |

取り込み（`POST /api/attributes/import`）:

- ファイルに載っているオブジェクトの属性を、その内容で置き換える。null・空文字の項目は未入力として消す。載っていないオブジェクトは変えない。
- 図面は `project_id` とシート番号で照合し、図面 ID が違う別の環境でもシート番号が 1 枚に決まれば取り込む。
- 同じ `object_id` が 2 回以上あるものは取り込まない（`duplicates`）。見つからないもの（`not_found`）、記号と配線の種類が合わないもの（`type_mismatch`）、数値の項目に数値でない値（`invalid`、その項目だけ取り込まない）を報告する。数値の項目は「3,000」のような文字でも数値にする。
- 同じファイルをもう一度取り込んでも変わらない。

回路シミュレーション（DD-0009）は、リレーコイル・電磁弁の `coil_resistance_ohm`（または `resistance_ohm`）があればその値を負荷の抵抗に使う。

---

## 7. 回路の書き出し

回路検証（SPECIFICATION.md §3.6）で、選んだ図面のアノテーションから組んだ回路を次の形で書き出す。どれも図面上の位置は含まず、
位置の正本は bundle.json（§3）のアノテーション。

| ファイル | API | 内容 |
|---|---|---|
| `circuit_code.py` | `POST /api/circuit/code` | 回路を書き換えられる Python のコード（DD-0029）。`CircuitCode` の呼び出し（`contact_a`・`contact_b`・負荷・タイマー）を図面ごとに並べたもので、リポジトリの中で `python circuit_code.py` と実行すると回路が組み上がる。各素子の `source=(図面 ID, 記号 ref)` で元の記号に戻れる。電気的なつながりと、リレーのコイルと接点の対応の正本 |
| `circuit.cir` | `POST /api/circuit/netlist` | SPICE ネットリスト。LTspice・ngspice で開ける。接点は与えた入力・励磁の状態の開閉で出す |
| `circuit_lcapy.txt` | `POST /api/circuit/lcapy` | Lcapy のネットリスト（DD-0028）。`lcapy.Circuit(text)` で読み込める。`fixed=true` なら接点を今の開閉（閉は導線 W・開は開放 O）に固定し、`false` なら接点をスイッチ（SW no / nc）で出す |
| `circuit_skidl.py` | `POST /api/circuit/skidl` | SKiDL のスクリプト（DD-0028）。実行すると KiCad のネットリスト `circuit.net` を書き出す |

ノード名は共通で、`P` / `P_xxx` が電源の正側（PER 母線）、`0` が負側（NER 母線）、`N001`… が配線でつながった端子の集まり。
リレーの動作（コイルの励磁で接点が切り替わる）は Lcapy・SKiDL では表せないため、もしも・タイマーなどのシミュレーションは
アプリの回路モデル（`services/circuit_model.py`）で行う。

### 7.1 機器索引（`device_index.xlsx`。DD-0044）

`GET /api/circuit/devices.xlsx?project_ids=1,2`（JSON は `POST /api/circuit/devices`）。位置は図面を 3×3 に分けた目安
（左上・中上・右上・左中・中央・右中・左下・中下・右下）。JSON では図面の幅・高さに対する割合（`cx`・`cy`・`w`・`h`）も返す。

| シート | 列・内容 |
|---|---|
| 機器一覧 | 器具番号／種類（リレー・外部入力・負荷）／コイルのページ／コイルの位置／a 接点数／b 接点数／接点の場所／タイマー／要確認。リレー・外部入力・負荷の順 |
| 接点の場所 | 器具番号／種類／接点（a 接点・b 接点、瞬時）／ページ／位置／記号 ref／原文／枠のコイルシート（接点のラベルの括弧のシート番号）／コイルのページ／備考（AI 候補など）。器具番号の無い接点も「（器具番号なし）」で並べる |
| 電線番号 | 電線番号／ページ／つながる記号（ラベルと記号 ref）／位置／種類／ノード数／要確認。分岐点は除く |
| 要確認 | 対象／ページ／位置／内容 |

### 図面の変更の操作（DD-0034）

図面の変更は操作の並び（JSON、最大 200 個）で表し、全体で 1 つの変更として原子的に適用する。操作の種類と各操作の
JSON Schema は `GET /api/circuit/edit/tools` が返す（LLM のツール定義としてそのまま使える）。適用した変更は
`circuit_change_sets`（ER_DIAGRAM.md）に、操作の並び・変更前の図面・変更後の図面のハッシュとともに残り、取り消せる。

---

## 8. 正解のやり取り（DD-0035）

東電設計様と Elith の間で、図面の読み取りの正解をやり取りするための形式。

### 8.1 対応表（`annotation_table.xlsx` / `.csv`）

`GET /api/exchange/table?project_ids=1,2&drawing_set=<図面セットID>&format=xlsx`。xlsx は 2 枚のシート。

| シート | 列・内容 |
|---|---|
| 対応表 | 図面セットID／ページID／要素ID／種別／原文／参照・接続先ID／判定／根拠・備考。要素 ID は「ページ:記号 ref」、端子は「ページ:記号 ref/端子名」。行は記号 1 個、配線 1 本、行先札の組 1 組、コイルと接点の対応 1 組（コイルの無い接点は出どころ） |
| 代表回路 | 代表回路の正解（何を知りたいか・起点／出力・追う順番とページ・成立条件・成立する例・しない例）の記入例 |

Elith が書き出して送り、東電設計様が判定の列（接続／非接続／不明）や原文を直して返す。返ってきた表は
`POST /api/exchange/table/compare` で、関係（配線・別ページの継続・コイルと接点の対応）ごとの適合率・再現率と誤結線
（正解で非接続の組を機械が出した数）、原文の完全一致率で照合する。「不明」の組は数えない。

### 8.2 代表回路のシナリオ（YAML / JSON）

`POST /api/exchange/scenarios/evaluate` に、シナリオの並び（`scenarios`）か YAML / JSON の文字列（`text`）を渡す。
照合するのは、対象が見つかるか、成立条件が同じ論理か（違えば反例を返す）、例ごとの判定、順引きの影響先の適合率・再現率、
根拠のページが正解のページを含むか。器具の名前は、器具番号の全体か先頭の語（一意なとき）で回路の器具に当てる。

### 8.3 CVAT（`annotations.xml`）

CVAT for images 1.1 の XML。`GET /api/exchange/cvat?project_ids=1,2` で書き出し、画像は `/api/projects/{id}/image` から取る。

| 要素 | CVAT の形 | 属性 |
|---|---|---|
| 記号 | box | `ref`・`text` |
| ラベルの文字の位置（DD-0049） | box（label = `label_text`） | `text`。`group_id` で記号に結ぶ |
| 端子 | points | `group_id` で記号に結ぶ |
| 配線 | polyline | `from`・`to`・`wire_no`・`judge` |

取り込み（`POST /api/exchange/cvat/import`）は、配線の端点をいちばん近い端子（12 px 以内）に着け、`judge=非接続` の線
（単なる交差の例）は配線にしない。画像名の `p{図面 ID}_` か図面名・シート番号で図面に当て、大きさが違えば警告する。
`apply=false` で確認だけ、`true` で図面のアノテーションを置き換える。ページをまたぐ関係は CVAT に入れず、対応表で扱う。

---

## 9. ベクター PDF の線・文字（`vector_pdf/{図面 ID}.json`）

PDF で図面を登録すると、ページごとに PyMuPDF で線・黒丸（塗りつぶした小さい図形）・文字を取り出し、画像と同じ座標
（150 dpi。横書きの図面を回したなら同じだけ回す）で `DATA_DIR/vector_pdf/{図面 ID}.json` に保存する。
`GET /api/projects/{id}/vector-pdf` で返す。保存は図面の画像のハッシュに結び付け、ID を使い回した図面では古い内容を返さない。

| 項目 | 内容 |
|---|---|
| `kind` | `vector`（線・文字あり）、`scan`（画像だけの PDF。線・文字は空）、`mixed`（ページの大半を画像が覆う）、`empty` |
| `width` / `height` / `dpi` / `turn` | 画像の大きさ、解像度、登録時に回した角度 |
| `wires` | 配線の候補の線。細切れの同じ線はつなぐ |
| `frame` / `dashed` | 図枠（ページ幅・高さの 85% 以上、ページの端に沿う線）、破線（盤の枠・注釈線） |
| `dots` | 黒丸 |
| `texts` | 文字の行ごとの外接枠・向き（横・縦・回転）・大きさ |
| `nets` / `junctions` / `crossings` | 接続でまとめた線の集まり（電気的な接続の候補）、接続点、黒丸の無い十字の交わり（非接続） |

受け取る図面はスキャン PDF のため、通常は `kind = "scan"` になり、線・文字は画像から読む（構造解析）。
電気的な接続の正本はアノテーションで、この JSON は候補にとどまる。

---

## 10. 時刻の表し方

書き出しファイル（`exported_at`・`generated_at`）と API（`created_at`・`updated_at`・`started_at`・`finished_at`・`undone_at`）の
時刻は、日本時間の ISO 形式（例 `2026-11-20T17:10:05+09:00`）で出す（DD-0036）。DB には UTC で保存している。
オフセットの無い古い形式の値を読むときは UTC とみなす。

