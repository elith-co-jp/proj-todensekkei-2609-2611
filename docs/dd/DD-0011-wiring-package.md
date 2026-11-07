# DD-0011: 配線解析のコードを責務ごとのパッケージに分け、調整値を 1 か所にまとめる

- 状態: レビュー中
- 作成日: 2026-11-08
- 関連文書: [DD-0007-accuracy-improvement-real-drawings.md](DD-0007-accuracy-improvement-real-drawings.md), [DD-0009-circuit-simulation.md](DD-0009-circuit-simulation.md), [SPECIFICATION.md](../SPECIFICATION.md)

## 1. 背景

アプリの解析（`analysis/runtime.py`）は、`analysis/tools` の評価・実験用のスクリプトを sys.path に足して読み込んでいた。読み込んでいた 12 本・約 1 万 2 千行のうち、本番で使う定義は約 4,600 行だけで、残りは学習データの作成、OCR の評価、レビュー画像の作成、実験の設定表だった。どこが本番のロジックかが分からず、変更のたびに影響を確かめる手間が大きかった。

しきい値（150 dpi の画素数）は 6 つのモジュールの定数と、runtime の関数呼び出しに直接書いた数値（10・28・24 など）に分かれていた。記号の種類の集合（通り抜けの点・接点・コイルなど）は、同じ内容が各所で別の名前で定義されていた。

## 2. 構成（`analysis/wiring`）

| モジュール | 責務 |
| --- | --- |
| `classes.py` | 記号の種類の役割（通り抜けの点、接点、コイル、2 端子の機器）。種類を足すときはここだけを直す |
| `params.py` | 調整値。段ごとの dataclass（`GraphParams`・`PortParams`・`LinkParams`・`BridgeParams`）に値・単位・根拠を書く。環境変数（`SEQANNO_LINE_PORTS`・`SEQANNO_FAINT_LINES`）は `WiringParams.from_env` で読む |
| `shapes.py` | 共通の型（枠・線分・検出した配線とラベル）と、線の軸・交点 |
| `geometry.py` | 点・枠・線分の基本計算 |
| `detect.py` | 画像から配線（線分）・文字領域・ラベルを検出する |
| `graph.py` | 配線から交点・端点のノードと辺のグラフを作る |
| `assemble.py` | 検出結果を構造 JSON の形にまとめる |
| `ports.py` | 記号の端子の位置を決める（配線との交わり、画像の線、薄い点線） |
| `linking.py` | 端子を配線・ノードに結ぶ |
| `connectivity.py` | 端子から配線グラフをたどり、端子間の接続（from-to）を求める。線の途切れの橋渡しを含む |
| `ocr.py` | ラベルの文字認識の読み込み |
| `dot_roles.py` `faint_lines.py` `filter_wire_decorations.py` `revision_clouds.py` `terminal_numbers.py` | 黒丸の役割、薄い線、罫線の除外、雲マーク、端子番号（`analysis/tools` からそのまま移した） |

`analysis/runtime.py` はこれらを順に呼ぶだけにし、sys.path の書き換えをやめた。各段の関数は調整値を引数（既定値 `params.DEFAULT`）で受け取る（`runtime.analyze_detections(..., params=)`、`build_connection_graph(..., params=)`、`line_ports(..., params=)`、`build_direct_from_to(..., params=)`）。値を変えて試すときは `dataclasses.replace` で別の値を作って渡し、モジュールの定数は書き換えない。

`analysis/tools` の評価・実験のスクリプトは、本番のコードを `analysis.wiring` から読む側になった（`_repo_path.py` でリポジトリの直下を import できるようにする）。本番は `analysis/tools` を読まないので、Windows 版 EXE の検索パスから外した。

## 3. 移し方と確認

定義の移動は、runtime から呼び出しをたどって本番で使う定義を洗い出し、元の文字列のまま移すツールで行った（手で写さない）。名前が同じで中身の違う `point_distance`（`math.hypot` と平方根）は、それぞれ使う側に残した。中身が同じ `merged_span_length` は 1 つにした。

- 30 枚の正解データの構造解析の出力（配線・ノード・端子・端子間の接続・`resolver` の値を含む JSON）を整理の前後で突き合わせ、30 枚とも一致した。取り込み評価・シミュレーション比較の値も同じ。
- Python のテスト全件、ruff。

## 4. 残課題

- `detect.py`（約 1,500 行）は、線の検出・文字領域・OCR の結果の整理が 1 つのモジュールに入っている。次に分けるならここ。
- 端子の位置の推定の設定は、実験の設定表（`ports.default_configs`）から名前で選ぶ形のまま（`runtime.PORT_INFERENCE_CONFIG`）。`PortParams` へ移すと設定の置き場が 1 つになる。
- `point_distance` の 2 つの実装の統一は、出力が変わり得るので評価で確かめてから行う。

## 5. ロールバック

本 DD の差分を revert する。保存データ・API・構造 JSON の形は変えていない。

