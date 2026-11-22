# リレーコイルと表示灯の取り違え（2026-11-22、DD-0043）

表示灯を含むスキャン風の評価用図面 40 枚（`analysis/synthetic_circuits/circles.py`、種 39）を本番の解析経路（`analysis.runtime.analyze_detections`、RapidOCR あり）で解析した。円形の記号 195 個（リレーコイル 99・表示灯 96）。

再現: `python analysis/tools/eval_suite.py --only circles --enable-ocr`（変更前の値は同じ図面を main 2943239 のコードで解析）。

## 場面ごと

| 場面 | 版 | 検出の取り違え | 判別後の取り違え | うち誤って確定 | 直した | 正しい検出を書き換えた | 要確認 | 期待どおりの判定 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| correct | 変更前（main） | 0 | 0 | 0 | 0 | 0 | 92 | 181/195 |
| correct | 今回 | 0 | 0 | 0 | 0 | 0 | 82 | 191/195 |
| swapped | 変更前（main） | 195 | 96 | 0 | 99 | 0 | 96 | 177/195 |
| swapped | 今回 | 195 | 82 | 0 | 113 | 0 | 82 | 191/195 |

## 書き方ごと（swapped: リレーコイル↔表示灯をすべて取り違えた検出）

| 書き方 | 個数 | 期待 | 判別後の取り違え 変更前 → 今回 | 要確認 変更前 → 今回 |
|---|---:|---|---:|---:|
| relay_table_contact | 24 | 確定 | 11 → 3 | 11 → 3 |
| relay_table | 26 | 確定 | 6 → 0 | 6 → 0 |
| relay_contact | 29 | 要確認 | 29 → 29 | 29 → 29 |
| relay_bare | 20 | 要確認 | 20 → 20 | 20 → 20 |
| lamp_x_color | 25 | 確定 | 0 → 0 | 0 → 0 |
| lamp_x | 30 | 確定 | 0 → 0 | 0 → 0 |
| lamp_color_twice | 12 | 確定 | 1 → 1 | 1 → 1 |
| lamp_color_inside | 29 | 要確認 | 29 → 29 | 29 → 29 |

## 書き方ごと（correct: 検出が正しい）

| 書き方 | 個数 | 期待 | 確定 変更前 → 今回 | 要確認 変更前 → 今回 |
|---|---:|---|---:|---:|
| relay_table_contact | 24 | 確定 | 15 → 21 | 9 → 3 |
| relay_table | 26 | 確定 | 22 → 26 | 4 → 0 |
| relay_contact | 29 | 要確認 | 0 → 0 | 29 → 29 |
| relay_bare | 20 | 要確認 | 0 → 0 | 20 → 20 |
| lamp_x_color | 25 | 確定 | 25 → 25 | 0 → 0 |
| lamp_x | 30 | 確定 | 30 → 30 | 0 → 0 |
| lamp_color_twice | 12 | 確定 | 11 → 11 | 1 → 1 |
| lamp_color_inside | 29 | 要確認 | 0 → 0 | 29 → 29 |

文字の手がかりを使わない場合（OCR なし、CI で毎回回す）: `full` 40 枚で swapped の判別後の取り違え 140、誤って確定 0、correct の書き換え 0（×印の手がかりだけで表示灯 55 個を直す）。基準値は `analysis/eval_baseline.json`。
