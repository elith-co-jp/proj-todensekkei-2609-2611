# DD-0015: DB 設計の見直しと読み書きの高速化

- 状態: レビュー中
- 作成日: 2026-11-08
- 関連文書: [ER_DIAGRAM.md](../ER_DIAGRAM.md), [DD-0010-object-attributes.md](DD-0010-object-attributes.md), [DD-0012-speed-and-ci.md](DD-0012-speed-and-ci.md)

## 1. 背景と測定

図面 100 枚（1 枚にシンボル 150・端子 300・配線 200、実際の構造解析の結果 1 枚 402 KB）の SQLite で API ごとの時間を測った（`scripts/bench_db.py`、3 回の中央値）。

| 操作 | 時間 | 原因 |
| --- | --- | --- |
| 図面の一覧 `GET /projects` | 170〜178 ms | 構造解析の JSON（1 枚 400 KB）を全図面ぶん開いて、配線の本数と接続の数の 2 つを数えていた。図面の数に比例して遅くなる |
| 図面 1 枚 `GET /projects/{id}` | 63 ms | 記号ごとに端子を、配線ごとに端子を 1 件ずつ遅延読み込みしていた（1 枚で数百回のクエリ）。端子の参照名を出すたびに記号の端子を並べ直していた |
| 図面の削除 | 63〜68 ms | SQLite の外部キーが無効で `ON DELETE CASCADE` が効かず、ORM が子の行を全部読んでから 1 行ずつ消していた |
| 上書き保存 `PUT /projects/{id}/annotations` | 97〜104 ms | 古い記号を 1 件ずつ読んで消し、新しい記号と端子を 1 行ごとに書き込んでいた（flush） |

ほかに、設計上の問題として次があった。

- SQLite で外部キーが無効のため、親を消しても子が残りうる（DB の制約が守られていなかった）。SQLite の id は AUTOINCREMENT ではないので、いちばん新しい図面を消すと次の図面が同じ id になり、残った子の行が新しい図面に付いてしまう（テストの作成中に、外部キーを無効にした接続で実際に起きた）。
- 外部キーの列（`connections.project_id` 以外の記号・端子の列、`symbol_terminals.symbol_id`、`predictions.project_id`、`annotation_images.project_id`）に索引が無い。
- 配線の参照名（W-0001）は属性の JSON と 1 対 1 で対応する（DD-0010）のに、DB 上で一意になっていない。
- すべての主キーに、主キーと同じ索引（`ix_<表>_id`）が重ねて作られていた。
- 列追加の移行が `database.py` に if 文の連続で書かれていた。
- 上書き保存しても図面の更新日時が変わらなかった。
- ER 図に `structure_predictions` と配線の参照名・属性の列が無かった。

## 2. 変更

**スキーマ（`models.py`）**

- `structure_predictions` に `wire_count`・`connection_count` を足す。`payload_json` を書くたびに数え直す（`@validates`）ので、どこから書いても JSON と食い違わない。
- 索引: `connections (from_symbol_id)`・`(to_symbol_id)`・`(from_terminal_id)`・`(to_terminal_id)`、`symbol_terminals (symbol_id)`、`predictions (project_id, kind)`、`annotation_images (project_id)`。
- 一意の索引 `connections (project_id, ref)`。
- 主キーの `index=True` を外す（重ねた索引を作らない）。
- relationship を `passive_deletes=True` にし、削除の連鎖を DB に任せる。端子は id 順に読む（`order_by`）。

**接続（`database.py`）**: SQLite の接続ごとに `PRAGMA foreign_keys=ON` と `busy_timeout=5000` を入れる。MySQL などでは何もしない。

**移行（`db_migrations.py`、新規）**: 起動のたびに次の手順を順に実行する。どれも済んでいれば何もしない。

1. 列の追加（これまでの列追加を表にまとめ、件数の 2 列を足した）
2. 件数の列の埋め戻し（列を足す前の構造解析の結果から数える）
3. 親の無い行の片付け（外部キーが無効だった頃に残った端子・配線・推論結果など）
4. 配線の参照名の振り直し（欠けと重複に W-0001 形式の空き番号。重複していない名前は変えない）
5. 索引の作成
6. 主キーに重ねた索引の削除

**読み書き**

- `serialize_project` を使うクエリに `WITH_ANNOTATIONS`（記号→端子、配線、画像を表ごとに 1 回で読む）を付けた（図面 1 枚、一覧の書き出し、回路、属性）。端子の参照名は 1 回だけ対応表を作る。
- 上書き保存: 古い記号と配線は条件付きの DELETE 2 回で消し（端子と配線は DB が連鎖して消す）、新しい行は relationship でつないで最後に 1 回で書き込む。図面の更新日時を更新する。エクスポートの取り込みも 1 行ごとの書き込みをやめた。
- 一覧は件数の列を読む。集計 `/stats` の状態ごとの件数と、推論結果の図面ごとの件数は GROUP BY で数える。

**採用しなかったもの**

- SQLite の WAL: 読み書きの同時実行には効くが、exe と DB をネットワークの共有フォルダに置くと WAL は使えない（壊れうる）。配布の形（exe と同じ場所に DB）を変えずに済むよう、ロックの待ち時間（`busy_timeout`）だけ入れた。
- `training_runs.project_ids_json` の関連の表への分割: 学習に使った図面の「その時点の一覧」で、図面を後で消しても履歴は変えない方がよい。
- `annotation_projects.image_width/height` と `annotation_images.width/height` の重複: 一覧で画像の表を読まずに済むための意図した重複として残す。
- Alembic の導入: 配布する exe の起動時に移行を流す必要があり、手順の数も少ないので、手順の並び（`STEPS`）で十分。

## 3. 測定（`scripts/bench_db.py --structure-file <実際の構造解析の結果>`、3 回の中央値）

| 操作 | 変更前 | 変更後 |
| --- | --- | --- |
| 図面の一覧 | 170〜178 ms | **9.7〜10.2 ms** |
| 図面 1 枚 | 63〜64 ms | **7.3〜7.4 ms** |
| 図面の削除 | 63〜68 ms | **2.7 ms** |
| 上書き保存 | 97〜104 ms | **22.6〜24.2 ms** |
| 新規の保存 | 38〜39 ms | 25〜27 ms |
| 集計 | 3.8〜8.6 ms | 3.4〜3.5 ms |

一覧は図面の数に比例していた部分（JSON の解析）が無くなったので、図面が増えるほど差が広がる。

## 4. 移行の確認

変更前のコードで作った DB（図面 5 枚）に、古い DB で起こりうる状態（配線の参照名の欠けと重複、親の無い端子と配線）を入れてから、変更後のコードで起動した。件数の列が埋まり、参照名は欠けも重複もなくなり、親の無い行は消え、索引がそろい（主キーに重ねた 11 個は消えた）、`PRAGMA foreign_key_check` は空だった。一覧・図面 1 枚・削除が動き、削除後に子の行は残らなかった。同じ確認を `tests/test_db_design.py` に入れた。外部キーを無効に戻すと、削除と上書き保存のテストが失敗することを確かめた。

## 5. ロールバック

本 DD の差分を revert する。移行で足した列・索引は古いコードでも害が無い（古いコードは使わないだけ）ので、DB を戻す必要は無い。古いコードは外部キーを有効にしないので、削除は ORM の連鎖に戻る。振り直した配線の参照名はそのまま使える。

