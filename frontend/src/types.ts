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
}

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
  updated_at: string | null
}

export type Stats = {
  project_count: number
  symbol_count: number
  connection_count: number
  by_status: Record<string, number>
  by_class: Record<string, number>
}

export const STATUS_LABEL: Record<string, string> = {
  draft: '作業中',
  review: 'レビュー待ち',
  done: '完了',
}
