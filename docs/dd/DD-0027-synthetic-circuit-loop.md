# DD-0027: コードから回路図・PDF・正解データを同時生成し、解析のずれを反復改善する

- 状態: 採用
- 作成日: 2026-11-12
- 関連文書: [DD-0016-missed-connections.md](DD-0016-missed-connections.md), [DD-0020-wiring-precision-99.md](DD-0020-wiring-precision-99.md), [DD-0024-detection-recall-trials.md](DD-0024-detection-recall-trials.md), [合成回路生成方式の調査](../research/synthetic-circuit-generation.md)

## 1. 目的と採用境界

図面を画像だけでなく、記号、端子、接続、配置を持つ回路コードとして再現する。1つの正本モデルから図面画像、PDF、YOLOラベル、エディタへ再取込できるZIP、SPICE互換ネットリストを同時生成し、PDFを本番の構造解析経路へ戻した結果を正本の接続グラフと自動照合する。

合成データの評価は解析規則の高速な回帰検証に使う。固定30実図面の交差検証は独立した採用ゲートとして残し、合成データだけで実運用精度を保証しない。

## 2. LTspiceを中核にしない理由

LTspiceはWindows/macOS向けで、公式に文書化されたCLIには回路図PDFのheadless出力がなく、`.asc`・`.asy`の直接生成形式も公開仕様ではない。大量生成の正本にすると、Linux上の再現性と自動検証が不安定になる。このため本実装はリポジトリの正規化回路モデルを正本にし、LTspice/ngspiceで読める`.cir`を副出力する。選定根拠と将来のSchemdraw、CircuiTikZ、KiCad adapter案は調査文書に記録した。

## 3. データフロー

```text
seed + generation profile
          |
          v
canonical project JSON
  symbols / terminals / connections / layout
          |
          +--> PNG + lossless PDF
          +--> YOLO labels + data.yaml
          +--> importable synthetic_bundle.zip
          +--> LTspice/ngspice compatible .cir
          |
          v
PDF rasterization --> production detection logic (explicit config) / OCR / wiring analysis
                                      |
                                      v
                         reconstructed circuit project
                         + predicted SPICE netlist
                                      |
canonical symbols / terminals / edges ---------------+--> evaluation.json
```

正解は解析画像から逆算せず、描画に使った同じ記号・端子・接続座標から出す。seed、schema version、各成果物のSHA-256をmanifestへ残す。同じ設定とseedではJSON、PNG、PDF、ZIPを同一byte列で再生成できる。

## 4. 実装

- `analysis.synthetic_circuits.DatasetConfig`: 件数、seed、ページ寸法、DPI、段数を検証する設定。
- `generate_dataset(output, config)`: 正本と全adapterを決定的に生成する。
- `evaluate_dataset(root)`: 各PDFを画像化し、本番の端子・配線・接続解析へ通す。既定は正本の記号枠をoracleとして配線解析を厳密に測る。`model_path`指定時は、本番と同じ低confidence分岐点選別を含むmain YOLO検出ロジックへ、評価レポートに記録した明示confidence（既定0.5）を渡す。connector専用閾値は本番API既定と同じく未指定とする。明示的に`--disable-ocr`を付けない限りOCRを要求するが、実際に動いたOCR engineと`completed/unavailable`は`ocr_runtime`へ別記する。合成器はカードを生成しないためcard modelは対象外である。
- `analysis/tools/synthetic_circuit_dataset.py`: `generate`、`evaluate`、`run`のCLI。
- `analysis/wiring/ports.py`: 接点とコイルだけでなく、表示灯、ソレノイド、押しボタン、ダイオードを含む二端子部品へ端子推定を適用する。
- `analysis/wiring/faint_lines.py`: 電気的に同じnetであることと、図面上に直接線分があることを分離する。長距離回復は、厳密に整列した分岐黒丸間の連続実線だけに限定する。

初期生成族は、上下の母線、3〜6本の縦段、単接点ラダー、2接点の直列インターロック、a/b接点、リレーコイル、表示灯、ソレノイド、分岐黒丸を含む。a/b接点は実図面と同じく、端子丸の右側／左側にある縦棒で種類を表す。描画はclean、薄い母線、1px母線、濃淡混在の4 profileをseedで選ぶ。文字・配置・線品質・記号族を増やす場合も正本モデルを変え、renderer固有表現を正本にしない。

## 5. 初回評価

seed 2609、100図面、3〜6段で2,290本の正解接続を生成した。

| 段階 | 接続hit/gold | 再現率 | 適合率 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 初回 | 2,238/2,290 | 0.9773 | 1.0000 | 薄い下側母線の直結辺を52本見落とした |
| 修正後（初期生成族） | 2,290/2,290 | 1.0000 | 1.0000 | 合成100図面の配線解析ゲートを通過 |
| 多様化・端子単位照合後 | 2,373/2,373 | 1.0000 | 1.0000 | 2回路族・4描画profile、回路semantic mismatch 0 |

初回の見落としは画素消失ではなかった。PDF往復の平均絶対画素差は0、暗画素再現率は1.0で、線は完全に残っていた。原因は次の2点だった。

1. 表示灯が二端子部品の端子推定対象から外れていた。
2. 別経路で同じnetに属する端子対を「接続済み」とみなし、目に見える直結母線まで省略していた。また通常の探索半径450pxを超える長い母線を扱えなかった。

修正後は、直接端子対だけを重複判定し、長い区間は整列した分岐黒丸間の連続実線だけを採用した。ずれた黒丸、点状ノイズ、カード枠、既存配線の二重記録を防ぐ精度テストを維持する。評価は記号IDの辺だけでなく、端子refの辺を照合する。さらに解析結果の最終クラス、OCRラベル、解析端子、解析接続だけから別の回路projectとSPICE互換netlistを再構成し、正本の記号意味とnode partitionに照合する。正本の記号クラスやラベルを予測側へ流用しない。端子対応には記号寸法の25%（最小8px）の距離上限を設け、遠い正解端子へ強制対応しない。誤検出、未検出、クラス違い、端子欠落、hard diagnostic、失敗ページはいずれもsemanticの不一致としてgold分母に残す。

独立再構築へ切り替えた直後は、合成b接点のストロークが実図面規約と逆で、100図面中176個がa接点へ補正されるずれが見つかった。長い縦棒を置く案は接点種別を直せたが、配線として誤検出され接続recallが0.9461まで低下したため棄却した。最終形は端子丸の左側に短く分割したb接点ストロークを置き、配線との距離を確保した。再生成したseed 2609の100図面では記号クラス2,115/2,115、端子接続2,373/2,373、回路semantic mismatch 0、評価エラー0となった。

固定30実図面も同じ本番解析経路で再生成した。公式指標`eval_editor_import.py`の`wire_nodes.conv_recall`は **1,665/1,698 = 0.980565**、判定済み1,714本中の誤接続5本でprecision **0.9971**、評価エラー0だった。未丸め98%ゲートと既存precisionゲートをともに通過し、合成向け修正による実図面指標の低下はなかった。成果物はlocal GPU hostの`runs/synthetic_circuit_real30_20261112/ei_coil_stable_identity.json`に保存した。

### 5.1 大量生成と学習試験

seed 2609で1,000図面を生成し、800 train／200 validationへ図面単位で固定分割した。直列インターロック334枚、単接点ラダー666枚、4描画profileを含み、成果物は117MBだった。local GPU hostの`runs/synthetic_circuit_train1000_v3_20261112/`に保存した。

| 条件 | 合成validation | 固定30実図面の記号recall / F1 | 公式配線ゲート | 採否 |
| --- | --- | --- | --- | --- |
| 既存モデル | mAP50=0、recall=0 | 0.9867 / 0.9884 | 1665/1698=0.980565 | 基準 |
| 合成800枚だけで30 epoch | mAP50=0.995、mAP50-95=0.920 | 0.2197 / 0.2305 | 未実施（記号ゲートで棄却） | 不採用・domain過適合 |
| 実図面24枚＋合成60枚の5分割 | ― | 0.9872 / 0.9737 | 1640/1698=0.965842 | 不採用・誤検出と配線漏れ増加 |
| 実図面24枚＋合成10枚の5分割 | ― | 0.9757 / 0.9759 | 未実施（記号ゲートで棄却） | 不採用・junction漏れ増加 |

合成図面内では学習効果が大きいが、実図面の線・文字・記号形状とのdomain差が残る。したがって今回の学習重みは同梱モデルへ反映しない。生成器と解析規則の回帰だけを採用し、実図面に近い第2renderer・scan劣化・実表示灯goldを追加した後に再学習する。

## 6. 実行方法

```bash
python analysis/tools/synthetic_circuit_dataset.py generate /path/to/output --count 100 --seed 2609
python analysis/tools/synthetic_circuit_dataset.py evaluate /path/to/output
python analysis/tools/synthetic_circuit_dataset.py evaluate /path/to/output --model assets/models/yolo11s_symbols_best.pt
python analysis/tools/synthetic_circuit_dataset.py run /path/to/output --count 100 --seed 2609
```

出力先は空のディレクトリに限定する。大量データはGitへ入れず、manifestと評価結果で生成条件・hash・件数を追跡する。manifestには各ページのsource、PNG、PDF、YOLO label、netlistと、結合PDF、再取込ZIP、classes、data.yamlのSHA-256を保存する。既定では5枚ごとに1枚をvalidationへ固定し、trainと同一画像を共有しない。1回の生成上限は2,000図面とし、結合PDFは画像を逐次再読込して作る。

## 7. 次段階

1. self-hold、interlock、端子台、行先札、複数ページ参照、交差点のdot有無を持つ生成profileを追加する。
2. blur、JPEG圧縮、傾き、crop、scan noiseを座標変換manifest付きで加える。
3. Schemdrawを主renderer adapter、CircuiTikZを第2renderer候補として、見た目の単一性を減らす。
4. 電気的検証が意味を持つ回路族ではngspice batch結果も照合する。
5. oracle記号枠による配線評価と、YOLOを含むend-to-end検出評価を別指標で記録する。
6. 固定実図面ゲートでは`eval_editor_import.py`の`wire_nodes.conv_recall`を用い、分母1,698本、hit 1,665本以上、既存precision以上を維持する。

