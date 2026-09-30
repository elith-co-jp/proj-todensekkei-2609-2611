# TodenYOLO

シーケンス図（プラント制御回路図）に対して、**シンボルを bbox で**、**シンボル間の配線を from-to で**
アノテーションし、**YOLO 学習可能な形式**で出力するツールです。
別の担当者が入力したデータを ZIP でやり取りし、別環境で**完全に復元**できます。

React/TypeScript + FastAPI 構成。

## ドキュメント

| 文書 | 内容 |
|------|------|
| [docs/SPECIFICATION.md](docs/SPECIFICATION.md) | 機能仕様、API、画面仕様 |
| [docs/ER_DIAGRAM.md](docs/ER_DIAGRAM.md) | ER 図、データ設計で意識した既知リスクへの対応 |
| [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md) | 出力 ZIP の構成、bundle.json のスキーマ、YOLO 形式の詳細 |

---

## 機能一覧

| 機能 | 概要 |
|------|------|
| **図面登録** | PDF / PNG / JPEG / BMP をアップロード。PDF は全ページを PNG 化し、1 ページ = 1 プロジェクトとして登録。画像は sha256 で重複排除しファイル実体として保存 |
| **シンボルのアノテーション** | クラス別の色でバウンディングボックスを描画・移動・リサイズ。座標は正規化 (cx, cy, w, h) で保持し YOLO 形式と一致 |
| **端子（任意）** | シンボル内をクリックして端子を配置。端子名（13 / NC1 等）を編集可能 |
| **配線 (from-to)** | 始点 → 終点をクリックして登録。端子を選べば端子単位、矩形本体を選べばシンボル単位。電線番号・シート間参照も記録 |
| **クラスマスタ** | `symbol_classes` テーブルで管理し `yolo_index` を明示採番。未知クラスはインポート時に末尾へ自動登録 |
| **Undo / Redo** | 直近 100 操作を巻き戻し可能（Ctrl+Z / Ctrl+Shift+Z） |
| **自動保存** | シンボル・端子・配線・図面情報の編集停止から約 0.4 秒後に直列保存。Ctrl+S / Cmd+S で即時確定 |
| **フォーカスワークスペース** | 編集画面では共通サイドバーを外して図面領域を最大化。詳細パネルを開閉し、前後ページへ直接移動可能 |
| **初回ツアー** | 初回アクセス時に PDF 登録、入力モード、自動保存、図面ズームを順番に案内。サイドバーから再表示可能 |
| **開閉式サイドバー** | アイコンボタンで展開／折りたたみ。折りたたみ時も各画面とツアーへアクセス可能 |
| **図面専用ズーム** | 図面キャンバス上のホイール、または − / 全体 / ＋ で図面だけを拡大縮小。ブラウザ標準の表示倍率も利用可能 |
| **AI 改善サイクル** | モデル登録 → 推論 → 結果を JSON で確認 → エディタで修正 → データ蓄積 → YOLO 学習 → 新旧モデルを蓄積データで精度比較し採用可否を選択。ultralytics が無い環境では外部実行（predict 出力 ZIP の取込 / best.pt の登録）に誘導 |
| **YOLO エクスポート** | `data.yaml` + 全画像を `dataset/images/train` に出力（val 分割はしない）。配線 CSV／netlist と完全復元用 bundle.json を 1 つの ZIP に同梱 |
| **配線の出力** | YOLO 形式では表現できないため `connections/connections.csv` と `connections/netlist.json` に別出力 |
| **完全復元インポート** | `bundle.json` を含む ZIP はシンボル・端子・配線・図面情報・クラス定義まで復元。素の YOLO ZIP は矩形のみ取り込み |
| **安全対策** | パストラバーサル拒否、`__MACOSX` / `.DS_Store` 除外、ファイル数・展開サイズ・圧縮率の上限（ZIP 爆弾対策） |

### シンボルクラス（初期値）

要件定義書 別紙B の暫定 12 クラス。`class id` は `classes.txt` の行番号（0 始まり）と一致します。

| id | key | ラベル |
|----|-----|--------|
| 0 | `relay_coil` | リレーコイル |
| 1 | `contact_a` | a接点（メーク） |
| 2 | `contact_b` | b接点（ブレーク） |
| 3 | `terminal` | 端子 |
| 4 | `junction` | 分岐点 |
| 5 | `diode` | ダイオード |
| 6 | `solenoid` | 電磁弁・ソレノイド |
| 7 | `connector` | コネクタ |
| 8 | `lamp` | 表示灯 |
| 9 | `push_button` | 押しボタン |
| 10 | `power_bus` | 電源母線端 |
| 11 | `other` | その他 |

---

## 起動手順

### Docker（推奨）

Docker Compose で frontend / backend / db の3サービスをまとめて起動します。

```bash
docker compose up --build -d
```

- UI: http://localhost:3000
- API / OpenAPI: http://localhost:8010/docs
- DB: PostgreSQL 17（Compose 内部ネットワークのみ）

起動状態は `docker compose ps`、ログは `docker compose logs -f` で確認できます。
停止は `docker compose down`、保存データも削除して初期化する場合のみ
`docker compose down -v` を実行してください。

パスワード等を変更する場合は `.env.example` を `.env` にコピーして編集します。
PostgreSQL のデータと変換後のページ画像は Docker volume に永続化されます。

### PDF の登録とページ単位の作業

1. http://localhost:3000 を開き、「PDF / 図面を登録」を押す。
2. `sample/Tif画像のPDF化_検証用_30枚_0722.pdf` を選択する。
3. 30ページが `... - 001` から `... - 030` の個別図面として登録される。
4. 一覧の図面名または編集ボタンから、ページ画像へアノテーションする。
5. 編集画面上部の前後ボタンでページを移動し、右上のボタンで詳細パネルを開閉する。

編集内容は入力停止後に自動保存されます。ヘッダーの「自動保存済み」を確認してください。
図面の拡大・縮小・全体表示はキャンバス左下、操作ヒントは右下に集約しています。
操作方法は「アノテーション手順」で、実図面を含まない匿名の画面イメージとともに確認できます。
キャンバスをフォーカスすると、`Enter` で矩形・端子・配線を登録し、矢印キーで矩形を調整できます。

PDF は最大 256 MiB / 500ページ、150 DPI で画像化します。画像単体のアップロードも従来どおり利用できます。

### Windows 単一 exe（Python 非同梱の PC 向け）

配布先の Windows PC に **Python も Node も入っていなくても**、exe だけで動きます。
PDF 取り込みも exe 内蔵の PyMuPDF で処理するため、Poppler の別途インストールは不要です。
推論・学習用の Ultralytics、CPU 版 PyTorch、学習開始用 YOLOv8n 重みに加え、
シーケンス図向けに学習済みの初期推論モデル（`yolo11n_all_symbols_best.pt`）も同梱します。
初回起動時に同梱モデルが使用中の推論モデルとして自動登録されるので、そのまま推論を実行できます。
アノテーションを蓄積して学習すると、蓄積データでの新旧モデルの精度比較が表示され、
採用を選んだときだけ生成したモデルが使用中に切り替わります。
CPU での学習は GPU より時間がかかります。
配布版は [AGPL-3.0](LICENSE) の条件に従い、[公開ソースコード](https://github.com/elith-co-jp/proj-todensekkei-2609-2611)
とともに提供します。Drive 上の EXE の説明欄には、ビルドに使用したコミットへのリンクを記載します。

**ビルド**（Python 3.11+ と Node.js 18+ を入れた Windows で 1 回だけ実行）:

```bat
build_windows.bat
```

短い学習・推論の検証が通ると `dist\TodenYOLO.exe` が生成されます。PyInstaller はクロスビルドできないため、
Windows 用 exe は必ず Windows 上でビルドしてください（spec: `seq-annotator.spec`）。
ビルド時のみ、公式 Ultralytics リリースから `yolov8n.pt` を取得して SHA-256 を照合します。
初期推論モデルはリポジトリの `assets/models/` に同梱されているため、配布先でモデルをダウンロードする必要はありません。

**配布・起動**:

1. `TodenYOLO.exe` を配布先 PC の任意のフォルダに置く。
2. ダブルクリックすると自動でローカルサーバが立ち上がり、既定ブラウザに UI が開く。
3. 終了するときは画面の「アプリを終了」を押す。ブラウザを閉じたままにした場合も、
   一定時間後にローカルサーバは自動終了します。
4. データ（`data/` … DB と画像）は exe と同じフォルダに作られます。フォルダごと
   コピーすれば別 PC へ移行でき、削除すれば初期化されます。

アプリのサーバは `127.0.0.1` のみで待ち受けます。空きポート（8010 など）を自動選択します。

#### GitHub Actions による自動ビルドと Google Drive への自動アップロード

`.github/workflows/build-windows-exe.yml` は **main へのマージ（push）時**と手動実行
（Actions タブの「Build Windows EXE」→「Run workflow」）で Windows ランナー上に
`TodenYOLO.exe` をビルドします。成果物は GitHub Artifacts（7 日保存）と、
[指定の Google Drive フォルダ](https://drive.google.com/drive/folders/1RXQF7syvhMOmS86IB3bhJZOxgFnlKqZq)
にアップロードされます（同名ファイルがあれば上書き更新）。Drive への
アップロードに失敗した場合、ワークフローも失敗します。
アップロード前に凍結 EXE を起動し、内蔵モデルからの短い学習と学習済みモデルによる
推論を検証します。手動実行で `publish=false` を選ぶと、配布せずにビルド検証できます。

**初回セットアップ**（Google Drive へのアップロードを有効にするには）:

1. Google Cloud Console でサービスアカウントを作成し、Google Drive API を
   有効化して JSON 鍵を発行する。
2. 上記の共有ドライブ内フォルダにサービスアカウントのメールアドレス
   （`xxx@yyy.iam.gserviceaccount.com`）を追加し、ファイルを作成・更新できる権限を付与する。
3. リポジトリの **Settings → Secrets and variables → Actions → New repository secret** に
   `GOOGLE_DRIVE_CREDENTIALS_JSON` として JSON 鍵の中身全文を登録する。

鍵はリポジトリやチャットに貼らないでください。Secret が未設定の場合はワークフローが
失敗し、Drive への未アップロードを成功と誤認しないようにします。フォルダ ID は
ワークフローに設定済みです。同名ファイルの上書きはサービスアカウント自身が
作成したファイルに限るため、フォルダ内に手動で同名ファイルを置かないでください。

開発時は次のコマンドで同じ挙動を確認できます。

```bash
cd frontend && npm run build && cd ..
python desktop.py
```

### 開発（フロント／API を別プロセスで）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8010      # API: http://localhost:8010

cd frontend && npm install && npm run dev  # UI:  http://localhost:5173
```

Vite の dev server は `/api` を既定で `http://localhost:8010` にプロキシします。
接続先を変える場合は `frontend/.env.example` を `frontend/.env` にコピーし、
`VITE_API_PROXY_TARGET` を変更してください。
PDF 取り込みは `requirements.txt` の PyMuPDF で処理するため、追加のインストールは不要です。
（PyMuPDF が無い環境では従来どおり Poppler `pdfinfo` / `pdftoppm` にフォールバックします。）

### 単体運用（フロントを同梱して 1 プロセスで配信）

```bash
cd frontend && npm run build && cd ..
uvicorn main:app --port 8010               # http://localhost:8010 で UI と API
```

`frontend/dist` があれば FastAPI が SPA を配信します。

### 環境変数

| 変数 | 既定値 | 内容 |
|------|--------|------|
| `SEQANNO_DATA_DIR` | `./data` | DB と画像の保存先 |
| `SEQANNO_DATABASE_URL` | `sqlite:///<data>/seqanno.db` | PostgreSQL 等へ差し替え可能（Docker は PostgreSQL） |
| `SEQANNO_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 許可オリジン |
| `VITE_API_PROXY_TARGET` | `http://localhost:8010` | Vite 開発サーバーの API 接続先（`frontend/.env`） |

---

## AI 改善サイクル（推論 → 修正 → 蓄積 → 学習 → 改善）

`/ml` 画面（サイドバー「AI 改善サイクル」）で一連の流れを回します。

1. **モデル登録** — YOLO の `.pt`（学習済み `best.pt` 等）をアップロード。「使用中」のモデルが推論と学習のベースになります
2. **推論** — 対象図面と信頼度しきい値を選んで実行。検出結果は図面ごとに最新だけ保存され、画面で JSON 確認できます
3. **修正** — エディタの「AI 推論」ボタンで検出を破線枠のシンボルとして取り込み、誤検出・漏れを人が修正
4. **蓄積** — 修正済みシンボルは `origin=inference` と信頼度つきで保存され、次の学習データになります
5. **学習** — 蓄積データで YOLO を再学習。完了すると `best.pt` が新モデルとして登録され、蓄積データでの新旧モデルの精度比較が表示されます
6. **採用判定** — 比較結果を見て「採用」を選ぶと新モデルが使用中に切り替わり、「見送り」なら現行モデルを継続使用します（あとからモデル管理で切り替えも可能）

### ultralytics が無い開発環境

推論・学習の API は `ultralytics` 未導入でもサーバは起動し、実行時に外部実行への案内を返します。
その場合の流れ:

```bash
# 1. このツールで ZIP をエクスポート（画像名は p<図面ID>_*.png）
# 2. GPU 環境で推論し、labels を ZIP 化して「推論結果を取り込む」から読み込み
yolo predict model=best.pt source=dataset_root/dataset/images/train save_txt=True save_conf=True
# 3. 外部で学習した best.pt を「モデル登録」からアップロード
yolo detect train data=dataset_root/data.yaml model=yolov8n.pt epochs=100 imgsz=1280
```

開発環境でツール内実行するには追加依存が必要です（GPU 推奨）。Windows 配布 EXE には
CPU 版の依存と初期モデルが同梱されています。

```bash
pip install -r requirements-ml.txt   # ultralytics
```

## 学習の実行（外部環境）

```bash
unzip seqanno_export_*.zip -d dataset_root
cd dataset_root
yolo detect train data=data.yaml model=yolov8n.pt epochs=100 imgsz=1280
```

---

## テスト

```bash
python3 -m pytest        # API・PDF 分割・YOLO 形式・往復復元・セキュリティ
cd frontend && npm test        # UIの純粋ロジック
cd frontend && npm run build   # 型チェック込み
docker compose config --quiet  # Compose 設定検証
```

---

## 設計上のポイント

| 項目 | 本ツールの方針 |
|------|----------------|
| 座標保持 | 正規化 (cx, cy, w, h) をそのまま DB へ（YOLO と同じ表現で変換コストゼロ） |
| 保存方式 | 全ボックス一括置換（差分管理なし。同時編集は後勝ち） |
| ZIP レイアウト | `dataset/` + `data.yaml` の ultralytics 標準構成。復元は自ツール出力の `bundle.json` 付き ZIP のみ |
| ZIP 安全対策 | パストラバーサル・ZIP 爆弾対策あり |
| class id | `symbol_classes` マスタで `yolo_index` を UK 管理（配列添字だけに依存しない） |
| 画像保存 | ファイル保存 + sha256、DB はメタデータのみ（base64 で肥大化させない） |
| data.yaml | 生成する（全画像を train に出力。val 分割はしない） |
| Undo | 直近 100 操作の Undo / Redo |
| ズーム／パン | 図面だけをホイール拡大縮小・ドラッグ移動。システム UI は固定 |
| 関係（from-to） | 端子単位／シンボル単位の配線を登録・出力 |
