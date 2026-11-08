# DD-0012: 解析の高速化・処理の分割と、CI の軽量化

- 状態: レビュー中
- 作成日: 2026-11-08
- 関連文書: [DD-0011-wiring-package.md](DD-0011-wiring-package.md), [SPECIFICATION.md](../SPECIFICATION.md)

## 1. 背景と測定

DGX で 1 図面の解析（アプリと同じ `runtime.analyze_detections`）の段ごとの時間を測った（ER21315、合計 39 秒）。

| 段 | 時間 |
| --- | --- |
| 文字の読み取り（ページ全体を 2 倍にしたタイル 4 枚） | 19.1 秒 |
| 文字の読み取り（コネクタ・接点・コイルの枠ごとの 4 倍読み、84 個） | 15.5 秒 |
| 罫線の除外（文字かどうかの OCR を含む） | 3.1 秒 |
| 配線の検出 | 1.1 秒 |
| 端子・結び付け・端子間の接続・黒丸・雲マークなど | 0.5 秒 |

時間のほとんどは OCR で、配線の処理は 2 秒に満たない。ほかに、配線の検出の結果を一時フォルダへ JSON 6 つと TSV 1 つで書き出し、すぐ読み戻していた。本番は 1 通りしか使わないのに、OCR で文字を消す実験の分岐 10 通りとデバッグ画像の描画が本番のモジュール（`detect.py` 約 1,500 行）に入っていた。

CI は main への push ごとの Windows EXE のビルド 1 本（約 8.5 分）で、文書やテストだけの変更でもビルドと Drive への配布が走っていた。

## 2. 変更

**解析の高速化（読む文字は変えない）**

- `analysis/parallel.py`: 結果の順番を保ってスレッドで並べる `ordered_map`・`run_all`。並べる数は `SEQANNO_OCR_WORKERS`（既定は CPU 数と 4 の小さいほう、1 で並べない）。onnxruntime は推論中に GIL を手放す。
- `text_extraction`: ページのタイル（`page_tiles`・`read_tile`）と記号ごとの拡大読みを 1 つの作業の並びにして並べて読む。
- `filter_wire_decorations`: 罫線の候補を先に集め、文字の確認の OCR を並べる（確認した罫線の集合は順番によらない）。
- 評価（`eval_wiring_cv`）は図面をプロセスで並べるので、既定で 1 図面の中は並べない。

**処理の分割**

- `analysis/wiring/detect.py`: 本番の検出を `detect_wiring(image) -> WiringDetection` 1 つにした（約 880 行）。OCR の実験の分岐、EasyOCR、確認画像、ファイルへの書き出し（`analyze_image`）は `analysis/tools/wire_detection_lab.py` に移した。
- `analysis/wiring/assemble.py`: 構造 JSON は `assemble_structure` 1 つで組み立てる。本番はメモリ上の検出結果から（`structure_from_detection`）、CLI はページのフォルダから（`wire_detection_lab.build_final_output_from_analysis`）同じ関数を呼ぶ。一時フォルダを使わなくなったので `analyze_detections` の `work_dir` 引数をなくした。
- `analysis/runtime.py`: `analyze_detections` を段の関数（記号の作成、文字の読み取り、端子の結び付け、仕上げ）に分け、段の順を docstring に書いた。元の画像の二値化は雲マークと円形の記号の確認で 1 回にした。
- `analysis/wiring/geometry.py`: 線分・枠の計算（射影・重なり・範囲）を `connectivity.py` から移した。2 つあった `point_distance`（`math.hypot` と平方根）は `math.hypot` に統一した（30 枚の出力が同じことを確かめた。DD-0011 §4 の残課題）。
- `connectivity.py`: グラフのたどり（`pairs_reached_through_graph`）と記号の中の組（`pairs_inside_symbols`）を引数だけで動く関数にした。
- 使われていない実験スクリプト 2 本（`experiment_solid_wire_detection.py`・`build_connection_review_outputs.py`）を消し、リポジトリ全体の ruff の指摘をなくした。`pytest` の既定の対象に `analysis/tests` を加えた（`make test` で 260 件）。

**CI の軽量化（`.github/workflows/build-windows-exe.yml`）**

- EXE に入らない変更（`docs`、`*.md`、`tests`、`analysis/tests`、`analysis/tools`、フロントエンドのテスト）では main への push でもビルドしない。
- 同じブランチで新しいビルドが始まったら古いビルドを止める（`concurrency`）。
- 依存を uv で入れる（版は同じ）。Drive への配布のライブラリも同じ段で入れる。
- PyMuPDF のレイアウト解析のモデルと C の開発用ファイル、OpenCV の動画入出力（ffmpeg）を EXE に詰めない（`seq-annotator.spec`）。アプリは PDF をページの画像にするだけで、画像しか扱わない。
- 同梱ファイルの大きさをパッケージごとにログへ出す（`scripts/report_bundle.py`）。polars（168 MB）は ultralytics が学習の結果を保存するときに使うので残す。

## 3. 確認

| 確認内容 | 方法 | 結果 |
| --- | --- | --- |
| 出力が変わらない | 30 枚の構造解析の出力（JSON 全体）を main と突き合わせ（分割の後と、geometry の統一の後の 2 回）。評価の値も比べる | 30 枚とも一致。コイル・配線・以前の正解の評価の値も同じ |
| 並べて読んでも読む文字が同じ | OCR のキャッシュを使わず 3 図面を main と並べた版で解析し、出力のファイルを比べる。スレッド数を変えた組み合わせも比べる | 3 図面ともバイト単位で一致。スレッド数を変えても同じ |
| 解析の時間（DGX、1 図面） | main・並べない・4 つ並べる を同じ条件で順に測る | ER21315 38.7 → 33.2 秒、ER21256 17.3 → 16.6 秒、ER21297 19.7 → 20.4 秒。並べないときは main と同じ（整理で遅くなっていない） |
| CI | ブランチで公開なしのビルドを実行 | 依存の導入 93 → 38 秒、同梱 963 → 884 MB、ビルド全体 7 分 1 秒 → 6 分 31 秒。凍結した EXE の学習・推論の確認も成功 |
| テスト | `pytest`（`tests` と `analysis/tests`）、`ruff check .` | 260 件成功、指摘なし |

並べる効果は、文字の多い図面ほど大きい。DGX の onnxruntime は 1 回の読み取りで 20 コアを使うため、並べても CPU の取り合いになり、OCR だけで 22% ほどの短縮にとどまる。コアの少ない PC では値が違う。

## 4. 残課題

- OCR の量を減らすこと。記号ごとの拡大読みは、RapidOCR が短い辺を 736 px まで拡大して文字の位置を探すため、小さな枠ほど無駄が大きい。設定を変えると読む文字が変わるので、ラベルの正解率を評価してから決める。
- `connectivity.build_predicted_graph_pairs` の橋渡しの規則（約 450 行）は、グラフ・停留点・統計を共有する入れ子の関数のまま。分けるならグラフの状態をクラスにまとめる。
- EXE の作成（約 4 分）は PyInstaller の解析と圧縮で、同梱を減らす以外の短縮は難しい。

## 5. ロールバック

本 DD の差分を revert する。保存データ・API・構造 JSON の形は変えていない。並べて読むことだけを止めるときは `SEQANNO_OCR_WORKERS=1`。

