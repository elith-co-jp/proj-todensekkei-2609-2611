# 仕様書

## 1. 目的

シーケンス図（プラント制御回路図）の正解データを人手で整備するためのツール。
以下の 2 種類のアノテーションを 1 つの画面で行い、機械学習に投入できる形式で出力する。

1. **シンボル**：バウンディングボックス（クラス付き）
2. **配線 (from-to)**：シンボル間、または端子間の接続関係

東電設計「シーケンス図電子化 PoC」の要件定義書 論点②（正解データの整備）に対応する。

## 2. 構成

| 層 | 技術 |
|----|------|
| フロントエンド | React 19 / TypeScript / Tailwind CSS 3 / Vite 6 / react-router 7 |
| バックエンド | FastAPI / SQLAlchemy 2 / Pydantic 2 |
| DB | PostgreSQL 17（Docker）／ SQLite（ローカル開発時の既定） |
| 画像 | `data/images/<sha256>.png`（PNG 正規化・重複排除） |

Docker Compose は `frontend`（Nginx）、`backend`（FastAPI + Poppler）、`db`（PostgreSQL）の
3サービスを起動する。ブラウザからの `/api` は frontend が backend へリバースプロキシする。

ディレクトリ配置（`routers/` `services/` `models.py` `schemas.py` `main.py` `tests/` `frontend/`）。

## 3. 画面仕様

### 3.1 図面一覧（`/`）

| 項目 | 内容 |
|------|------|
| 役割 | 図面の登録、進捗確認、一括出力・削除 |
| 主な操作 | 「PDF / 図面を登録」で PDF または画像をアップロード（複数可）／PDF は全ページを自動分割／行クリックで編集画面へ／チェックボックスは Shift で範囲選択 |
| 表示 | 登録図面数・シンボル数・配線数・完了数の KPI、図面ごとのシンボル／端子／配線件数と状態 |
| 利用 API | `GET /api/projects` `GET /api/stats` `POST /api/projects` `POST /api/projects/bulk-delete` `POST /api/export` |

一覧・データ受け渡し・ガイドで共通のサイドバーは展開／折りたたみ可能。編集画面は図面領域を
最大化する専用フォーカスレイアウトとし、一覧へ戻る導線を編集ヘッダーに表示する。
初回アクセス時は4ステップのツアーを表示し、完了後もサイドバーの
「ツアーガイドを表示」から再表示できる。PCでは表、SPではカードを使い、ページ全体の横スクロールを避ける。

### 3.2 アノテーションエディタ（`/projects/:id`）

編集ヘッダーには現在位置（現在ページ / 全図面数）、前後の図面への移動、個別出力、自動保存状態を表示する。
ページ移動時に未送信の編集が残っている場合も、対象プロジェクトIDを保持した保存キューから送信する。
詳細パネルは開閉可能で、PCでは右側、SPではキャンバス上のオーバーレイとして表示する。
パネルを閉じると図面全体表示を再計算してキャンバスを最大限利用する。

| モード | キー | 操作 |
|--------|------|------|
| 選択・移動 | `V` | 矩形の選択、ドラッグ移動、四隅ハンドルでリサイズ |
| シンボル描画 | `B` | 図面上をドラッグして矩形を作成（作成前にクラスを選択）。キャンバス上の `Enter` でも中央に作成 |
| 端子を置く | `T` | シンボル内側をクリックして端子を追加。選択中シンボルはキャンバス上の `Enter` でも追加 |
| 配線 (from-to) | `C` | 始点 → 終点をクリック。端子の丸を選べば端子単位、矩形本体ならシンボル単位。選択中シンボルは `Enter` で順に確定可能 |

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

- **シンボル**：一覧、クラス変更、ラベル入力、端子名の編集・削除、正規化座標の表示
- **配線**：一覧、電線番号、種別（配線／シート間参照）、参照先の入力、削除
- **図面情報**：名称・シート番号・頁・改訂・状態・担当・メモ、画像のサイズと sha256

編集内容は入力停止から約0.4秒後に**自動保存**する。通信は直列化し、保存中に追加された変更は
最新状態へまとめて次のリクエストで保存する。保存は**シンボルと配線をまとめて置き換える**方式
（送られた内容で全置換する）。図面情報も同じ待機時間で自動保存する。
失敗した保存はプロジェクト別に保持し、自動処理では各世代を1回だけ試行する。新しい編集または保存ボタンで
再試行し、ページ移動後も未保存のアノテーションと図面情報を失わない。
差分管理をしないため実装は単純だが、同時編集は後勝ちになる。

ブラウザ標準の拡大縮小は維持する。図面キャンバス内ではホイールとキャンバス左下の
`縮小 / 全体 / 拡大` で図面だけを拡大縮小できる。マウス・ペン・タッチの単一ポインター操作に対応する。
主要ボタンは24 CSS px以上の操作対象を確保し、キーボードフォーカスを可視化する。

### 3.3 エクスポート／インポート（`/export`）

対象図面を選択して ZIP を出力。全画像は `dataset/images/train` に出力し、train/val 分割はしない。
`project_<id>/` のような別レイアウトは出力しない。
ZIP を選択してインポートすると、`bundle.json` の有無で復元内容が変わる（[DATA_FORMAT.md](DATA_FORMAT.md) 参照）。
クラス定義の一覧と、出力される ZIP の構成、学習コマンド例を同画面に表示する。

### 3.4 アノテーション手順（`/guide`）

5 ステップの手順書とキーボード操作一覧、複数人で分担する場合のデータ受け渡し手順。
実際の図面は掲載せず、匿名の操作画面モックで PDF 登録、描画モード、自動保存、図面ズームを説明する。

### 3.5 AI 改善サイクル（`/ml`）

「推論 → 結果表示（JSON）→ 修正 → 蓄積 → 学習 → 改善」のサイクルを回す画面。

- **モデル管理**: YOLO の `.pt` をアップロードして登録。`is_active` のモデルが推論・学習のベース。学習で生成された `best.pt` は自動登録されるが、適用は採用判定で選ぶ
- **推論**: 対象図面と信頼度しきい値を指定して実行。検出は `predictions` テーブルに図面単位で「最新だけ」保持し、同画面で JSON 表示できる
- **外部推論の取込**: `ultralytics` 未導入環境では、外部で `yolo predict --save-txt --save-conf` した labels ZIP を取り込む。エクスポート画像名（`p<図面ID>_*.png`）と同名の `p<図面ID>_*.txt` を自動で図面へ対応づける
- **修正**: エディタの「AI 推論」ボタンで検出を破線枠のシンボルとして取り込み、通常のシンボルと同じ操作で修正。保存時に `origin=inference` と信頼度が付く
- **学習**: 蓄積アノテーションから `data/training/run_<id>/dataset` を生成し、`ultralytics` で学習（バックグラウンドスレッド）。履歴にメトリクス（mAP 等）とログ末尾を保持。完了時に新旧モデルを同じ蓄積データで評価し、比較指標とベースライン名を記録して採用待ち（`decision=pending`）にする
- **Windows 配布版**: CPU 版 PyTorch・Ultralytics・学習開始用の検証済み YOLOv8n 重み・学習済みの初期推論モデル（`assets/models/yolo11n_all_symbols_best.pt`）を単一 EXE に同梱。モデル未登録の初回起動時に同梱モデルを使用中の推論モデルとして自動登録する。初回学習は使用中のモデル（同梱モデル等）をベースに行われ、登録モデルが無い環境では内蔵 yolov8n 重みが起点になる。学習済み `best.pt` は採用判定で推論モデルになる。シーケンス図向け未学習の初期重みを推論モデルとして自動登録しない
- **外部実行への誘導**: `ultralytics` が無い場合、推論・学習 API は日本語メッセージで外部フロー（エクスポート → 外部 predict/学習 → 取込）を案内する

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
| `POST` | `/api/projects` | multipart `files`。画像は1ファイル、PDFは1ページ = 1プロジェクト。`{project_ids, count}` |
| `GET` | `/api/projects?q=` | 一覧。`q` が数字なら ID 一致、それ以外は名称・シート番号の部分一致 |
| `GET` | `/api/projects/{id}` | 詳細（シンボル・端子・配線を含む） |
| `PUT` | `/api/projects/{id}/meta` | 図面情報の更新 |
| `PUT` | `/api/projects/{id}/annotations` | **一括置換**。`{symbols[], connections[]}` |
| `DELETE` | `/api/projects/{id}` | 削除（画像・シンボル・配線を連鎖削除） |
| `POST` | `/api/projects/bulk-delete` | `{ids}` |
| `GET` | `/api/projects/{id}/image` | PNG を返す |

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
| `GET` | `/api/ml/projects/{id}/predictions` | 図面の最新推論結果（JSON。クラス名・信頼度つき） |
| `POST` | `/api/ml/training/run` | `{project_ids[], only_done, epochs, imgsz, base_model}`。バックグラウンドで学習 |
| `GET` | `/api/ml/training/runs` | 学習履歴（最新 50 件。メトリクス・ログ末尾つき） |
| `GET` | `/api/ml/training/runs/{id}` | 学習ジョブの状態・メトリクス・新旧比較・ログ |
| `POST` | `/api/ml/training/runs/{id}/decision` | `{decision: adopt\|reject}`。採用で成果物モデルを使用中に切替え |

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
- `origin` は `manual`（省略時）または `inference`（AI 推論から取り込んだシンボル）。
  `inference` の場合は検出時の `confidence`（0〜1）を保持でき、学習データの出自追跡に使う。

## 5. 非機能

| 項目 | 内容 |
|------|------|
| 認証 | なし（ローカル運用前提）。必要ならセッション認証を追加する |
| 外部通信 | なし。CORS は `SEQANNO_CORS_ORIGINS` で明示したオリジンのみ |
| アップロード上限 | PDF 256 MiB・500ページ／画像 1 枚 64 MiB／ZIP 展開後 2 GiB・20,000 ファイル・圧縮率 25 倍 |
| PDF 画像化 | Poppler を利用し 150 DPI の PNG に変換。元ファイル名と1始まりのページ番号を保持 |
| 同時編集 | 排他制御なし（後勝ち）。分担する場合は図面単位で分け、ZIP で統合する |
| 機械学習 | Windows 配布 EXE は CPU 版 PyTorch・`requirements-ml.txt`・学習開始用モデルを同梱。開発環境では追加依存を任意導入。未導入環境でもモデル登録・推論結果の取込・JSON 表示は動作し、推論・学習は外部実行フローへ誘導 |

## 6. テスト

`python3 -m pytest` で実行する。

| ファイル | 観点 |
|---|---|
| `tests/test_annotation_api.py` | クラスマスタ、PDFページ分割、CRUD、一括置換、参照解決、座標の丸め、日本語エラー |
| `tests/test_export_import.py` | ZIP 構成、YOLO ラベル形式、data.yaml、全画像 train 出力（val 分割なし）、CSV／netlist、往復復元、素の YOLO 取り込み、セキュリティ（パストラバーサル・`__MACOSX`・非 ZIP） |
| `tests/test_ml_cycle.py` | モデル登録・切替・削除、推論の前提エラー（モデル未登録 / ultralytics 未導入）、推論結果 ZIP 取込（対応付け・置換・無効 ZIP）、推論由来シンボルの保存と統計、学習の前提エラー |
