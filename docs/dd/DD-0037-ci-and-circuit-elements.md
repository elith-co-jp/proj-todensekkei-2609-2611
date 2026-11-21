# DD-0037: テストを自動で回す CI・依存の版の固定と、押しボタン・ダイオードを回路に入れる

- 状態: レビュー中
- 作成日: 2026-11-21
- 関連文書: [DD-0009](DD-0009-circuit-simulation.md)、[DD-0012](DD-0012-speed-and-ci.md)、[DD-0029](DD-0029-circuit-code-whatif-impact.md)、[DD-0035](DD-0035-reading-procedure.md)

## 1. 目的

システム全体のレビュー（2026-11-21）で見つかった、結果の正しさに関わる 2 点を直す。

1. テストが自動で回っていない。CI は main への EXE のビルドだけで、PR のテストは人が手で回していた。
   依存は `>=` だけなので、入れ直すと新しい版が入る。2026-11-21 に入れ直すと FastAPI 0.165 が入り、
   TestClient が別のパッケージを要求してテストが 1 件も動かなかった。ruff も新しい版で既定の規則が増え、155 件の指摘が出た。
2. 回路のシミュレーションが、押しボタンとダイオードを正しく扱っていない。
   - 押しボタン（`push_button`）は接点にも負荷にもならず、回路から黙って消えていた（開いたままと同じ。入力にも診断にも出ない）。
   - ダイオードは 1 kΩ の負荷だった。コイルと直列にするとコイルに 55 V しか掛からず励磁しない。逆向きにも流れる。

## 2. 変えたこと

**CI と版の固定**

- `.github/workflows/ci.yml`: PR と main への push で、Python 3.12 の pytest・ruff と、画面の `tsc -b`・vitest を回す。
- `constraints.txt`: `requirements.txt` と `requirements-dev.txt` の依存をすべて版まで固定（`uv pip compile --universal --python-version 3.11`。
  Windows でも同じ版になる）。CI・Docker・EXE のビルド・`build_windows.bat`・`make install` で `-c constraints.txt` を付ける。
- `requirements.txt`: FastAPI を `<0.120` にする（0.120 以降は TestClient が httpx 以外を要求する）。
- `requirements-dev.txt`: pytest・httpx・ruff（0.15.0 に固定）・opencv-python-headless・numpy。
- ruff 0.15.0 で残った 5 件（関数内の lambda、パスを足した後の import）は意図した書き方なので `noqa` を付けた。

**押しボタン**（`services/circuit_model.py`）

- 押しボタンは外部入力の接点にする。a / b は、属性 `contact_kind`（a / b）→ 名前の取り決め（停止・非常・切・STOP は b、
  起動・運転・入・START は a。DD-0033 の `input_kind_by_name`）→ 既定の a の順で決める。
- 押しボタンを入力にしたことを診断 `push_button_input` で示す。

**ダイオード**

- ダイオードは向きのある導線（素子の種類 `d`）にする。アノード側の端子は、属性 `anode_terminal`（端子名か ref）、
  端子名（A・+・ANODE・アノード / K・-・CATHODE・カソード）の順で決める。
- 向きが分かれば、閉（順方向）から始め、逆向きに流れたら開、開で順方向の電圧が掛かったら閉にし直す。
  リレーの 1 歩ごとに、ダイオードの開閉が変わらなくなるまで（最大 20 回）解き直す。落ち着かなければ警告する。
- 向きが分からなければ両向きに流れる導線（閉じた接点と同じ 0.01 Ω）にし、診断 `diode_direction_unknown` を出す。
- シミュレーションの結果に `diodes`（導通しているか）を足す。
- 逆引き（DD-0035）は、ダイオードを向きどおりにだけたどる。ダイオードは条件の変数にしない。
- 回路コード（DD-0029）は `c.diode(ref, a, b, source=..., directed=True)`。Lcapy は導通なら導線 `W`、止めていれば開放 `O`。
  LTspice / ngspice 向けの書き出しは、向きが分かればダイオード `D_IDEAL`、分からなければ 0.01 Ω の抵抗。
- もしも（DD-0029）の断線はダイオードを開のまま、短絡は両向きに流す。

ダイオードの無い回路は、行列もシミュレーションの手順も前と同じ（`tests/test_speedups.py` で確認）。

## 3. 確認

| 対象 | 方法 | 結果 |
|------|------|------|
| CI | `ruff check .`・`pytest`（Python 3.12、`constraints.txt` で入れた環境）・`tsc -b`・vitest | 指摘 0 件、512 件成功・19 件スキップ（RapidOCR・ngspice が要るもの）、画面 98 件成功 |
| 押しボタン | P ─ 押しボタン ─ コイル ─ N。名前 STOP、属性 `contact_kind` | 入力 PB1 で励磁、STOP は b 接点、属性があれば属性に従う |
| ダイオード | P ─ ダイオード ─ コイル ─ N。向き不明・順・逆、端子名 A/K | 不明と順は励磁（110 V）、逆は励磁しない |
| ダイオードの分離 | 2 つの押しボタンがそれぞれダイオードを通って 1 つのコイルへ。片方の先に表示灯 | PB1 だけでコイルは励磁し、PB2 の先の表示灯は点かない |
| 逆引き・コード | 押しボタン ─ ダイオード ─ コイル | 順向きなら条件 PB1、逆向きなら FALSE。コードを書き出して読み直しても同じ |
