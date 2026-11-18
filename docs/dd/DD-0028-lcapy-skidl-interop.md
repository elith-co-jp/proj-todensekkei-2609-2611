# DD-0028: 読み込んだ図面を Lcapy・SKiDL の表現に置き換える

- 状態: レビュー中
- 作成日: 2026-11-18
- 関連文書: [DD-0009-circuit-simulation.md](DD-0009-circuit-simulation.md), [DD-0027-synthetic-circuit-loop.md](DD-0027-synthetic-circuit-loop.md), [合成回路生成方式の調査](../research/synthetic-circuit-generation.md)

## 1. 目的

図面（PDF → ページ画像 → シンボル・端子・配線のアノテーション）から組んだ回路を、Python の回路ライブラリ
Lcapy と SKiDL の表現へ変換できるかを確かめ、変換器を用意する。

## 2. 現在の図面の表現

```text
PDF / 画像 ──(pdf_service: 1 ページ = 1 図面)──> AnnotationProject
  symbols（class_key・label・bbox・terminals）/ connections（from-to）
        │ serialize_project（＋構造解析の PER/NER の文字）
        v
services/circuit_model.build_circuit ──> Circuit
  nodes:    配線でつながった端子の集まり（P / P_xxx = 電源の正側、0 = NER）
  elements: a 接点・b 接点（kind=a/b, device=器具番号）、負荷（コイル・表示灯・電磁弁・ダイオード = 抵抗）
  coils:    器具番号 → コイル（接点を開閉する対応）
```

既に SPICE（LTspice / ngspice）のネットリストを出しているため、変換の入力は図面の画像や bbox ではなく、この
`Circuit` にした。ノードと 2 端子の素子の一覧なので、どちらのライブラリにも素直に写せる。

## 3. 2 つのライブラリの比較

| 観点 | Lcapy | SKiDL |
|------|-------|-------|
| 目的 | 線形回路の解析（sympy による記号計算の節点解析）と回路図の描画（CircuiTikZ） | 部品・端子・ネットを Python で書き、EDA（KiCad など）のネットリストを生成 |
| 表現の単位 | 素子行のネットリスト（`R1 a b 1000`、`SW1 a b no`、`W`、`O`） | `Part`（端子つき部品）と `Net`（端子の集まり） |
| 本件に合う点 | a/b 接点を `SW … no/nc`、開閉を決めれば `W`/`O` で表せる。直流解析で内蔵の解き方と照合できる | 器具番号・元の記号をフィールドに持てる。KiCad へ渡せばネットの確認（ERC など）やほかの EDA への入口になる |
| 合わない点 | リレー（コイルの電圧で接点が開閉する非線形の動作）は表せない。開閉を決めた 1 状態ずつの解析になる。浮いたノードがあると行列が特異になる（大きな抵抗で接地して回避） | 解析はしない（シミュレーションは別ツール）。盤の配線図には基板の部品の形（footprint）が無く、仮の名前を入れる。KiCad の記号ライブラリは使わずその場で部品の型を作る |
| 描画 | `cct.draw()` で回路図を描けるが、LaTeX（pdflatex + CircuiTikZ）が必要で、配置のヒント（right/down）を各素子に付ける必要がある | 回路図の自動生成（schematizer）は依存が入らず未評価 |
| 速さ（合成回路 40 枚・素子 388・ノード 212） | 1 状態の直流解析 約 0.3 秒 | KiCad ネットリスト生成 約 0.1 秒（10 枚） |
| インストール | 1.26 は依存の `importlib` のため Python 3.13 で `pip install lcapy` が失敗 | 3.0.0 は依存の `kinet2pcb` などのビルドが新しい setuptools で失敗 |
| ライセンス | LGPL-2.1 | MIT |

### 結論

**両方に対応し、役割を分ける。**

- **Lcapy = 解析の照合用。** 接点の開閉を決めた回路を Lcapy で解いた結果は、内蔵の解き方（`circuit_model.solve`）と
  一致した（合成回路 10/20/40 枚、開閉をランダムに決めた各 3 状態の延べ 1,107 ノードで、電圧が電源の半分以上かが一致）。
  リレーの自己保持のような繰り返しの計算は今まで通り `circuit_model.simulate` が受け持つ。
- **SKiDL = ネットリスト交換用。** 変換前の回路と、SKiDL が出した KiCad のネットリストのネット（部品・端子の組）が一致する
  ことをテストで確かめた。器具番号（Device）と元の記号（Source = 図面 ID:記号 ref）を部品のフィールドに残す。
- どちらも本体の必須依存にはしない。Windows exe と Docker に入れず、ライブラリ無しで作れる文字列（Lcapy のネットリスト、
  SKiDL のスクリプト）を API から出し、ライブラリのオブジェクトは遅延 import の関数で作る。

## 4. 実装

| 追加 | 内容 |
|------|------|
| `services/circuit_interop.py` | `part_list`（参照記号: K コイル、S 接点、H 表示灯、Y 電磁弁、D ダイオード、R その他の負荷）、`lcapy_netlist`、`to_lcapy`、`solve_lcapy`、`contact_states`、`skidl_script`、`to_skidl`、`kicad_netlist` |
| `POST /api/circuit/lcapy` | Lcapy のネットリスト。`fixed: true` なら `inputs`・`energized` から接点の開閉を決めて `W`/`O` にする |
| `POST /api/circuit/skidl` | 実行すると `circuit.net`（KiCad 8）を書き出す SKiDL のスクリプト |
| `requirements-interop.txt` | 任意。上のインストールの不具合を避ける手順つき |
| `tests/test_circuit_interop.py` | 文字列の変換と API はいつも、Lcapy の解との照合・SKiDL のネットの照合はライブラリがある環境で確かめる |

### 変換の規則

| 図面 → `Circuit` | Lcapy | SKiDL |
|------------------|-------|-------|
| 母線 P / P_xxx | `V1 P 0 dc 110` | ネット `P` |
| 母線 NER（0） | 基準ノード `0` | ネット `GND` |
| a 接点 | `SWn a b no`（開閉を決めたら `W` / `O`） | 部品 `CONTACT_A`（端子 1・2） |
| b 接点 | `SWn a b nc`（同上） | 部品 `CONTACT_B` |
| コイル・表示灯・電磁弁など | `R<ref> a b <抵抗>`（属性の抵抗値があればその値） | 部品 `COIL` / `LAMP` / `SOLENOID` / `DIODE` / `LOAD` |
| 浮いたノード | `Rleak` 1 GΩ で接地 | なし |

## 5. 既知の制約と次の候補

- 浮いたノード（開いた接点の先だけにつながるノード）の電圧は、内蔵の解き方（開いた接点も 1 GΩ）と Lcapy（開放 `O`）で
  値が違う。どちらも「電源の半分未満」で、生きているかの判定は同じ。
- Lcapy の回路図の描画は未対応。図面の記号の座標（cx, cy）から right/down の配置のヒントを付ければ描ける見込みだが、
  pdflatex が要る。描画は DD-0027 の Schemdraw の方針と合わせて判断する。
- 画面からのダウンロードボタンは未追加（API のみ）。
