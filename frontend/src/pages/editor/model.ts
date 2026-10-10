import { Cable, CircleDot, MousePointer2, Square } from 'lucide-react'

import type { Connection, ProjectDetail, SymbolBox } from '../../types'

export type Mode = 'select' | 'box' | 'terminal' | 'connect'
export type Corner = 'nw' | 'ne' | 'sw' | 'se'
export type Snapshot = { symbols: SymbolBox[]; connections: Connection[] }
export type SaveStatus = 'pending' | 'saving' | 'saved' | 'error'

export function buildAnnotationPayload(symbols: SymbolBox[], connections: Connection[]) {
  return {
    symbols: symbols.map((symbol) => ({
      ref: symbol.ref,
      class_key: symbol.class_key,
      label: symbol.label,
      cx: symbol.cx,
      cy: symbol.cy,
      w: symbol.w,
      h: symbol.h,
      note: symbol.note,
      origin: symbol.origin ?? 'manual',
      confidence: symbol.confidence ?? null,
      terminals: symbol.terminals.map((terminal) => ({
        ref: terminal.ref,
        name: terminal.name,
        tx: terminal.tx,
        ty: terminal.ty,
      })),
    })),
    connections,
  }
}

export type AnnotationPayload = ReturnType<typeof buildAnnotationPayload>

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function isAnnotationPayload(value: unknown): value is AnnotationPayload {
  if (!isRecord(value) || !Array.isArray(value.symbols) || !Array.isArray(value.connections)) return false
  const validSymbols = value.symbols.every((symbol) => {
    if (!isRecord(symbol) || !Array.isArray(symbol.terminals)) return false
    return (
      typeof symbol.ref === 'string' &&
      typeof symbol.class_key === 'string' &&
      isNullableString(symbol.label) &&
      isFiniteNumber(symbol.cx) &&
      isFiniteNumber(symbol.cy) &&
      isFiniteNumber(symbol.w) &&
      isFiniteNumber(symbol.h) &&
      isNullableString(symbol.note) &&
      (symbol.origin === undefined || symbol.origin === 'manual' || symbol.origin === 'inference') &&
      (symbol.confidence === undefined || symbol.confidence === null || isFiniteNumber(symbol.confidence)) &&
      symbol.terminals.every(
        (terminal) =>
          isRecord(terminal) &&
          typeof terminal.ref === 'string' &&
          typeof terminal.name === 'string' &&
          isFiniteNumber(terminal.tx) &&
          isFiniteNumber(terminal.ty),
      )
    )
  })
  if (!validSymbols) return false
  return value.connections.every(
    (connection) =>
      isRecord(connection) &&
      typeof connection.from_symbol_ref === 'string' &&
      isNullableString(connection.from_terminal_ref) &&
      typeof connection.to_symbol_ref === 'string' &&
      isNullableString(connection.to_terminal_ref) &&
      isNullableString(connection.wire_no) &&
      isNullableString(connection.net_id) &&
      (connection.kind === 'wire' || connection.kind === 'sheet_ref') &&
      isNullableString(connection.external_ref) &&
      isNullableString(connection.note),
  )
}

export function buildMetaForm(project: ProjectDetail) {
  return {
    name: project.name,
    sheet_no: project.sheet_no ?? '',
    page_no: project.page_no ?? '',
    revision: project.revision ?? '',
    status: project.status,
    assignee: project.assignee ?? '',
    note: project.note ?? '',
  }
}

export type MetaForm = ReturnType<typeof buildMetaForm>

export function isMetaForm(value: unknown): value is MetaForm {
  return (
    isRecord(value) &&
    typeof value.name === 'string' &&
    typeof value.sheet_no === 'string' &&
    typeof value.page_no === 'string' &&
    typeof value.revision === 'string' &&
    typeof value.status === 'string' &&
    typeof value.assignee === 'string' &&
    typeof value.note === 'string'
  )
}

export const MODES: { id: Mode; label: string; key: string; icon: typeof Square }[] = [
  { id: 'select', label: '選択・移動', key: 'V', icon: MousePointer2 },
  { id: 'box', label: 'シンボル描画', key: 'B', icon: Square },
  { id: 'terminal', label: '端子を置く', key: 'T', icon: CircleDot },
  { id: 'connect', label: '配線 (from-to)', key: 'C', icon: Cable },
]

export const INSPECTOR_TABS = [
  ['symbols', 'シンボル'],
  ['connections', '配線'],
  ['meta', '図面情報'],
] as const
export type InspectorTab = (typeof INSPECTOR_TABS)[number][0]

export const SAVE_STATUS_LABELS: Record<SaveStatus, { short: string; long: string }> = {
  pending: { short: '未保存', long: '変更を検出' },
  saving: { short: '保存中', long: '自動保存中…' },
  saved: { short: '保存済', long: '自動保存済み' },
  error: { short: '再試行', long: '保存に失敗しました。押すと再試行します' },
}

/** ドラッグ操作の種類 */
export type Drag =
  | { kind: 'none' }
  | { kind: 'pan'; sx: number; sy: number; ox: number; oy: number }
  | { kind: 'draw'; x1: number; y1: number; x2: number; y2: number }
  | { kind: 'move'; ref: string; dx: number; dy: number }
  | { kind: 'resize'; ref: string; corner: Corner }

export const MIN_BOX = 0.002

// AI 推論の取り込み時に「既存シンボルと同一」とみなす許容誤差（正規化座標）
export const INFERENCE_CENTER_TOLERANCE = 0.01
export const INFERENCE_SIZE_TOLERANCE = 0.02

export function clamp01(v: number) {
  return v < 0 ? 0 : v > 1 ? 1 : v
}

/** レイヤー行の表示状態: 全表示 / 全非表示 / 一部非表示 */
export type LayerState = 'on' | 'off' | 'partial'
export function layerStateOf(hidden: number, total: number): LayerState {
  if (total === 0 || hidden === 0) return 'on'
  return hidden === total ? 'off' : 'partial'
}

/** 矩形が画像の外へはみ出さないように中心を寄せる */
export function fitInside(cx: number, cy: number, w: number, h: number) {
  const bw = Math.min(w, 1)
  const bh = Math.min(h, 1)
  return {
    cx: Math.min(1 - bw / 2, Math.max(bw / 2, cx)),
    cy: Math.min(1 - bh / 2, Math.max(bh / 2, cy)),
    w: bw,
    h: bh,
  }
}

export function nextSymbolRef(symbols: SymbolBox[]) {
  let n = symbols.length + 1
  const used = new Set(symbols.map((s) => s.ref))
  while (used.has(`SYM-${String(n).padStart(4, '0')}`)) n += 1
  return `SYM-${String(n).padStart(4, '0')}`
}
