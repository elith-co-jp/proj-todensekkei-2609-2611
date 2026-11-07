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
  /** メーカー・型式・定格などの属性（DD-0010） */
  attributes?: Attributes
}

export type ConnectionKind = 'wire' | 'sheet_ref'

export type Connection = {
  /** 図面内で一意な配線の参照名（W-0001）。属性の JSON のオブジェクト ID に使う */
  ref?: string | null
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
  /** 長さ・電線種別・断面積などの属性（DD-0010） */
  attributes?: Attributes
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
  /** コイル（リレーコイル・電磁弁・表示灯）の配線の取り決め（端子 2 個・両方に配線・通り抜け無し）に合わないもの */
  coils?: { count: number; issues: CoilIssue[] }
}

export type CoilIssue = {
  symbol_ref: string
  class_key: string
  label: string
  kind: 'terminals' | 'unwired' | 'through'
  message: string
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
  /** 評価用の図面でのクラス別の値（新モデル・比較対象） */
  per_class?: Record<string, ClassMetric> | null
  baseline_per_class?: Record<string, ClassMetric> | null
  /** 採用判定の基準による推奨 */
  review?: AdoptionReview | null
  log_tail: string | null
  started_at: string | null
  finished_at: string | null
}

export type ClassMetric = {
  label: string
  instances: number
  precision: number
  recall: number
  map50: number
  map50_95: number
}

export type AdoptionReview = {
  /** adopt=採用を勧める / hold=見送りを勧める / caution=学習に使った図面での評価 / unknown=指標なし */
  verdict: 'adopt' | 'hold' | 'caution' | 'unknown'
  reasons: string[]
}

export type ClassStats = {
  done_drawings: number
  thin_threshold: number
  classes: {
    key: string
    label: string
    color: string
    done_count: number
    done_drawings: number
    total_count: number
    total_drawings: number
    thin: boolean
  }[]
}

export type HardDrawings = {
  threshold: number
  drawings: {
    project_id: number
    name: string
    sheet_no: string | null
    f1: number
    ai_count: number
    gold_count: number
    missed: number
    false_detections: number
    relabeled: number
    hard: boolean
  }[]
}


export const STATUS_LABEL: Record<string, string> = {
  draft: '作業中',
  review: 'レビュー待ち',
  done: '完了',
}

// ---------- 回路シミュレーション ----------
export type CircuitSymbolRef = { project_id: number; symbol_ref: string }

export type CircuitDiagnostic = {
  kind: string
  message: string
  page?: string | null
  project_id?: number
  symbol_ref?: string
  device?: string
}

export type CircuitSummary = {
  engine: 'ngspice' | 'builtin'
  pages: { id: number; name: string }[]
  element_count: number
  node_count: number
  supplies: string[]
  coils: { device: string; symbols: CircuitSymbolRef[]; contacts: (CircuitSymbolRef & { kind: 'a' | 'b' })[] }[]
  inputs: { device: string; contacts: (CircuitSymbolRef & { kind: 'a' | 'b' })[] }[]
  diagnostics: CircuitDiagnostic[]
}

export type CircuitBox = { cx: number; cy: number; w: number; h: number }

export type CoilWiringSplitResult = {
  symbols: SymbolBox[]
  connections: Connection[]
  counts: { split: number; multiple: number; none: number; has_terminals: number }
}

export type CircuitOverlay = {
  project_id: number
  wires: { x1: number; y1: number; x2: number; y2: number; live: boolean }[]
  contacts: (CircuitBox & CircuitSymbolRef & { device: string; kind: 'a' | 'b'; closed: boolean; label: string | null })[]
  coils: (CircuitBox & { device: string; symbol_ref: string; energized: boolean })[]
}

export type CircuitResult = CircuitSummary & {
  steps: number
  warnings: string[]
  coil_states: Record<string, { energized: boolean; voltage: number }>
  overlay: CircuitOverlay[]
}

// ---------- 属性（DD-0010） ----------
export type AttributeValue = string | number | boolean | null
export type Attributes = Record<string, AttributeValue>

export type AttributeDef = { key: string; label: string; type: 'text' | 'number'; unit: string | null }

export type AttributeDefinitions = {
  symbol: { common: AttributeDef[]; by_class: Record<string, AttributeDef[]> }
  wire: AttributeDef[]
}

export type AttributeImportReport = {
  updated: number
  unchanged: number
  not_found: string[]
  duplicates: string[]
  type_mismatch: string[]
  invalid: string[]
}
