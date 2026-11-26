# DD-0047: 暗黙の状態の移り方（ステートマシン）を明示する

- 状態: レビュー中
- 作成日: 2026-11-25
- 関連文書: [DD-0034](DD-0034-llm-circuit-edit.md)、[DD-0039](DD-0039-analysis-jobs.md)、[状態図](../state-machines.md)

## 1. 目的

アプリの中には、文字列の列や真偽値の組み合わせで「状態」と「どこへ移れるか」を表している箇所があり、
移り方はコードを読み通さないと分からなかった。次の 3 つの困りごとがあった。

- 利用者（UX）: 学習中にアプリを閉じると、学習ジョブの行が `running` のまま残り、次に起動しても
  「別の学習が実行中です」と言われ続けて、学習を二度と始められなかった（DB を直接直すしかなかった）。
- 開発者（DX）: 「完了した学習を失敗に書き換える」「判定済みの学習をもう一度採用する」といったありえない移り方を
  止める仕組みが無く、呼ぶ側の if 文に散らばっていた。
- エージェント（AX）: 状態と移り方の一覧がどこにも無く、AI が変更するときも毎回コード全体から推測していた。

## 2. 洗い出した暗黙の状態

| 場所 | 持ち方 | 判断 |
|---|---|---|
| 学習ジョブ `TrainingRun.status`（running / success / failed） | 文字列の列を 3 か所で直接代入 | **明示する**。再起動で running が残る不具合も直す |
| 学習の採用判定 `TrainingRun.decision`（'' / pending / adopted / rejected） | 文字列の列を 4 か所で直接代入、判定済みの確認は router の if | **明示する** |
| 裏の解析ジョブ `AnalysisJob.status`（running / done / cancelled / failed）と `cancel_requested` | 終わり方を三項演算子で決めていた | **明示する**（中止の印は状態にせず、終わり方の条件にする。API は変えない） |
| 回路の変更セット `CircuitChangeSet.status`（applied / undone） | 文字列の列を直接代入 | **明示する** |
| デスクトップ版の画面のつながり（`App.tsx` の desktopMode・shutdownPending・desktopTerminated） | 真偽値 3 つ（8 通りのうち意味があるのは 4 通り） | **明示する**（1 つの状態と移り方の表） |
| 図面の作業状態 `AnnotationProject.status`（draft / review / done） | 利用者が自由に切り替える | 移り方に制限が無いのが仕様。値は `schemas.ProjectStatus` で既に縛っているので変えない |
| 採用中のモデル `MlModel.is_active` | 「1 つだけ真」の不変条件 | 状態の移り方ではないので対象外（`model_service.activate` が 1 か所で守っている） |
| デスクトップ版のサーバの自動終了（`main.py` の heartbeat の監視） | 時刻と真偽値 | 別の PR（#96）が大きく書き換えているので今回は触らない |
| 画面の読み込み中・処理中（`busy`・`loading`・`uploading` など） | 画面ごとの真偽値 | 多くは 1 つの処理の「処理中」だけで組み合わせが無い。#96 が同じ画面を書き換えているので今回は触らない（次の候補） |

## 3. 使う仕組みの比較

一から書かず、GitHub の OSS を比べて選んだ（星の数は 2026-11-25 時点）。

| 候補 | 言語 | 星 | 最終更新 | 合う点 | 合わない点 |
|---|---|---|---|---|---|
| [python-statemachine](https://github.com/fgmacedo/python-statemachine)（MIT） | Python | 1.3k | 2026-10 | クラスで宣言。既存のオブジェクト（ORM の行・dataclass）の列をそのまま読み書きできる（`state_field`）。依存なし。条件つきの移り方（`cond`）、終わりの状態（`final`）、Mermaid の状態図を標準で書き出せる。型付き | 星は transitions より少ない |
| [transitions](https://github.com/pytransitions/transitions)（MIT） | Python | 6.6k | 2025-09 | 実績が多い。辞書で定義 | モデルにメソッドを動的に生やすため型検査・補完が効かない。状態図は graphviz が要る。更新が止まり気味 |
| [XState](https://github.com/statelyai/xstate)（MIT） | TypeScript | 30.5k | 2026-11 | 画面の状態機械の定番。可視化ツールあり | 今回の画面の状態は 4 つだけで、本体 十数 KB を足すほどではない |
| [Robot](https://github.com/matthewp/robot)・[zag](https://github.com/chakra-ui/zag) | TypeScript | 2.2k・5.3k | 2026 | 小さい | 同上。zag は部品（UI コンポーネント）向け |
| React の `useReducer` ＋ 移り方の表 | TypeScript | （React 標準） | — | 依存を足さない。型で状態を 1 つに縛れる | 大きな機械には向かない |

- サーバ側は **python-statemachine 3.2.2** にした。DB の列を値そのままで扱え（移行が要らない）、状態図を定義から作れて
  文書と食い違わない。純 Python で依存が無く、EXE（PyInstaller）にもそのまま入る。
- 画面側は **`useReducer` ＋ 移り方の表** にした。対象は 4 状態 1 つだけで、XState を足す利点が小さい。
  画面に出る学習・解析ジョブの状態はサーバが正なので、画面では作り直さない（型 `types.ts` の値と同じ）。

## 4. 変えたこと

- `services/lifecycles.py`（新規）: 4 つの状態機械（`TrainingRunLifecycle`・`TrainingDecisionLifecycle`・
  `AnalysisJobLifecycle`・`ChangeSetLifecycle`）。状態の値は DB・API の文字列そのまま。
  許されない移り方は `TransitionNotAllowed` になる。
- 状態を直接代入していた箇所を、機械の移り方の呼び出しに置き換えた（`services/training_service.py`・
  `routers/ml.py`・`services/analysis_jobs.py`・`services/circuit_service.py`）。応答の形と値は変えていない。
  学習が例外で終わったときは、まだ学習中のときだけ失敗にする（`fail_if_running`）。
- 起動時に、前回のプロセスで学習中のまま残った学習ジョブを失敗にする（`training_service.recover_interrupted_runs`。
  `main.py` の lifespan から呼ぶ）。履歴のログに「アプリの終了・再起動で学習が中断されました」と残す。
  学習はプロセスの中のスレッドで回す前提（EXE・1 台のサーバ。DD-0039 と同じ）なので、起動時に生きている学習は無い。
- 状態図: `python scripts/state_diagrams.py` が定義から [docs/state-machines.md](../state-machines.md) を作る。
  `tests/test_lifecycles.py` が文書と定義の食い違いを見つける。
- 画面（`frontend/src/hooks/desktopSession.ts`・`App.tsx`）: デスクトップ版のつながりを
  `web → connected → shutting_down → terminated`（終了に失敗すれば connected に戻る）の 1 つの状態にした。
  画面の見た目・動きは変えていない。
- 依存: `requirements.txt` に `python-statemachine>=3.2,<4`、`constraints.txt` に `python-statemachine==3.2.2`。

## 5. 確認

| 対象 | 方法 | 結果 |
|---|---|---|
| 状態の移り方 | pytest（`tests/test_lifecycles.py`） | 許される移り方だけが通り、終わった状態からは動かない。解析ジョブは中止の印と残りの図面で中止・完了が分かれる |
| 再起動で残った学習 | pytest（同上） | running の行が失敗になり、次の学習を始められる。終わった行は変えない |
| 状態図 | pytest（同上）・`scripts/state_diagrams.py --check` | 文書が定義と同じ |
| 既存の動き | 既存の pytest 全部・vitest 全部・ruff・tsc | すべて通る（応答の形・値は変わらない） |
| 画面のつながり | vitest（`desktopSession.test.ts`） | 表に無い移り方は無視し、終了後は戻らない |

## 6. 状態を足すとき

1. `services/lifecycles.py` に状態・移り方を書き、`MACHINES` に登録する。
2. 呼ぶ側では列に直接代入せず、`lifecycles.<機械>(行).<移り方>()` を呼ぶ。
3. `python scripts/state_diagrams.py` で状態図を作り直す。
