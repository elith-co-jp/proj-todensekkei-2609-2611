# DD-0042: 内部 DB の読み込みの見直し（回路・対応表・図面セットの一括読み込み、文字の列、外部キーの索引）

- 状態: レビュー中
- 作成日: 2026-11-21
- 関連文書: [DD-0015](DD-0015-db-design.md)、[DD-0039](DD-0039-analysis-jobs.md)、[DD-0041](DD-0041-drawing-sets.md)、[ER_DIAGRAM.md](../ER_DIAGRAM.md)

## 1. 背景と測定

DD-0015 で図面 1 枚の読み書き（一覧・図面・保存・削除）は速くなった。その後、図面セット（DD-0041）で
回路・もしも・波及範囲・追跡・対応表を **何十枚もまとめて** 回すようになり、遅い所がそちらに移った。

図面 100 枚（1 枚にシンボル 150・端子 300・配線 200、構造解析 419 KB）、図面セット 30 枚で測った
（`scripts/bench_db.py --structure-file <構造解析の JSON>`、5 回の中央値、4 コア CPU）。

| 操作 | 時間 | 原因 |
| --- | --- | --- |
| 回路 `POST /circuit/build`（30 枚） | 553 ms | ① 図面ごとに構造解析の JSON（1 枚 400 KB）を全部開いて、使うのは `texts`（PER/NER の文字）だけ。30 回のクエリと 12 MB の JSON の解析。② 記号・端子・配線 約 2 万行を ORM の行として組み立てていた（読むだけなのに）。③ 図面ごとにクラスの表を読み直していた（30 回） |
| 対応表 `GET /exchange/table`（30 枚） | 737 ms | 同上（回路を組むため） |
| 図面セットの過不足 `GET /drawing-sets/{id}/coverage` | 297 ms | ②③ |
| 構造解析 `GET /ml/projects/{id}/structure` | 36 ms | 保存した JSON を開いてから、もう一度 JSON に書き直していた |
| 整合性チェック・変更の試行/適用 | — | 構造解析の JSON を図面ごとに 2 回（文字のためと本体のため）開いていた |

回路の API は「もしも」を試すたびに呼ばれるので、ここが一番効く。

ほかに、設計上の問題として次があった。

- ER 図に「外部キーの列にはすべて索引」とあるのに、`annotation_symbols.class_id`・`predictions.class_id`・
  `predictions.model_id`・`training_runs.result_model_id` に索引が無かった（クラスごとの集計、モデルを消したときの
  `SET NULL` が表全体の走査になる）。

## 2. 変更

**スキーマ（`models.py`）**

- `structure_predictions.texts_json`（TEXT、NULL 可）を足す。`payload_json` の `texts` の写し。
  `payload_json` を書くたびに `@validates` で写すので、どこから書いても食い違わない（`wire_count` と同じ方式、DD-0015）。
  文字が無ければ `"[]"`。**NULL は「まだ写していない」** の意味（列を足す前の行と、列を知らない古い版が書いた行）。
- 索引: `annotation_symbols (class_id)`、`predictions (class_id)`、`predictions (model_id)`、`training_runs (result_model_id)`。
  これで外部キーの列はすべて索引の先頭にある（`tests/test_db_design.py` で確かめる）。

**移行（`db_migrations.py`）**

1. 列の追加に `structure_predictions.texts_json TEXT` を足す。
2. 新しい手順 `backfill_structure_texts`: `texts_json` が NULL の行に `payload_json` の `texts` を写す。
   1 枚ずつ読んで書く（全部の JSON を一度にメモリに載せない）。済んでいれば対象は 0 行で、起動のたびに流しても何もしない。
   古い版に戻して解析し直した行（構造解析は図面ごとに消してから入れ直すので、写しは NULL になる）も、次の起動で埋まる。
3. 索引は既存の手順（`create_indexes`）が足す。

**読み込み**

- `annotation_service.load_pages(db, project_ids)`（新規）: 読むだけの図面を、図面・記号・端子・配線の表ごとに 1 回の
  SELECT で列だけ読み、`serialize_project(with_image_meta=False)` と同じ dict を返す。dict の組み立ては
  `serialize_project` と共通の `_page_dict` で行うので、並びも中身も同じになる。
  回路（`circuit_service._pages`）、対応表・CVAT（`routers/exchange.py`）、図面セット（`drawing_sets._pages`）で使う。
- 回路の文字: `structure_service.get_structure_texts` が `texts_json` だけを 1 回のクエリで読む。
  構造解析が無い・文字が空の図面は従来どおりベクター PDF の印字に落とす（画像の sha256 も 1 回のクエリで読む）。
- 整合性チェック・変更（DD-0031・DD-0034）は構造解析の本体を `get_structures` で 1 回のクエリで読み、2 回開かない。
  `POST /ml/analysis/export` も同じ。
- `GET /ml/projects/{id}/structure`: 保存した JSON を開かずに、そのまま応答に埋め込む（中身は同じ）。
- `serialize_project` にクラスの対応表を渡せるようにし、書き出し（ZIP・CSV・ネットリスト・属性）で図面ごとに読み直さない。

**変えないもの**

- 時刻の保存（タイムゾーンなしの UTC）と表示（日本時間、DD-0036）。
- API の入出力。書き込みの経路（保存・解析・削除）。

**採用しなかったもの**

- SQLite の WAL: DD-0015 と同じ理由（EXE と DB を共有フォルダに置くと壊れうる）で入れない。裏の解析（DD-0039）は
  図面 1 枚ごとの短い書き込みで、読み込みも 1 回の SELECT ごとにしかロックを持たないので、`busy_timeout` で足りる
  （書き込み中の一覧の読み込みは 20 ms ほどで返ることを確かめた）。
- 構造解析の JSON の圧縮: DB は小さくなるが、DB を直接見て確かめられなくなり、移行も重い。図面 1000 枚で 400 MB ほどで、
  1 台の PC で困る大きさではない。必要になったら別に検討する。
- 構造解析の JSON を表に分けること（配線・記号・文字を行にする）: 画面・書き出しは JSON 全体を使い、回路が使うのは文字だけなので、
  文字の列で足りる。
- 解析のジョブ（DD-0039）を DB に残すこと: DD-0039 のとおり、プロセスの中だけで持つ。
- 保存（`PUT /annotations`）の 1 行ずつの INSERT のまとめ書き: 150 記号で 60 ms ほどで、遅くない。

## 3. 測定（`scripts/bench_db.py --projects 100 --structure-file <419 KB の構造解析>`、5 回の中央値）

| 操作 | 変更前 | 変更後 |
| --- | --- | --- |
| 回路 `POST /circuit/build`（30 枚） | 553 ms | **263 ms** |
| 対応表 `GET /exchange/table` json（30 枚） | 737 ms | **404 ms** |
| 図面セットの過不足（30 枚） | 297 ms | **162 ms** |
| 構造解析 `GET /ml/projects/{id}/structure` | 36 ms | **3.3 ms** |
| 回路の入力の読み込み（`_pages`、30 枚） | 334 ms | 174 ms |
| 一覧・図面 1 枚・保存・削除・集計 | — | 変わらない（誤差の範囲） |

図面セットの過不足と `_pages` は、同じ規模のデータ（図面 90 枚）で API と関数を直接測った値。

回路・対応表の残りの時間は、回路を組む計算と応答の JSON 化で、DB の外にある。
DB の大きさは文字の写しの分だけ増える（実際の文字は 1 枚 数十 KB 以下）。

## 4. 確認

- **出力が変わらないこと**: 合成回路（DD-0032）3 組・図面 8 枚に、構造解析あり・文字なし・構造解析なし（ベクター PDF に落ちる）
  の図面を混ぜ、回路・もしも・順序・タイマー・波及範囲・逆引き/順引き・整合性チェック・変更の試行・Lcapy・SKiDL・
  回路コード・対応表（json/csv）・CVAT・属性・構造解析の書き出し・図面セット・図面・一覧の 95 の応答を、変更前（PR #91 の先頭）と
  変更後で比べ、生成時刻を除いて同一だった。
- **既存の DB の移行**: 変更前のコードで作った DB を変更後のコードで開くと、`texts_json` が埋まり、索引 4 つが足され、
  `PRAGMA foreign_key_check` は空で、回路・もしも・タイマー・整合性チェックの応答は変更前と同じだった。
- **ロールバック**: 移行した DB を変更前のコードで開いても、同じ応答が返った（古いコードは新しい列と索引を使わないだけ）。
- テスト（`tests/test_db_design.py`）: 一括の読み込みが `serialize_project` と同じ dict を返す（参照名と id の並びが逆、
  属性、未知のクラス、端子の無い配線、存在しない id）。文字の列が JSON と一致し、NULL の行も読め、起動時の移行で埋まる。
  外部キーの列すべてに索引がある（変更前のコードではこの 3 つが失敗することを確かめた）。

## 5. ロールバック

本 DD の差分を revert する。足した列と索引は古いコードでも害が無いので、DB を戻す必要は無い。
古い版で解析し直した図面は `texts_json` が NULL になるが、本 DD の版に戻せば起動時に埋まる。
列と索引も消す場合（任意）:

```sql
DROP INDEX ix_annotation_symbols_class_id;
DROP INDEX ix_predictions_class_id;
DROP INDEX ix_predictions_model_id;
DROP INDEX ix_training_runs_result_model_id;
ALTER TABLE structure_predictions DROP COLUMN texts_json;  -- SQLite 3.35 以降
```
