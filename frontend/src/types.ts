export type SymbolClass = {
  id: number
  key: string
  label: string
  yolo_index: number
  color: string
  is_active: boolean
  sort_order: number
}

export type Terminal = {
  ref: string
  name: string
  /** 画像に対する正規化座標 (0-1) */
  tx: number
  ty: number
  /** 登録の経緯。inference=AI の解析から取り込んだ候補 */
  origin?: SymbolOrigin
}

export type SymbolOrigin = 'manual' | 'inference'

export type SymbolBox = {
  ref: string
  class_key: string
  class_label?: string
  yolo_index?: number
  label: string | null
  /** すべて正規化座標 (0-1)。YOLO 形式と同じ表現 */
  cx: number
  cy: number
  w: number
  h: number
  note: string | null
  /** 登録の経緯。manual=手入力 / inference=AI 推論から生成 */
  origin?: SymbolOrigin
  /** 推論由来のときの信頼度 (0-1) */
  confidence?: number | null
  terminals: Terminal[]
}

export type ConnectionKind = 'wire' | 'sheet_ref'

export type Connection = {
  from_symbol_ref: string
  from_terminal_ref: string | null
  to_symbol_ref: string
  to_terminal_ref: string | null
  wire_no: string | null
  net_id: string | null
  kind: ConnectionKind
  external_ref: string | null
  note: string | null
  /** 登録の経緯。inference=AI の解析から取り込んだ候補 */
  origin?: SymbolOrigin
}

export type ProjectDetail = {
  id: number
  name: string
  sheet_no: string | null
  page_no: string | null
  revision: string | null
  source_file: string | null
  image_width: number
  image_height: number
  status: string
  assignee: string | null
  note: string | null
  symbols: SymbolBox[]
  connections: Connection[]
  images: { filename: string; sha256: string; width: number; height: number }[]
}

export type ProjectRow = {
  id: number
  name: string
  sheet_no: string | null
  page_no: string | null
  revision: string | null
  status: string
  assignee: string | null
  image_width: number
  image_height: number
  symbol_count: number
  connection_count: number
  terminal_count: number
  prediction_count: number
  /** AI 解析（構造解析）で検出した配線の本数。未解析なら null */
  analysis_wire_count: number | null
  analysis_connection_count: number | null
  updated_at: string | null
}

export type Stats = {
  project_count: number
  symbol_count: number
  connection_count: number
  by_status: Record<string, number>
  by_class: Record<string, number>
  by_origin: Record<string, number>
  prediction_count: number
}

// ---------- AI 改善サイクル ----------
export type MlModel = {
  id: number
  name: string
  version: number
  file_name: string
  sha256: string
  size_bytes: number
  source: 'upload' | 'trained' | 'bundled'
  is_active: boolean
  metrics: Record<string, string> | null
  note: string | null
  created_at: string | null
}

export type MlStatus = {
  ultralytics: boolean
  active_model: MlModel | null
  model_count: number
  training_running: TrainingRun | null
}

export type Detection = {
  class_key: string
  class_label: string
  yolo_index: number
  cx: number
  cy: number
  w: number
  h: number
  confidence: number | null
}

export type ProjectPredictions = {
  project_id: number
  name: string
  model_label: string | null
  model_id: number | null
  count: number
  detections: Detection[]
  /** シンボル対象外として除外した大きな枠（作図領域・DI範囲・雲マーク・LOC など） */
  regions?: Detection[]
  region_count?: number
}

export type PredictionSummary = {
  project_id: number
  count: number
  model_label: string | null
}

export type InferenceRunResult = {
  project_id: number
  name: string
  detections: number
  region_count?: number
  skipped_images?: number
  structure_available?: boolean
  wire_count?: number
  connection_count?: number
  warnings?: string[]
}

export type StructureResult = {
  status: 'completed' | 'partial'
  source: { project_id: number; image_sha256: string }
  image_size: { width: number; height: number }
  wires: { id: string; polyline: [number, number][] }[]
  symbols: {
    id: string
    class_name: string
    class_label: string
    label?: string
    bbox: { x0: number; y0: number; x1: number; y1: number }
  }[]
  from_to: { connection_count: number; connections: {
    id: string
    from: { symbol_id: string; label: string; terminal_name: string }
    to: { symbol_id: string; label: string; terminal_name: string }
  }[] }
  warnings: string[]
}

export type InferenceRunResponse = {
  model: MlModel
  conf: number
  connector_conf?: number | null
  results: InferenceRunResult[]
  detection_count: number
}

export type InferenceImportResult = {
  results: InferenceRunResult[]
  unmatched_files: string[]
  missing_project_ids: number[]
  count: number
}

/** エディタに足す AI の候補。既存のシンボル・端子・配線は含まない（追加分だけ） */
export type AiImportResult = {
  /** 取り込み元。structure=AI 解析（端子・配線あり）/ predictions=推論のシンボルだけ / null=結果なし */
  source: 'structure' | 'predictions' | null
  symbols: SymbolBox[]
  /** 既存のシンボルに足す端子 */
  terminals: (Terminal & { symbol_ref: string })[]
  connections: Connection[]
  counts: {
    /** AI の候補と重なった既存シンボルの数（追加しない） */
    existing_symbols: number
    region_excluded: number
    duplicate_dots: number
    text_zone: number
  }
}

export type WiringReviewCounts = {
  ai_count: number
  imported: boolean
  kept: number
  deleted: number
  manual_added: number
}

export type ReviewSummary = {
  project_id: number
  status: 'draft' | 'review' | 'done'
  in_next_training: boolean
  ai_detection_count: number
  region_excluded_count: number
  imported: boolean
  kept: number
  relabeled: number
  deleted: number
  manual_added: number
  /** AI 解析の端子・配線の確認状況。AI 解析の結果が無い図面は null */
  wiring?: { terminals: WiringReviewCounts; connections: WiringReviewCounts } | null
}

export type SheetNumberCandidate = {
  text: string
  score: number
  region: 'title_block' | 'margin'
}

export type EvalScope = {
  mode: 'holdout' | 'train'
  train_project_ids: number[]
  val_project_ids: number[]
}

export type TrainingRun = {
  id: number
  status: 'running' | 'success' | 'failed'
  project_ids: number[]
  image_count: number
  epochs: number
  imgsz: number
  base_model: string | null
  result_model_id: number | null
  result_model: MlModel | null
  metrics: Record<string, string> | null
  /** 比較対象（旧モデル）を蓄積データで評価した指標 */
  baseline_metrics: Record<string, string> | null
  baseline_label: string | null
  /** 採用判定: pending=採用待ち / adopted=採用 / rejected=見送り */
  decision: 'pending' | 'adopted' | 'rejected' | null
  eval_scope: EvalScope | null
  log_tail: string | null
  started_at: string | null
  finished_at: string | null
}

export const STATUS_LABEL: Record<string, string> = {
  draft: '作業中',
  review: 'レビュー待ち',
  done: '完了',
}
