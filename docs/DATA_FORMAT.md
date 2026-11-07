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
  "exported_at": "2026-08-04T22:51:38+00:00",
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

参照先の図面は、図面情報のシート番号（未入力ならシート番号の候補の 1 位）で照合する。枝番つきの参照（`ER21212-1`）は枝番なしの図面にも合わせる。

