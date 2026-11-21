# DD-0040: EXE を少し軽くし、起動の速いフォルダ形式も選べるようにする

- 状態: レビュー中
- 作成日: 2026-11-21
- 関連文書: [DD-0012](DD-0012-speed-and-ci.md)

## 1. 目的

運用はまず各 PC の EXE（2026-11-21 の確認）。1 ファイルの `TodenYOLO.exe` は起動のたびに同梱物（約 900 MB）を
一時フォルダへ展開するため、ダブルクリックから画面が開くまで 10 秒以上かかる。起動を速くし、同梱物を減らせるか確かめる。

## 2. 変えたこと

- `seq-annotator.spec`:
  - EXE で使わないものを外した: `psycopg`（Docker の PostgreSQL 用。EXE は SQLite だけ）、Pillow の AVIF。
  - `SEQANNO_ONEDIR=1` でフォルダ形式（`dist/TodenYOLO/TodenYOLO.exe` と `_internal/`）を作る。既定は従来の 1 ファイル。
    `runtime.py` はもともとフォルダ形式にも対応している（同梱物は `_MEIPASS`、`data/` は EXE と同じフォルダ）。
- `.github/workflows/build-windows-exe.yml`: 手動実行の入力 `layout`（onefile / onedir、既定 onefile）。main への push は従来どおり 1 ファイル。
  onedir のときは、フォルダの大きさを記録し、リポジトリから同梱したファイル（spec の datas）に図面・DB・文書の型が無いことを
  PyInstaller の TOC で確かめ（`scripts/check_bundle_sources.py`。matplotlib の back.pdf などパッケージのファイルは名前では
  見分けられないため対象外）、凍結 EXE の学習・推論を確かめてから `TodenYOLO-folder.zip` にして配る。
- `scripts/smoke_packaged_ml.py`: 起動時間（1 回目と 2 回目）をログに出す。

## 3. 結果（GitHub の Windows ランナー、2026-11-21）

| 形式 | 同梱物 | 配る物 | 起動（1 回目 / 2 回目） |
|---|---|---|---|
| 1 ファイル（変更前） | 928.8 MB | EXE 1 つ | — |
| 1 ファイル（本 DD） | 913.1 MB | EXE 1 つ | 13.1 秒 / 10.5 秒 |
| フォルダ形式 | 871.1 MB（3,888 ファイル） | ZIP 371.6 MB | 2.0 秒 / 1.5 秒 |

起動時間は、EXE を起動してから `/api/health` が応答するまで（`smoke_packaged_ml.py`）。

- 除外で減ったのは 16 MB ほどで、大きいのは torch（273 MB）・polars（168 MB）・OpenCV（91 MB）・同梱モデル（75 MB）。
  polars は ultralytics の学習が results.csv を読むのに使うため外せない。torch を外すには学習を EXE から切り離す必要がある。
- フォルダ形式は起動が 5〜7 倍速い。代わりに配る物が ZIP になり、利用者は展開したフォルダごと置く（EXE だけを動かすと起動しない）。

## 4. 残り

- 配布は 1 ファイルの EXE のまま（2026-11-21、勝山さんの判断）。フォルダ形式は手動実行の `layout=onedir` で必要なときだけ作る。
- 推論だけの PC 向けに torch を外した軽い版を作るかは、学習を別の PC で行う運用にするかと合わせて決める。
