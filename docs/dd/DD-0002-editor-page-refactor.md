# DD-0002: アノテーションエディタの構造リファクタ

- 状態: レビュー中
- 作成日: 2026-10-10
- 関連文書: [SPECIFICATION.md](../SPECIFICATION.md)、[DD-0001](DD-0001-annotation-layer-visibility.md)

## 1. 背景

アノテーションエディタ（EditorPage.tsx）は機能追加を重ねた結果、単一ファイルで約2,400行に達し、純粋ロジック・UI・キーボード処理・保存キュー管理が一つのコンポーネントに混在していた。今後の変更（例: issue #24 の精度改善ループ整備）の変更コストと回帰リスクを下げるため、振る舞いを変えずに内部構造を分割する。

## 2. 目的

- EditorPage.tsx を読みやすい規模に縮小し、責務ごとのファイルに分離する。
- 外部から見える挙動・API・保存ペイロード・Undo/Redo・ショートカットは一切変更しない（構造変更のみ）。

## 3. スコープ

- 対象: `frontend/src/pages/EditorPage.tsx` および新設する `frontend/src/pages/editor/` 配下。
- スコープ外: バックエンド（最大ファイル約500行で問題なし）、他ページ（MlOpsPage 約830行）は対象外。

## 4. 変更案の概要

`frontend/src/pages/editor/` を新設し、以下の構成に分割する。

1. `model.ts`: 純粋ロジック — ペイロード構築（buildAnnotationPayload/buildMetaForm）、永続データの型ガード、fitInside/clamp01/nextSymbolRef、定数（MODES/INSPECTOR_TABS/SAVE_STATUS_LABELS/MIN_BOX/推論取り込み許容誤差）。
2. `LayerPanel.tsx`: レイヤー管理パネル（クラス行・AI検出・配線・端子の切替、キー遮断ポリシー）。
3. `MetaPanel.tsx`: 図面情報フォーム。
4. `EditorInspector.tsx`: 詳細パネル（タブ切替・シンボル一覧・配線一覧・MetaPanel ホスト）。
5. `EditorHeader.tsx`: ヘッダー（戻る・前後ページ・解析・AI推論・エクスポート・保存状態）。
6. `EditorToolbar.tsx`: モードツールバー・クラス選択・レイヤーボタン・Undo/Redo。
7. `EditorCanvasSvg.tsx`: 図面上のSVGレイヤー（矩形・端子・配線・ドラッグ中の下書き）。
8. `CanvasControls.tsx`: モードバッジ・詳細ボタン・ズームバー・操作ヒント。
9. `saveQueue.ts`: アノテーションと図面情報で同型だった保存キュー状態一式を `SaveQueueStore<T>` に共通化。
10. `useEditorShortcuts.ts`: window/canvas のキーボードショートカットをフックとして集約。

## 5. 検討した代替案

- **components/ へ配置**: pages/editor/ にまとめる方が「EditorPage 専用」であることが明確で、再利用を誤解させないため採用。
- **状態管理ライブラリ導入（Redux 等）**: この規模では依存と学習コストが割に合わないため不採用。props での受け渡しを維持。

## 6. 影響・リスク

- 振る舞い不変が前提。既存 vitest 53件（レイヤー・ショートカット・保存競合の回帰テストを含む）がすべてパスすることを確認。
- ファイル分割により import 境界が増えるのみで、実行時の差はない。

## 7. 未決事項（人への判断）

- なし。

## 8. 検証

- `tsc -b` で型エラーなし、vitest 53/53 パス。
- EditorPage.tsx 2,438行 → 約1,300行に縮小。
