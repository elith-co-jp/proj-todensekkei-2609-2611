# 仕様書

## 1. 目的

PDF（スキャン）で受け取るシーケンス図（展開接続図・プラント制御回路図）を、システムが回路として理解し再現できる
形（コード）に置き換え、「A を変えたらどうなるか」のシミュレーションと波及範囲の検討をできるようにするツール。
東電設計「シーケンス図電子化 PoC」の要件定義書 論点②（正解データの整備）から始まり、現在は次の 4 つを 1 つのアプリで行う。

1. **正解データの整備**：シンボル（クラス付きのバウンディングボックス）と配線（from-to。シンボル間または端子間）の
   アノテーションを行い、機械学習に投入できる形式（YOLO）と完全復元用の形式で出力する
2. **AI による読み取り**：記号の検出（YOLO）、配線・端子・文字・リレー表・シート間参照の構造解析と、
   人の確認・修正を学習に戻す AI 改善サイクル
3. **回路の理解と検証**：アノテーションから回路を組み、Python の回路コード・SPICE・Lcapy・SKiDL に書き出し、
   動作・もしも・波及範囲・タイマーを含む時間の流れ・成立条件（逆引き・順引き）・解析方法どうしの整合性を確かめる
4. **図面の変更**：決まった形の操作（JSON）で図面を変更し、試行・適用・取り消しができる。将来ローカル LLM が
   変更案を出す使い方に備える（外部の LLM API は使わない）

運用は各 PC で動く Windows 単一 EXE を第一の形とし、Docker（共有サーバ）でも動かせる。
受け取る PDF はスキャン画像で、回路の動作の正解データは無い。回路の理解の正しさは、論理から作った
正解つきの合成回路で確かめている（DD-0032・DD-0033・DD-0035）。

## 2. 構成

| 層 | 技術 |
|----|------|
| フロントエンド | React 19 / TypeScript / Tailwind CSS 3 / Vite 6 / react-router 7 |
| バックエンド | FastAPI / SQLAlchemy 2 / Pydantic 2 |
| DB | PostgreSQL 17（Docker）／ SQLite（ローカル開発時の既定） |
| 画像 | `data/images/<sha256>.png`（PNG 正規化・重複排除）、一覧用縮小画像 `data/images/thumbs/<sha256>_640.jpg` |

Docker Compose は `frontend`（Nginx）、`backend`（FastAPI + Poppler）、`db`（PostgreSQL）の
3サービスを起動する。ブラウザからの `/api` は frontend が backend へリバースプロキシする。

ディレクトリ配置（`routers/` `services/` `models.py` `schemas.py` `main.py` `tests/` `frontend/`）。

図面の解析は `analysis/runtime.py` が入口で、配線解析の各段（配線の検出、グラフ、端子、結び付け、端子間の接続）は `analysis/wiring/` に責務ごとに分ける。調整値は `analysis/wiring/params.py`、記号の種類の役割は `analysis/wiring/classes.py` だけで決める（DD-0011）。`analysis/tools/` は評価・実験・学習データ作成のスクリプトで、アプリからは読まない。解析は一時ファイルを作らずメモリ上で行い、文字の読み取り（OCR）はページのタイルと記号ごとの拡大読みをスレッドで並べて読む（並べる数は `SEQANNO_OCR_WORKERS`、DD-0012）。

回路は `services/circuit_model.py`（回路の組み立てとシミュレーション、DD-0009）を中心に、責務ごとに分ける。

| ファイル | 役割 |
|---|---|
| `services/circuit_model.py`・`services/circuit_service.py` | アノテーションから回路（ノード・素子・コイルと接点の対応）を組み、状態が落ち着くまで解く。API との受け渡し |
| `services/sheet_links.py` | 行先札・来先札を組にして図面をまたいでつなぐ（DD-0030） |
| `services/circuit_code.py` | 回路を書き換えられる Python のコードにする（DD-0029） |
| `services/circuit_interop.py` | Lcapy のネットリスト・SKiDL のスクリプトにする（DD-0028） |
| `services/circuit_whatif.py`・`services/circuit_timing.py` | もしも・操作の手順・波及範囲（DD-0029）、タイマーと時間の流れ（DD-0033） |
| `services/circuit_trace.py` | 成立条件の逆引きと影響の順引き（DD-0035） |
| `services/consistency.py` | 解析方法どうしの矛盾のチェックと直し方（DD-0031） |
| `services/circuit_edit.py` | 図面の変更の操作・試行・適用・取り消し（DD-0034） |
| `services/annotation_table.py`・`services/circuit_scenarios.py`・`services/cvat_format.py`・`services/reading_metrics.py` | 正解のやり取り（対応表・代表回路・CVAT）と正解率（DD-0035） |
| `services/vector_pdf.py` | PDF の線・黒丸・文字を取り出す（DD-0035。スキャン PDF では取り出せる線が無い） |
| `services/drawing_sets.py` | 図面セット（行先札でつながる図面の組）と、足りないシート・組の候補（DD-0041） |
| `services/device_index.py` | 機器索引: 器具ごとのコイルと接点の場所、a / b 接点の数、要確認、電線番号の一覧と Excel（DD-0044） |
| `services/analysis_jobs.py` | 全図面の AI 解析を裏のジョブで 1 枚ずつ行い、図面ごとに保存する（DD-0039） |
| `services/jst.py` | 画面・API・書き出しの時刻を日本時間にする（DD-0036） |

## 3. 画面仕様

### 3.1 図面一覧（`/`）

| 項目 | 内容 |
|------|------|
| 役割 | 図面の登録、進捗確認、一括出力・削除 |
| 主な操作 | 「図面を登録」で PDF または画像をアップロード（複数可）／PDF は全ページを自動分割／名称またはサムネイルで編集画面へ／チェックボックスは Shift で範囲選択／状態タブ（すべて・作業中・レビュー待ち・完了）で絞り込み／カード表示と表表示の切り替え（ブラウザに保存） |
| 表示 | 登録図面数・シンボル数・配線数・完了数と完了率の KPI、作業の流れ（登録→AI 解析→確認・修正→回路検証→書き出し）、図面ごとのサムネイル・シンボル／端子／配線件数と状態。保存済みの件数と AI の結果（「AI検出 N」「AI配線 M」）を分けて表示し、凡例で意味を示す |
| 利用 API | `GET /api/projects` `GET /api/projects/{id}/thumbnail` `GET /api/stats` `POST /api/projects` `POST /api/projects/bulk-delete` `POST /api/export` |

一覧・入出力・ヘルプで共通のサイドバーは展開／折りたたみ可能。編集画面は図面領域を
最大化する専用フォーカスレイアウトとし、一覧へ戻る導線を編集ヘッダーに表示する。
初回アクセス時は4ステップのツアーを表示し、完了後もサイドバーの
「ツアー」から再表示できる。既定はサムネイルつきカード表示とし、表表示はPCでは表、SPではカードを使い、ページ全体の横スクロールを避ける。
共通ナビゲーションと図面一覧は、[Best Designs on X](https://bestdesignsonx.com/) を参考に、
明るい背景、控えめな境界線、広い余白、青いアクセントと明瞭な見出しで操作を整理する。
ナビゲーションは「作業」（図面一覧→回路検証→入出力）と「AI・ヘルプ」（AI 学習・ヘルプ）の 2 つに分け、項目ごとの色つきアイコンで見分け、案内はアイコン・説明を横に並べたグレーのバナーで示す。
AI 改善サイクルの5段階は番号と線でつながった流れとして表示する。
画像やロゴは流用しない。編集画面のキャンバスは作業領域を優先する。

### 3.2 アノテーションエディタ（`/projects/:id`）

編集ヘッダーには現在位置（現在ページ / 全図面数）、前後の図面への移動、個別出力、自動保存状態を表示する。
現在ページは番号を入力して Enter で移動できる（全角数字可、範囲外は元に戻す）。
ページ移動時に未送信の編集が残っている場合も、対象プロジェクトIDを保持した保存キューから送信する。
詳細パネルは開閉可能で、PCでは右側、SPではキャンバス上のオーバーレイとして表示する。
パネルを閉じると図面全体表示を再計算してキャンバスを最大限利用する。

| モード | キー | 操作 |
|--------|------|------|
| 選択 | `V` | 矩形の選択、ドラッグ移動、四隅ハンドルでリサイズ |
| シンボル | `B` | 図面上をドラッグして矩形を作成（作成前にクラスを選択）。キャンバス上の `Enter` でも中央に作成 |
| 端子 | `T` | シンボル内側をクリックして端子を追加。選択中シンボルはキャンバス上の `Enter` でも追加 |
| 配線 | `C` | 始点 → 終点をクリック。端子の丸を選べば端子単位、矩形本体ならシンボル単位。選択中シンボルは `Enter` で順に確定可能 |

| キー | 動作 |
|------|------|
| `1`〜`9` | クラス切り替え |
| `Delete` / `Backspace` | 選択中のシンボル、または選択中の配線を削除 |
| `Esc` | 選択解除・配線の始点取消・描画中のキャンセル |
| `Ctrl+Z` / `Ctrl+Shift+Z` | 元に戻す／やり直す（直近 100 操作） |
| `Ctrl+S` / `Cmd+S` | 保留中の自動保存を即時実行 |
| `矢印` / `Shift`+`矢印` | 選択中の矩形を移動／リサイズ（`Alt` 併用で微調整） |
| 図面上のホイール | 図面だけを拡大・縮小（カーソル位置基準） |
| `Alt`+ドラッグ／空白ドラッグ | 画面移動 |

詳細パネルは 3 タブ。タブには `tablist` / `tab` / `tabpanel` を設定し、左右キーと Home / End で移動できる。

- **シンボル**：一覧、クラス変更、ラベル入力、端子名の編集・削除、正規化座標の表示。並び順は登録順・位置順・種別順を選べる。図面上で選ぶとこのタブへ切り替えて該当行までスクロールし、一覧で選ぶと画面外のシンボルの位置まで図面を移動する
- **配線**：一覧、電線番号、種別（配線／シート間参照）、参照先の入力、削除
- **図面情報**：AI 検出の確認状況（採用・種別修正・削除・手動追加と次回学習への反映）、コイルの配線の取り決め（下記）に合わないコイルの一覧と、通り抜ける配線をその場で分けて端子を付けるボタン（元に戻せる）、名称・シート番号（「AIで読取」で候補を表示）・頁・改訂・状態・担当・メモ、画像のサイズと sha256

**コイルの配線の取り決め**（DD-0009 §6）: リレーコイル・電磁弁・表示灯には端子を 2 個付け、配線はその端子で止める。コイルの中を通り抜ける 1 本の配線にはしない（回路シミュレーションでコイルの両側を別のノードにするため）。

**記号の枠の取り決め**（DD-0017）: 同じ描き方の記号は、どの図面でも同じ範囲を囲む。記号の検出は図面をまたいで学習するので、図面ごとに囲み方が違うと検出の枠がずれ、配線が正解の端子に着かない。

**印だけを囲む**（DD-0049、2026-11-26 から）: 記号の枠は印（白丸・棒・丸・≫ など）だけを囲み、器具番号・型式・参照先の文字は枠に入れない。文字の位置は図面ごとに違うため。器具番号はラベル欄に書き、文字の位置は「ラベルの文字の位置」（シンボルタブの「図面で囲む」）で示す。「近くの文字からラベル」で、近くに書かれた文字からラベルと位置をまとめて付けられる（入力済みのラベルは変えない。候補が複数のものはメモに書く）。下の項目のうち「名前まで囲む」は、今の正解データ（印と名前の枠）の取り決めで、正解データを分け直すまで残す。

- a 接点・b 接点: 端子は接点の内側の白丸 2 個。枠は接点の印と名前を囲み、カードの外側の黒丸（C・NC1・NC2・NO・COM）は含めない。
- 横棒のあるコネクタ（CN10 など）: 黒丸の行に加え、記号の横棒まで囲む。横棒の無い端子台（2AT3 など）は黒丸の行だけでよい。
- 端子台・コネクタ: 名前（TB3 など）まで囲む。右に添えた参照先の注記（(ER20149) など）は含めない。
- 行き先の札（「ER21276へ」）: 札に着く母線の端と札の文字を囲み、その下の空白は含めない。
- コネクタの目盛り線（黒丸の上下にある短い横線）: 枠に含める（DD-0025）。
- 余白: 枠はインクの外側 0〜8 px に付け、名前の下などの空白を含めない（DD-0025）。
- カードの中の接点: カードの外側の黒丸の名前（NC1・NC2・NO・COM）も含めない（DD-0025）。

レイヤーパネルは配線・端子・ラベル（TAG）・AI 検出・シンボル種別ごとに表示を切り替える。ラベルを隠しても選択中のシンボルのラベルは表示する。

編集内容は入力停止から約0.4秒後に**自動保存**する。通信は直列化し、保存中に追加された変更は
最新状態へまとめて次のリクエストで保存する。保存は**シンボルと配線をまとめて置き換える**方式
（送られた内容で全置換する）。図面情報も同じ待機時間で自動保存する。
失敗した保存はプロジェクト別に保持し、自動処理では各世代を1回だけ試行する。新しい編集または保存ボタンで
再試行し、ページ移動後も未保存のアノテーションと図面情報を失わない。
差分管理をしないため実装は単純だが、同時編集は後勝ちになる。

ブラウザ標準の拡大縮小は維持する。図面キャンバス内ではホイールとキャンバス左下の
`縮小 / 全体 / 拡大` で図面だけを拡大縮小できる。マウス・ペン・タッチの単一ポインター操作に対応する。
主要ボタンは24 CSS px以上の操作対象を確保し、キーボードフォーカスを可視化する。

### 3.2.1 解析結果（`/projects/:id/analysis`）

AI の推論と構造解析の結果を 1 枚の図面の上で確かめる画面。検出したシンボル・配線・領域枠（作図領域・DI 範囲・雲マーク・LOC などの大きな枠。
シンボルの件数には含めない）の表示を切り替え、構造 JSON を確認・書き出せる。エディタで人が確認して保存した件数は、AI の結果とは
別に数えて示す。ヘッダーから一覧・前後の図面・アノテーション編集・表の確認へ移動できる。

### 3.2.2 表の読み取り確認（`/projects/:id/tables`、DD-0051）

リレー表・表題欄の OCR の結果を、元の表と同じ行 × 列に並べて図面の表と上下（または重ねて）に見せ、欄ごとに直して
「下書き保存」「確定」する。要確認（確からしさ 70% 未満）・直した欄・AI が読んでいない欄を色で分ける。確定したリレー表は
構造解析の結果のリレー表の行に使い（整合性チェック・シート間の照合・書き出し）、解析し直しても人の読みを使う。

### 3.3 入出力（`/export`）

対象図面を選択して ZIP を出力。全画像は `dataset/images/train` に出力し、train/val 分割はしない。
`project_<id>/` のような別レイアウトは出力しない。
ZIP を選択してインポートすると、`bundle.json` の有無で復元内容が変わる（[DATA_FORMAT.md](DATA_FORMAT.md) 参照）。
クラス定義の一覧と、出力される ZIP の構成、学習コマンド例を同画面に表示する。

### 3.4 ヘルプ（`/guide`）

5 ステップの手順書とキーボード操作一覧、複数人で分担する場合のデータ受け渡し手順。
実際の図面は掲載せず、匿名の操作画面モックで PDF 登録、描画モード、自動保存、図面ズームを説明する。

### 3.5 AI 学習（`/ml`、AI 改善サイクル）

「推論 → 結果表示（JSON）→ 修正 → 蓄積 → 学習 → 改善」のサイクルを回す画面。

- **モデル管理**: YOLO の `.pt` をアップロードして登録。`is_active` のモデルが推論・学習のベース。学習で生成された `best.pt` は自動登録されるが、適用は採用判定で選ぶ
- **推論**: 対象図面と信頼度しきい値（既定 0.5。図面単位の交差検証で F1 が最も高かった値）を指定して実行。雲マークがあれば、その線を消した図面で検出する。検出は `predictions` テーブルに図面単位で「最新だけ」保持し、同画面で JSON 表示できる
- **領域枠**: 正規化サイズで面積 4% 以上、または長辺 40% 以上の検出は `kind=region` として保存し、シンボル件数・エディタへの取込・構造解析から外す。解析結果画面で表示を切り替えられる
- **構造解析**: 検出したシンボルから配線と端子間の接続を組み、文字（器具番号・端子番号・装置名・行先）、リレー表、シート間参照、シート番号の候補、領域の種類を構造 JSON に出す（形式は DATA_FORMAT.md §5）。リレー表・表題欄の中の検出は配線に使わない。雲マークの線は消してから配線を読み、雲マークは領域として出す。接点の端子番号を読み、リレー表の行を行列の位置の接点に結び付けて端子番号と接点の種類を照合する。円形の記号はリレー・表示灯の手がかりで確かめ、「その他」は区分（行先・カード・端子台ユニット・盤・遮断器）を付ける。書き出し時に、行先・来先のシート番号を同時に書き出した図面と照合し、別のシートのコイルを指す接点・コイルをその図面のコイルとリレー表の行に結び付ける
- **AI 解析の実行**: 「AI 解析を実行」で、選んだ図面（未選択なら全図面）の推論と構造解析を裏のジョブで 1 枚ずつ行い、図面ごとに保存する（DD-0039）。1 秒ごとに進み具合（何枚目か・解析中の図面・失敗の数）と中止ボタンを出し、画面を離れて戻っても表示を続ける。失敗した図面は理由とともに飛ばして次へ進み、中止すると解析中の図面を終えたところで止まる。ジョブはプロセスの中だけに持つ（再起動すると一覧は消えるが、保存した結果は残る）。EXE は解析中のジョブがある間は自動終了しない
- **日本語の名称・注記の読み直し**: 名称・注記と判定した文字だけを、同梱の日本語対応の文字認識モデル（PP-OCRv5）で読み直す。モデルファイルが無い開発環境では読み直さない
- **ローカル LLM による読み直し（任意）**: 環境変数 `LABEL_LLM_URL`（Ollama 互換 API）と `LABEL_LLM_MODEL` を設定したときだけ、器具番号の形をしていない接点・コイルのラベル（830-1、0010 など）と、リレー表・表題欄の外の日本語の名称・注記を LLM に読み直させる。設定がなければ OCR の結果だけを使う
- **図面の字体に合わせた文字認識（任意）**: 環境変数 `SEQANNO_OCR_REC_MODEL` に、図面の文字で追加学習した文字認識モデル（ONNX）を指定すると、コネクタ・接点・コイルの枠ごとの拡大読みだけをそのモデルで読む。ページ全体の読み（リレー表・注記・シート番号）は同梱のモデルのまま。指定がなければ同梱のモデルで読む
- **外部推論の取込**: `ultralytics` 未導入環境では、外部で `yolo predict --save-txt --save-conf` した labels ZIP を取り込む。エクスポート画像名（`p<図面ID>_*.png`）と同名の `p<図面ID>_*.txt` を自動で図面へ対応づける
- **修正**: エディタの「AI 取込」ボタンで、AI の検出（シンボル）と、構造解析の端子・配線を 1 回で取り込む（構造解析が無ければシンボルだけ）。取り込んだものは破線で表示する AI の候補で、通常と同じ操作で修正・削除する。保存時に `origin=inference`（シンボルは信頼度も）が付く。取り込みは追加だけで、人が入力したものは変えない。既にあるシンボル・端子・配線には重ねず、2 回押しても増えない
- **学習**: 蓄積アノテーションから `data/training/run_<id>/dataset` を生成し、`ultralytics` で学習（バックグラウンドスレッド）。データ拡張は図面向けの設定（反転と色の揺らぎなし、拡大縮小 ±20%）。横書きの図面・シンボルも読めるよう、学習用の図面には 90° と 270° に回したコピーを足す（評価用の図面には足さない。学習に使う画像は 3 倍になる）。配線の折れ点の目印（その他／ラベル「分岐点」）は記号ではないので学習データに入れない（DD-0007）。エクスポートの YOLO ラベルには従来どおり含める。履歴にメトリクス（mAP 等）とログ末尾を保持。「未学習の図面で評価する」（既定オン）で図面が 5 枚以上あれば、約 20% を評価用として学習から外す。完了時に新旧モデルを同じ評価用図面で評価し、比較指標・ベースライン名・評価範囲（`eval_scope`）を記録して採用待ち（`decision=pending`）にする。評価用の図面でのクラス別の P・R・mAP と件数も新旧とも記録する
- **採用判定**: 採用待ちの学習ジョブに、新旧の全体の指標とクラス別の mAP50-95 の表、基準による推奨を出す。基準は、学習に使っていない図面で全体の mAP50-95 が 0.005 を超えて下がらず、評価用に 5 件以上あるクラスで 0.05 を超えて下がらなければ「採用をおすすめ」、どちらかに当たれば「見送りをおすすめ」、学習に使った図面で評価したときは「この評価だけでは判断できません」（DD-0008 §3）。採用・見送りは利用者が選ぶ
- **学習データの状態**: 完了の図面のクラス別の正解件数と図面数を出し、30 件未満のクラスを「少ない」と示す。AI の検出と直した後の正解の一致（種別も含めた F1）を完了の図面ごとに求めて低い順に並べ、0.9 未満を「難例」としてエディタへのリンクを付ける（DD-0008）
- **Windows 配布版**: CPU 版 PyTorch・Ultralytics・学習開始用の検証済み YOLO11s 重み・学習済みの初期推論モデル（`assets/models/yolo11s_symbols_best.pt`）・カード用のモデル（`assets/models/yolo11s_cards_best.pt`）を単一 EXE に同梱。推論では「その他」（カード・札・領域）だけをカード用のモデルの検出に置き換え、ほかの記号は使用中のモデルの検出を使う（並んだカードを 1 つにまとめないため。DD-0019）。カード用のモデルは環境変数 `SEQANNO_CARD_MODEL` で差し替えられ、空にすると使わない。AI 改善サイクル画面に「カードは別モデル」と表示する。モデル未登録の初回起動時に同梱モデルを使用中の推論モデルとして自動登録する。初回学習は使用中のモデル（同梱モデル等）をベースに行われ、登録モデルが無い環境では内蔵 yolo11s 重みが起点になる（図面単位の交差検証で yolo11n より未学習図面の検出 F1 が高かった、DD-0007 §4.11）。学習済み `best.pt` は採用判定で推論モデルになる。シーケンス図向け未学習の初期重みを推論モデルとして自動登録しない
- **外部実行への誘導**: `ultralytics` が無い場合、推論・学習 API は日本語メッセージで外部フロー（エクスポート → 外部 predict/学習 → 取込）を案内する

### 3.5.1 記号・配線の属性

記号と配線にメーカー・型式・長さなどの属性を持たせる（DD-0010）。エディタの詳細で、標準の項目（記号の種類・配線で変わる）と自由に足した項目を編集する。配線には図面内で一意な参照名（W-0001）があり、保存し直しても変わらない。入出力の画面で、全オブジェクトをオブジェクト ID と 1 対 1 で並べた JSON を書き出し（値のみ / 入力用の空欄つき）、編集した JSON を取り込める（形式は DATA_FORMAT.md §6）。

### 3.6 回路検証（`/circuit`）

図面のアノテーションから回路を組み、接点の On/Off で電気がどこまで通り、どのリレーが動くかを確かめる画面（DD-0009）。

**画面の構成**（「図面選択 → 回路作成 → 動作確認」の 3 段で示す。文言とアイコンは [UI_WORDING.md](UI_WORDING.md)）

- 図面選択の欄は、図面セット（DD-0041）、検索、全選択／選択解除、選択中の枚数を持つ。図面セットを選ぶとその図面を選び、行先札が指すのにセットに無いシートを示す（登録済みなら「選択に追加」）。選んだ図面は名前を付けてセットとして保存でき、「行先札から候補」で札でつながる図面の組を候補に出す。「回路を作成」のあとは 1 行に畳み、「図面選択」「再作成」（エディタで直した内容を読み込み直す）を置く
- 図面一覧の各図面の「回路検証」アイコンと、選んだ図面の「回路検証（N 件）」から `#/circuit?ids=1,2` で開くと、その図面で回路を組んだ状態になる
- 作成したあとは、見出しを 1 行（図面・警告の件数・図面選択・再作成）に縮め、残りの高さをすべて図面に使う
- 図面はホイールでカーソル位置を中心に拡大・縮小、ドラッグで移動、ダブルクリック・「全体表示」・F キーで全体表示、＋／− キーで拡大・縮小（倍率を表示）。「全画面」でメニューも隠して画面いっぱいに表示し、Esc で戻る
- 操作は図面の上に重ねる: 左上にページの切り替え・入力 ON の数・励磁の数・回路の警告・凡例（通電／接点 閉・開／励磁／入力）、左下に拡大・縮小、右上に全画面とパネルの開閉（P キー）。外部入力の接点（水色）は図面の上でクリックして ON / OFF を切り替えられる
- パネルのタブ（操作／機器／条件／波及／タイマー／検査／変更）は、幅 1280px 以上では図面の右に重ね、閉じるとアイコンだけの列になって図面を広く使える。全体表示はパネルで隠れる幅を除いて合わせる。それより狭い画面では図面の下に並べる。「検査」は回路にできなかった箇所・シート間接続・整合性チェックをまとめ、件数をバッジで示す

- 選んだ図面の記号・端子・配線から回路を組む。コネクタ・分岐点は通り抜けの点、接点はコイルで開閉する接点、コイル・電磁弁・表示灯は負荷。押しボタンは外部入力の接点（a / b は属性 `contact_kind`、無ければ名前で停止は b・起動は a）、ダイオードは向きのある導線（アノード側は属性 `anode_terminal` か端子名 A/K。分からなければ両向きに流し、確認が必要な箇所に出す。DD-0037）。母線（PER…/NER…）と器具番号が同じリレーは図面をまたいでつなぐ
- 同じ記号の 2 つの端子を結ぶ配線は、接点・コイルなどの機器では素子そのもの、カード（2B-2RSAM など）の列の上と下の黒丸（+ と -、縦に 30 px 以上）を結ぶ線はカードの入出力の素子として扱い、導線にしない。盤の枠（2RAXP・2RMMP）の中の線（枠を横切る短い線、母線に沿った線）は導線としてつなぐ（DD-0022）
- 端子の無い分岐点・コネクタに付けた配線は、記号そのものを 1 つの節点としてつなぐ（DD-0023）
- 選んだ図面にコイルが無い接点は外部入力として On/Off を切り替える。切り替えるたびに前の励磁状態から解くので、自己保持の回路は保持される
- コイルは端子 2 個で配線する取り決め（§3.2）に従ったアノテーションを使う。AI の取り込みだけでは励磁の再現が 3 割程度のため、器具番号・接点の端子・黒丸の無い交差をエディタで確かめてから使う（DD-0009 §7）
- 図面の上に、電圧のかかった配線（赤）、閉じた接点（緑）、励磁したコイル（赤）を重ねる。端子の足りない記号や PER と NER の直結など、回路にできない箇所を一覧に出す。一覧の「エディタで直す」で、該当の記号を選んだ状態のエディタを開く（`#/projects/{id}?symbol=SYM-0001`）
- 接点とコイルは器具番号と機器名で結び付ける。参照先シートの括弧は空白が無くても除き、OCR が取り違えやすい O と 0、I と 1、B と 8 は同じ字とみなす
- 電気は SPICE（ngspice があればそれ、無ければ同じネットリストを内蔵の節点解析）で解く。LTspice・ngspice で開けるネットリスト（.cir）を書き出せる

**パネルのタブ**

| タブ | 内容 |
|---|---|
| 操作 | 外部入力の ON / OFF の切り替え、全 OFF、SPICE ネットリスト（.cir）の書き出し。結果は図面の上の重ね表示に出る |
| 機器 | 機器索引（DD-0044）。器具番号・電線番号・ページで探し（全角・半角、大文字・小文字、空白の違いは無視）、器具ごとにコイル（負荷は本体）と接点の場所（ページと、図面を 3×3 に分けた位置「左上」など）、a 接点・b 接点の数、タイマーの設定を示す。場所を押すと図面がそのページに切り替わり、記号を中心に拡大して枠を点滅させる。一覧は機器／電線番号／要確認を切り替える。要確認は、接点の無いリレー、コイルが 2 個ある器具、表記の違うラベル（O と 0 など）をまとめた器具、接点の枠のコイルのシートとコイルのページの食い違い、選んだシートにあるはずのコイルが無い接点、器具番号の無い接点、別のノードにある同じ電線番号。「Excel」で機器一覧・接点の場所・電線番号・要確認の 4 枚のシートを書き出す（DATA_FORMAT.md §7.1） |
| 条件 | 図面を読む手順（DD-0035）。**逆引き**は対象のコイル・出力を選ぶと、電源から戻りまでの経路を全部たどり、直列は AND・並列は OR で成立条件を出す（コイルより下の戻り側の接点も含む）。入力ごとに動作・非動作・不明を選ぶと、成立・不成立・判定不能を枝ごとに示す（開は赤・不明は黄。押すとエディタのその記号へ）。接点を動かす元へ辿った木も示す。**順引き**は変化した器具を選ぶと、その接点を条件に含む出力を段数・接点の位置づけ（必須か、一部の枝だけか）・状態の変化つきで示す。器具の出どころ（リレー・外部入力・参照先欠落・未解決）を分け、参照先のシートが無いものは断定しない。対応表（Excel）と CVAT の XML もここから書き出す |
| 波及 | 「もしも」と波及範囲（DD-0029）。機器を選び、今の状態から**強制 ON**（溶着・誤動作の確認）／**強制 OFF**（断線・故障の確認）にしたときに変わるリレー・負荷・接点を示す。その機器が動いたら影響しうる機器と、その機器を動かしうる機器を示す。回路を書き換えられる Python のコード（.py）として書き出す（回路コード出力） |
| タイマー | 時刻つきの操作を行で並べて実行し、オンディレー・オフディレーのタイマーを含む時間の流れを示す（DD-0033）。タイマーの種類と時間はコイルの属性（`timer_mode`・`delay_s`）から読み、画面で時間を変えて試せる |
| 検査 | 回路にできなかった箇所（エディタで直す導線つき）、シート間接続（行先札・来先札の組と組にならない札。DD-0030）、整合性チェック（DD-0031）。整合性チェックは、リレー表・接点の参照シート・電線番号・行先札・端子の配線・回路計算の方法（内蔵・ngspice・Lcapy）・波及範囲・発振の有無を突き合わせて矛盾を出し、根拠が 2 つの方法でそろう矛盾は一括修正・個別に修正を適用できる（直した内容は記号のメモに残る）。適用後に再チェックする |
| 変更 | 図面の変更（DD-0034）。部品の追加・削除、名前や種類の変更、配線のつなぎ替え、直列への差し込みを JSON の操作で書き、**試行**で図面を変えずに差分・動作の違い・波及範囲・検査結果を出す。**適用**すると変更セットとして履歴に残り、**元に戻す**で取り消せる（その後に図面が変わっていれば確認のうえ「それでも戻す」） |

**回路の表現と書き出し**

- 回路の正本は図面のアノテーション（記号・端子・配線）で、そこから組んだ回路を次の形に書き出せる。形式は DATA_FORMAT.md §7
  - Python の回路コード（`POST /api/circuit/code`）: 書き換えて実行すると、もしも・波及範囲をコードで試せる
  - SPICE ネットリスト（`.cir`）: LTspice・ngspice で開ける
  - Lcapy のネットリスト・SKiDL のスクリプト（DD-0028）: リレーの動作は表せないため、電気の答え合わせと KiCad 用のネットリスト用。Lcapy・SKiDL は任意の依存（`requirements-interop.txt`）で、Windows EXE には入れない
- 図面をまたぐつながりは、母線（PER…/NER…）、同じ器具番号のリレー、行先札・来先札の組（DD-0030）で作る。区別できない札は無理につながず、検査に出す
- 属性の候補（DD-0050）: AI 解析は図面の文字から、タイマーの種類と時間（コイルの右の「1sec」、下の型式 TDPU / TDDO）、キープリレーのセット・リセット（並んだ丸の中の O・R）、信号名（母線の上の縦書き）、外部入力（I/O カードの点番号 D001 の接点）を推定し、AI 取込の記号に入れて注記に確かめる理由を書く。I/O 表（器具番号と信号名の一覧。デモ画面の「I/O 表を登録」）を登録すると、回路を組むたびに器具番号が一致する接点へ外部入力と信号名を入れる。人が登録した図面では入っている属性を変えず、AI 解析だけの図面では I/O 表を優先する
- タイマー（限時継電器）はリレーコイルの属性で表す。タイマーの時間が無いものは 0 秒として扱い、確認が必要な箇所に出す（DD-0033）
- 押しボタンなど入力の接点は、名前の規則（停止は b 接点、起動は a 接点）で a/b の読み違いを確認する（DD-0033）
- 将来 LLM が図面を変更するときは、`GET /api/circuit/edit/tools` のツール定義（JSON Schema）で操作を出し、試行の結果を見て人が適用する。LLM の呼び出しそのものはアプリに入れていない（ローカル LLM を前提とし、外部 API は使わない）

## 4. API 仕様

ベースパス `/api`。エラーは `HTTPException` で日本語メッセージを返す。

### クラスマスタ

| メソッド | パス | 内容 |
|---|---|---|
| `GET` | `/api/classes` | `yolo_index` 昇順で一覧 |
| `POST` | `/api/classes` | 追加（`yolo_index` は末尾に自動採番） |
| `PUT` | `/api/classes/{id}` | 更新（`key` の重複は 400） |

### プロジェクト
| メソッド | パス | 内容 |
|---|---|---|
| `POST` | `/api/projects` | multipart `files`。画像は1ファイル、PDFは1ページ = 1プロジェクト。横書き（ページ全体を 90° 回して描いた）ページは縦向きに直して保存し、図面のメモに残す。`{project_ids, count, turned: [{project_id, degrees}]}` |
| `GET` | `/api/projects?q=` | 一覧。`q` が数字なら ID 一致、それ以外は名称・シート番号の部分一致。`prediction_count` は領域枠を除く AI 検出数、`analysis_wire_count` / `analysis_connection_count` は構造解析の配線数・接続数（未解析は null） |
| `GET` | `/api/projects/{id}` | 詳細（シンボル・端子・配線を含む） |
| `PUT` | `/api/projects/{id}/meta` | 図面情報の更新 |
| `POST` | `/api/projects/{id}/sheet-number/suggest` | 図面下部を OCR し、シート番号の候補を最大 3 件返す `{candidates: [{text, score, region}]}`。sheet_no は書き換えない |
| `PUT` | `/api/projects/{id}/annotations` | **一括置換**。`{symbols[], connections[]}` |
| `DELETE` | `/api/projects/{id}` | 削除（画像・シンボル・配線を連鎖削除） |
| `POST` | `/api/projects/bulk-delete` | `{ids}` |
| `GET` | `/api/projects/{id}/image` | PNG を返す |
| `GET` | `/api/projects/{id}/thumbnail` | 長辺 640px 以下の JPEG を返す（初回作成後は再利用） |
| `GET` | `/api/projects/{id}/vector-pdf` | PDF で登録した図面の線・黒丸・文字と接続の候補（DD-0035、DATA_FORMAT.md §9）。画像で登録した図面・DD-0035 より前の登録は 404。スキャン PDF は `kind="scan"` で線は無い |

### 入出力

| メソッド | パス | 内容 |
|---|---|---|
| `GET` | `/api/projects/{id}/export` | 単一図面の ZIP |
| `POST` | `/api/export` | `{ids}` で ZIP（全画像を train に出力） |
| `POST` | `/api/import` | multipart `archive`（`.zip`）。`{mode, project_ids, count}` |
| `GET` | `/api/stats` | 件数集計（`by_origin` に manual / inference の内訳を含む） |

### AI 改善サイクル

| メソッド | パス | 内容 |
|---|---|---|
| `GET` | `/api/ml/status` | ultralytics 導入可否・使用中モデル・実行中の学習 |
| `GET` | `/api/ml/models` | モデル一覧 |
| `POST` | `/api/ml/models` | multipart `file`（`.pt`）。初回は自動で使用中に |
| `POST` | `/api/ml/models/{id}/activate` | そのモデルを使用中に切替 |
| `DELETE` | `/api/ml/models/{id}` | 削除（実ファイルは他の参照がなければ削除） |
| `GET` | `/api/ml/models/{id}/download` | モデルファイルを返す |
| `POST` | `/api/ml/inference/run` | `{project_ids[], conf}`。使用中モデルで推論し `predictions` へ保存 |
| `POST` | `/api/ml/inference/import` | multipart `archive`（外部 `yolo predict` の labels ZIP）。`{results, unmatched_files, missing_project_ids, count}` |
| `GET` | `/api/ml/predictions` | 図面ごとの検出件数サマリ |
| `GET` | `/api/ml/projects/{id}/predictions` | 図面の最新推論結果（JSON。クラス名・信頼度つき）。`detections` はシンボル候補、`regions` は領域枠 |
| `GET` | `/api/ml/projects/{id}/review-summary` | AI 検出の確認状況（採用・種別修正・削除・手動追加、領域枠数、次回学習に含まれるか）。`wiring` に AI 端子・AI 配線の `ai_count` / `imported` / `kept` / `deleted` / `manual_added`（構造解析が無ければ null）。`coils` にコイルの数（`count`）と取り決めに合わないコイル（`issues[]`: `symbol_ref`・`label`・`kind` = terminals / unwired / through・`message`） |
| `POST` | `/api/ml/projects/{id}/ai-import` | エディタの現在の状態（未保存を含む）を受け取り、AI のシンボル・端子・配線のうち未登録のものだけを追加分として返す |
| `POST` | `/api/ml/analysis/run` | `{project_ids[], conf}`。使用中モデルで推論し、続けて構造解析を実行して図面ごとに最新の結果を保存（アノテーションは変更しない） |
| `POST` | `/api/ml/analysis/jobs` | 同じ解析を裏のジョブで行う（DD-0039）。図面ごとに保存し、失敗した図面は飛ばす。202 でジョブ。解析中は 409。AI 改善サイクルの画面はこちらを使う |
| `GET` | `/api/ml/analysis/jobs`、`/api/ml/analysis/jobs/{id}` | ジョブの一覧・進み具合（`done` / `total`、`current`、`results`、`failures`） |
| `POST` | `/api/ml/analysis/jobs/{id}/cancel` | 解析中の図面を終えたら止める |
| `GET` | `/api/ml/projects/{id}/structure` | 図面の最新の構造解析結果（未実行は null） |
| `GET` | `/api/projects/{id}/tables` | 表（リレー表・表題欄）の OCR の格子・人が直した値・状態（`unreviewed` / `draft` / `confirmed`）・リレー表の行（DD-0051） |
| `PUT` | `/api/projects/{id}/tables/{kind}` | `{values: [{row, col, text}], confirm}`。直した値を保存し、`confirm` で確定（確定済みの表を確定なしで保存すると確認中に戻る） |
| `DELETE` | `/api/projects/{id}/tables/{kind}` | 確かめた結果を消して AI の読みから始め直す |
| `POST` | `/api/ml/analysis/export` | `{project_ids[]}`。構造解析の結果を 1 つの `structure.json` にまとめて返す（形式は DATA_FORMAT.md §5）。未実行の図面があれば 409 |
| `POST` | `/api/ml/training/run` | `{project_ids[], only_done, epochs, imgsz, base_model, holdout}`。バックグラウンドで学習。`holdout`（既定 true）で評価用図面を学習から外す |
| `GET` | `/api/ml/training/runs` | 学習履歴（最新 50 件。メトリクス・ログ末尾つき） |
| `GET` | `/api/ml/training/runs/{id}` | 学習ジョブの状態・メトリクス・新旧比較・ログ。`per_class` / `baseline_per_class` にクラス別の値（`label`・`instances`・`precision`・`recall`・`map50`・`map50_95`）、`review` に採用判定の推奨（`verdict`: adopt / hold / caution / unknown と `reasons`） |
| `GET` | `/api/ml/training/class-stats` | クラス別の正解件数（完了の図面と全図面）、その記号がある図面数、30 件未満の印 `thin` |
| `GET` | `/api/ml/training/hard-drawings` | 完了の図面ごとの AI の検出と正解の一致（`f1`・`missed`・`false_detections`・`relabeled`）。一致の低い順。0.9 未満は `hard: true` |
| `POST` | `/api/ml/training/runs/{id}/decision` | `{decision: adopt\|reject}`。採用で成果物モデルを使用中に切替え |
| `GET` | `/api/attributes/definitions` | 記号（共通・種類別）と配線の標準の属性 |
| `POST` | `/api/attributes/export` | `{project_ids[], template}`。オブジェクト ID と属性を 1 対 1 で並べた JSON（DATA_FORMAT.md §6） |
| `POST` | `/api/attributes/import` | multipart `file`。属性の JSON を取り込み、`updated`・`unchanged`・`not_found`・`duplicates`・`type_mismatch`・`invalid` を返す |
| `GET` / `POST` | `/api/drawing-sets` | 図面セットの一覧 / 作成 `{name, project_ids[], note}`（201。同じ名前は 400）。DD-0041 |
| `GET` / `PUT` / `DELETE` | `/api/drawing-sets/{id}` | 図面セットの取得 / 変更（`name`・`project_ids`・`note`）/ 削除（204。図面は消えない） |
| `GET` | `/api/drawing-sets/{id}/coverage` | 行先札が指すのにセットに無いシート（`missing_sheets`: 参照した札と、登録済みの図面の候補）、シート番号の分からない図面、`complete` |
| `GET` | `/api/drawing-sets/suggest` | 登録済みの全図面を、行先札でつながるまとまりに分けた候補（保存しない）。回路の API にはセットの `project_ids` を渡す |
| `POST` | `/api/circuit/build` | `{project_ids[]}`。回路を組み、コイルと接点、外部入力、確認が必要な箇所（`diagnostics`）、電源、解き方（`engine`）を返す |
| `POST` | `/api/circuit/simulate` | `{project_ids[], inputs: {器具番号: bool}, energized: {器具番号: bool}}`。前の励磁状態から解き、`coil_states`・`warnings`・図面ごとの重ね表示（`overlay`）を返す |
| `POST` | `/api/circuit/split-coil-wires` | `{symbols[], connections[], image_width, image_height}`（PUT annotations と同じ形）。コイルを通り抜ける配線を 2 本に分けて端子を付けた結果（`symbols`・`connections`・`counts`）を返す。保存はしない |
| `POST` | `/api/circuit/netlist` | `{project_ids[], inputs}`。LTspice・ngspice で開けるネットリスト（circuit.cir） |
| `POST` | `/api/circuit/code` | `{project_ids[], inputs, energized}`。回路を書き換えられる Python のコード（circuit.py。DD-0029） |
| `POST` | `/api/circuit/lcapy` | `{project_ids[], inputs, energized, fixed}`。Lcapy のネットリスト。`fixed=true` で接点を今の開閉（閉は導線・開は開放）に固定（DD-0028） |
| `POST` | `/api/circuit/skidl` | `{project_ids[]}`。実行すると KiCad のネットリストを書き出す SKiDL のスクリプト（DD-0028） |
| `POST` | `/api/circuit/what-if` | `{project_ids[], inputs, energized, change}`。`change` は `inputs`（入力の切り替え）・`devices`（機器の動作の固定）・`contacts`（接点の溶着 / 接触不良）・`open`（断線）・`short`（短絡）。変わるリレー・負荷・接点を返す（DD-0029） |
| `POST` | `/api/circuit/sequence` | what-if と同じ形に `steps[]`（`label`・`inputs`、最大 50）。操作の手順を順に解き、各手順で変わったところを返す |
| `POST` | `/api/circuit/timeline` | what-if と同じ形に `events[]`（`at` 秒・`label`・`inputs`、最大 100）、`delays`（器具番号 → 秒）、`until`。タイマーを含む時間の流れ（DD-0033） |
| `POST` | `/api/circuit/impact` | `{project_ids[], device}`。その機器が動いたら影響しうるリレー・負荷と、その機器を動かしうる機器（DD-0029） |
| `POST` | `/api/circuit/trace` | `{project_ids[], mode, target / symbol / device, states, energized, external}`。`mode=backward` は成立条件と成立・不成立・判定不能（枝ごとの根拠つき）、`forward` は影響先（DD-0035） |
| `POST` | `/api/circuit/trace/targets` | `{project_ids[]}`。逆引きの対象（コイル・負荷）と順引きの起点（器具と出どころ） |
| `POST` | `/api/circuit/devices` | `{project_ids[]}`。機器索引（DD-0044）: 器具ごとのコイル・接点・負荷の場所（ページ・位置）、a / b 接点の数、タイマー、要確認、器具番号の無い接点、電線番号ごとのつながる記号 |
| `GET` | `/api/circuit/devices.xlsx?project_ids=1,2` | 機器索引の Excel（機器一覧・接点の場所・電線番号・要確認の 4 シート） |
| `POST` | `/api/circuit/consistency` | `{project_ids[]}`。解析方法どうしの矛盾と精査の結果・直し方（DD-0031） |
| `POST` | `/api/circuit/consistency/apply` | `{project_ids[], issue_ids[]}`。選んだ直し方をアノテーションに適用し、もう一度チェックした結果を返す |
| `GET` | `/api/circuit/io-table` | 登録した I/O 表の要約（`loaded`・`file_name`・`updated_at`・`rows`・`inputs`・先頭 5 行の `preview`。DD-0050） |
| `PUT` | `/api/circuit/io-table` | multipart の `file`（CSV〈UTF-8 / Shift_JIS、カンマ・タブ〉か .xlsx、10 MB まで）。見出しから器具番号・信号名・入出力の列を決めて登録する。見出しが無ければ 400 |
| `DELETE` | `/api/circuit/io-table` | I/O 表を外す |
| `GET` | `/api/circuit/edit/tools` | 図面の変更の LLM 用ツール定義と操作ごとの JSON スキーマ（DD-0034） |
| `POST` | `/api/circuit/edit/context` | `{project_ids[]}`。変更する図面の記号・端子・配線・回路コード（LLM に渡す文脈） |
| `POST` | `/api/circuit/edit/preview` | `{project_ids[], ops[], title, reason, author, base_hash}`（`ops` は最大 200）。試行: 差分・検証・動作の変化・波及範囲。図面は変えない |
| `POST` | `/api/circuit/edit/apply` | preview と同じ形。適用して変更セットとして残す。`base_hash` が今の図面と違えば 409 |
| `GET` | `/api/circuit/edit/history?project_ids=` | 変更セットの履歴（新しい順） |
| `POST` | `/api/circuit/edit/{id}/undo` | `{force}`。変更前の図面に戻す。その後に図面が変わっていれば 409（`force=true` で戻す） |

### 正解のやり取り（DD-0035）

| メソッド | パス | 内容 |
|---|---|---|
| `GET` | `/api/exchange/table?project_ids=&drawing_set=&format=` | 対応表（図面セットID／ページID／要素ID／種別／原文／参照・接続先ID／判定／根拠・備考）と代表回路の記入例。`format` は xlsx（既定）/ csv / json |
| `POST` | `/api/exchange/table/compare` | multipart `file`・`project_ids`。東電設計様が判定を直した対応表（と代表回路のシート）を、いまの図面と照合して適合率・再現率・誤結線を返す |
| `POST` | `/api/exchange/scenarios/evaluate` | `{project_ids[], scenarios[] または text（YAML / JSON）, external}`。代表回路の正解と機械の判定を照合 |
| `GET` | `/api/exchange/cvat?project_ids=` | CVAT for images 1.1 の XML（記号の枠・端子の点・配線の折れ線） |
| `POST` | `/api/exchange/cvat/import` | multipart `file`・`project_ids`（省略時は画像名から）・`apply`。CVAT の XML を図面に当てる。`apply=false` は確認だけ、`true` でアノテーションを置き換える |

### `PUT /api/projects/{id}/annotations` のリクエスト

```json
{
  "symbols": [
    {
      "ref": "SYM-0001",
      "class_key": "relay_coil",
      "label": "33HB",
      "cx": 0.38, "cy": 0.50, "w": 0.06, "h": 0.08,
      "note": null,
      "origin": "manual", "confidence": null,
      "terminals": [{ "ref": "SYM-0001-T1", "name": "13", "tx": 0.38, "ty": 0.54 }]
    }
  ],
  "connections": [
    {
      "from_symbol_ref": "SYM-0001", "from_terminal_ref": "SYM-0001-T1",
      "to_symbol_ref": "SYM-0002", "to_terminal_ref": null,
      "wire_no": "610", "kind": "wire", "external_ref": null
    }
  ]
}
```

- `ref` を省略すると `SYM-0001` 形式で自動採番する。
- 存在しないシンボルを参照する配線はスキップし、レスポンスの `skipped` に理由を返す。
- 未知の `class_key` はクラスマスタへ自動登録する（`yolo_index` は末尾）。
- 座標は `services/geometry.py` の `sanitize_box` で 0.0〜1.0 に丸める。
- `origin` は `manual`（省略時）または `inference`（AI 推論から取り込んだシンボル・端子・配線）。端子と配線にも `origin` がある。
  `inference` の場合は検出時の `confidence`（0〜1）を保持でき、学習データの出自追跡に使う。

## 5. 非機能

| 項目 | 内容 |
|------|------|
| 運用形態 | 各 PC で動く Windows 単一 EXE を第一とする（サーバは `127.0.0.1` だけで待ち受け、データは EXE と同じフォルダの `data/`）。1 ファイルの EXE は約 913 MB で起動に 10〜13 秒、必要なときだけ作るフォルダ形式（ZIP 約 372 MB）は 1.5〜2 秒で起動する（DD-0040）。Docker Compose（共有サーバ）でも動く |
| 認証 | なし（ローカル運用前提）。必要ならセッション認証を追加する |
| 外部通信 | なし（`LABEL_LLM_URL` を設定したときだけ、その URL へ記号・文字の切り出し画像を送る）。CORS は `SEQANNO_CORS_ORIGINS` で明示したオリジンのみ。ブラウザ発のリクエストは `Origin` がローカル（localhost/127.0.0.1/::1）または許可オリジン以外の場合 403 で拒否（localhost CSRF 対策） |
| アップロード上限 | PDF 256 MiB・500ページ／画像 1 枚 64 MiB／ZIP 展開後 2 GiB・20,000 ファイル・圧縮率 25 倍 |
| PDF 画像化 | PyMuPDF で 150 DPI の PNG に変換（PyMuPDF が無い環境では Poppler）。元ファイル名と1始まりのページ番号を保持。受け取る図面はスキャン PDF を前提とし、線・文字は画像から読む |
| ページの向き | 登録時に OCR の文字検出で文字の向きを調べ、文字の大半が縦向きのページを 90° か 270° 回して保存する（1 ページ約 0.25 秒）。OCR が使えないときはそのまま保存する。上下逆さのページは直さない |
| 時刻 | DB には UTC で保存し、画面・API・書き出しはすべて日本時間（`+09:00` 付きの ISO 形式）で出す。画面は PC のタイムゾーン設定に左右されない（DD-0036） |
| 処理時間 | 4 コア CPU のみの環境で、図面の解析（記号の検出・配線・文字の読み取り）は合成図面 6 枚で約 29 秒（1 枚目のモデル読み込みを除く）。うち約 19 秒が文字の読み取り。回路の画面の各 API は 1 回 0.01〜0.08 秒（[speedup-20261120](evidence/speedup-20261120.md)） |
| 依存の版 | `constraints.txt` で固定する（DD-0037）。更新したら CI が通ることを確かめる |
| LLM | 使う場合はローカル LLM（Ollama 互換 API）のみ。外部の LLM API は使わない。設定が無ければ LLM を使わずに動く |
| 同時編集 | 排他制御なし（後勝ち）。分担する場合は図面単位で分け、ZIP で統合する |
| 機械学習 | Windows 配布 EXE は CPU 版 PyTorch・`requirements-ml.txt`・学習開始用モデルを同梱。開発環境では追加依存を任意導入。未導入環境でもモデル登録・推論結果の取込・JSON 表示は動作し、推論・学習は外部実行フローへ誘導 |

## 6. テスト

`python3 -m pytest`（`tests/` と `analysis/tests/`）、画面は `cd frontend && npm test`（vitest）と `npm run build`（型検査）で実行する。
PR と main への push ごとに `.github/workflows/ci.yml` が pytest・ruff・画面の型検査とテストを回す（DD-0037）。`make test` で同じものをまとめて実行できる。
主なテストファイルと観点は次のとおり。

| 区分 | ファイル | 観点 |
|---|---|---|
| アノテーション・入出力 | `tests/test_annotation_api.py`・`test_export_import.py`・`test_object_attributes.py`・`test_page_orientation.py`・`test_sheet_number.py` | クラスマスタ、PDF ページ分割、CRUD、一括置換、参照解決、座標の丸め、日本語エラー、ZIP 構成・YOLO 形式・往復復元・素の YOLO 取り込み・ZIP のセーフガード、属性の JSON、ページの向き、シート番号の候補 |
| 安全・DB | `tests/test_security.py`・`test_db_design.py`・`test_jst.py`・`test_desktop_launcher.py` | `Origin` ガード、外部キーと連鎖削除・索引・移行、日本時間の出力、EXE の起動 |
| AI 改善サイクル | `tests/test_ml_cycle.py`・`test_ai_import.py`・`test_review_summary.py`・`test_training_holdout.py`・`test_training_quality.py`・`test_card_model.py`・`test_region_frames.py`・`test_fetch_base_model.py` | モデル登録・切替・削除、推論の前提エラー、推論結果 ZIP の取込、AI 取込の重複防止、確認状況、評価用図面の分離、採用判定の推奨、カード用モデル、領域枠 |
| 構造解析 | `tests/test_structure_api.py`・`test_structure_runtime.py`・`test_wire_*.py`・`test_faint_wires.py`・`test_inside_wires.py`・`test_coil_wiring.py`・`test_wiring_recall_gate.py`・`test_vector_pdf.py`、`analysis/tests/` | 配線の検出・グラフ・端子・結び付け、文字・リレー表・シート間参照、再現率の基準、ベクター PDF |
| 回路 | `tests/test_circuit_model.py`・`test_circuit_api.py`・`test_circuit_interop.py`・`test_circuit_whatif.py`・`test_circuit_timing.py`・`test_circuit_trace.py`・`test_sheet_links.py`・`test_consistency.py`・`test_circuit_edit.py`・`test_drawing_sets.py` | 回路の組み立て・シミュレーション、Lcapy・SKiDL・SPICE、もしも・手順・波及範囲、タイマー、逆引き・順引き（3 値の判定）、行先札、整合性チェック、図面の変更と取り消し、押しボタン・ダイオード、図面セット |
| 正解のやり取り・評価 | `tests/test_exchange_api.py`・`test_reading_metrics.py`・`test_logic_validation.py`・`test_synthetic_scan_data.py`、`analysis/tests/test_eval_suite.py` | 対応表・CVAT・代表回路のシナリオ、正解率の分け方、正解つき合成回路での論理の検証、評価の基準値との比較 |
| 性能 | `tests/test_speedups.py` | 高速化した計算が元の計算と同じ結果を返すこと |

合成回路とスキャン風の合成図面での評価は `make eval`（`analysis/tools/eval_suite.py`）の 1 コマンドで行い、`analysis/eval_baseline.json` の基準値より悪くなれば失敗する（DD-0038。CI では `--quick`）。受け取る図面の正解データはまだ無いため、実図面の精度ではなく、合成データでの退行を見張る。
回路の理解の正しさは、正解つきの合成回路 60 個で `analysis/tools/validate_circuit_logic.py`・`validate_reading_procedure.py` を
実行して確かめている（結果は `docs/evidence/`。DD-0032・DD-0033・DD-0035）。
