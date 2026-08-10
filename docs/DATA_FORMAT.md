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
            { "ref": "SYM-0001-T1", "name": "13", "tx": 0.3824, "ty": 0.5411 }
          ]
        }
      ],
      "connections": [
        {
          "from_symbol_ref": "SYM-0001", "from_terminal_ref": "SYM-0001-T1",
          "to_symbol_ref": "SYM-0002", "to_terminal_ref": null,
          "wire_no": "610", "net_id": null,
          "kind": "wire", "external_ref": null, "note": null
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

### セーフガード

| 対策 | 上限 |
|------|------|
| パストラバーサル（絶対パス・`..`） | 拒否 |
| `__MACOSX` / `.DS_Store` / `._*` | 無視 |
| ファイル数 | 20,000 |
| 展開後の合計サイズ | 2 GiB |
| 圧縮率（10 MB 超のとき） | 25 倍まで |
| 画像 1 枚あたり | 64 MiB |
