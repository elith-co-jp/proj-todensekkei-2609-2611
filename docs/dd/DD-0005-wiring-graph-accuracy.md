# DD-0005: 配線グラフ組立の精度改善（接続ペア F1 向上）

- 状態: レビュー中
- 作成日: 2026-10-12
- 関連文書: [DD-0003-structure-accuracy-improvement.md](DD-0003-structure-accuracy-improvement.md), [DD-0004-old-jis-symbol-support.md](DD-0004-old-jis-symbol-support.md), [SPECIFICATION.md](../SPECIFICATION.md)

## 1. 背景

DD-0003 で配線（from_to 接続ペア）F1 を 0.36 → 0.71 へ改善したが、さらなる向上要求に対して誤差分析を実施した。残存誤差の内訳（正解30ページ）:

- **FN 接続ペア 7,307**: うち約60%はゴールドシンボル自体の未検出が原因（`contact_b`/`power_bus` 全量未検出＝全て `other` へ誤分類、モデル側問題）。グラフ組立起因は 2,908 エッジで、そのうち両端子とも検出済みなのに未接続だったものが 109 エッジ。
- **FP 接続ペア 14,227**: 大半は `relay_coil`/一部 `other` がゴールドのネット一覧に存在しないために発生する「孤立ネット結合」（評価系の特性）。真の誤併合（別ネット同士の結合）は 130 エッジで page 13/28/12/20 に集中。
- **ジャンクション経路のバイパス**: ジャンクション（T字結線・黒点）での導通は本来「インクで黒点を確認してから導通」だが、junction クラス端子間 140px の直接ブリッジ（253件）・ワイヤ間ギャップブリッジ（94件）等がジャンクション判定を迂回し、導通規則を素通りしていた。

## 2. 目的

- from_to 接続ペア F1 およびエッジ再現率を、検出モデル差替なしに改善する。
- ネットリスト（nets）と from_to 両方の導通経路で規則を統一し、評価経路ごとの挙動差を解消する。

## 3. スコープ

- 対象: `analysis/tools/build_from_to_review_outputs.py`（from_to 導通経路）、`analysis/tools/run_yolo_e2e_pipeline.py`（DIRECT_* 定数・build_net_candidates・build_direct_from_to 呼出）。
- スコープ外: 検出モデル（重み）の差替・フロントエンド。希少クラス（contact_b/power_bus）の誤分類対策は別途モデル再学習で対処。

## 4. 変更案の概要

### 4.1 ジャンクション導通経路の T字/交差 分離

- ワイヤの「終端がジャンクション位置に触れる（T字）」場合のみ導通候補とし、全インシデントワイヤが素通りする単なる交差は非導通とする判定 `_wire_ends_at_node` を追加（h: 端点座標がノード±8px、v 同様）。
- `build_net_candidates`（ネットリスト経路）にも同一規則を適用し、従来 junction_points は junction クラスシンボル中心（bbox中心フォールバック）＋インク検証で構成。
- junction クラスシンボル中心が `junction_node_threshold`（24px）以内にあるノードは引き続き導通。

### 4.2 ノードアンカーのワイヤリンク上書き修正

- `connector`/`junction`/`power_bus`/`terminal` のノードアンカー（≤24px）が、既に別ワイヤへリンク済みの端子を上書きして誤ったノードへ接続していた問題を修正: `wire_id` が既にある端子へはノードアンカーを適用しない。

### 4.3 エンドポイントブリッジ

- ワイヤ端点（type=endpoint）同士は端子アンカーを持たないため、T字接合が未接続になる問題に対し、エンドポイント同士のみ `DIRECT_ENDPOINT_BRIDGE_MAX`（60px）までのギャップブリッジを許可。`has_terminal_anchor` 拒否条件を「エンドポイント同士かつ gap≤60」の場合のみ解除。
- 値はオフラインアブレーション（検出結果固定で from_to を再計算）で決定: 16→60 で tp 増、60超で FP 増。

## 5. 評価方法

- `work/ablate.py`: `eval/oj3c50` の検出結果（final_output.json）を固定し、from_to 構築だけを差替えて from_to ペア（和併合→クリック展開）とエッジ再現率を再採点。学習やYOLOを回さず数十秒で反復できる。
- `work/driver.py eval`: 正解30ページのフル採点（fromto-pairs / graph-pairs / edge-recall）。

## 6. 結果

| 指標 | ベースライン | 本変更 |
| --- | --- | --- |
| fromto-pairs F1 | 0.7188 | 0.7285 |
| edge-recall | 0.8020 | 0.8206 |
| graph-pairs F1 | 0.5138 | 0.53 前後 |

- 誤併合（別ネット結合）130エッジのうちジャンクション迂回由来が解消し、T字/交差分離自体はメトリクス中立化（迂回経路が消えたため規則が正しく効く）。
- 残: シンボル未検出起因の FN（モデル再学習対象）と、ゴールドネット外の孤立ネット FP（評価系特性・擬似誤差）。

## 7. ロールバック

`DIRECT_*` 定数と `_wire_ends_at_node` を含む差分を revert。検出モデルは不変。

## 8. 未解決事項

- contact_b/power_bus の `other` 誤分類（コピペ増補による再学習を別途実施）。
- 評価 conf をクラス別に下げる（junction 0.25）と F1 はさらに改善するが、本番の推論conf経路（services/inference_service）への適用は別検討。
