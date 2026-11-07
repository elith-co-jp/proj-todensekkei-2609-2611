import { Cable, CircleDot, MousePointer2, Square } from 'lucide-react'

import type { AiImportResult, Connection, ProjectDetail, SymbolBox } from '../../types'

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
      attributes: symbol.attributes ?? {},
      terminals: symbol.terminals.map((terminal) => ({
        ref: terminal.ref,
        name: terminal.name,
        tx: terminal.tx,
        ty: terminal.ty,
        origin: terminal.origin ?? 'manual',
      })),
    })),
    connections: connections.map((connection) => ({
      ref: connection.ref ?? null,
      from_symbol_ref: connection.from_symbol_ref,
      from_terminal_ref: connection.from_terminal_ref,
      to_symbol_ref: connection.to_symbol_ref,
      to_terminal_ref: connection.to_terminal_ref,
      wire_no: connection.wire_no,
      net_id: connection.net_id,
      kind: connection.kind,
      external_ref: connection.external_ref,
      note: connection.note,
      origin: connection.origin ?? 'manual',
      attributes: connection.attributes ?? {},
    })),
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

function isAttributes(value: unknown) {
  return (
    value === undefined ||
    (isRecord(value) &&
      Object.values(value).every((v) => v === null || ['string', 'number', 'boolean'].includes(typeof v)))
  )
}

function isOrigin(value: unknown) {
  return value === undefined || value === 'manual' || value === 'inference'
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
      isOrigin(symbol.origin) &&
      (symbol.confidence === undefined || symbol.confidence === null || isFiniteNumber(symbol.confidence)) &&
      isAttributes(symbol.attributes) &&
      symbol.terminals.every(
        (terminal) =>
          isRecord(terminal) &&
          typeof terminal.ref === 'string' &&
          typeof terminal.name === 'string' &&
          isFiniteNumber(terminal.tx) &&
          isFiniteNumber(terminal.ty) &&
          isOrigin(terminal.origin),
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
      isNullableString(connection.note) &&
      isOrigin(connection.origin) &&
      (connection.ref === undefined || isNullableString(connection.ref)) &&
      isAttributes(connection.attributes),
  )
}

/** AI の候補（追加分だけ）を、送ったときの内容に足す。既存の要素は変えない */
export function mergeAiImport(symbols: SymbolBox[], connections: Connection[], result: AiImportResult) {
  const extra = new Map<string, SymbolBox['terminals']>()
  for (const { symbol_ref, ...terminal } of result.terminals) {
    extra.set(symbol_ref, [...(extra.get(symbol_ref) ?? []), terminal])
  }
  return {
    symbols: [
      ...symbols.map((s) => (extra.has(s.ref) ? { ...s, terminals: [...s.terminals, ...extra.get(s.ref)!] } : s)),
      ...result.symbols,
    ],
    connections: withWireRefs([...connections, ...result.connections]),
  }
}

function wireRef(n: number) {
  return `W-${String(n).padStart(4, '0')}`
}

/** 参照名の無い配線に W-0001 形式の空き番号を振る（保存し直しても属性が同じ配線に付いたままになる） */
export function withWireRefs(connections: Connection[]): Connection[] {
  const used = new Set(connections.map((c) => c.ref).filter((r): r is string => Boolean(r)))
  let n = 0
  return connections.map((c) => {
    if (c.ref) return c
    do n += 1
    while (used.has(wireRef(n)))
    used.add(wireRef(n))
    return { ...c, ref: wireRef(n) }
  })
}

export function aiImportAddedCount(result: AiImportResult) {
  return result.symbols.length + result.terminals.length + result.connections.length
}

export function aiImportNotice(result: AiImportResult) {
  const { counts } = result
  const regionNote = counts.region_excluded ? `／領域枠 ${counts.region_excluded} 件はシンボル対象外として除外` : ''
  if (result.source === null) {
    return regionNote
      ? `取り込める検出がありませんでした${regionNote}`
      : 'この図面の推論結果がありません。「AI 改善サイクル」で推論を実行してください'
  }
  const terminalCount =
    result.terminals.length + result.symbols.reduce((n, s) => n + s.terminals.length, 0)
  const skipped = [
    counts.duplicate_dots ? `重複した黒丸 ${counts.duplicate_dots} 件` : '',
    counts.text_zone ? `リレー表・表題欄の中の文字 ${counts.text_zone} 件` : '',
  ].filter(Boolean)
  const skippedNote = skipped.length ? `／${skipped.join('・')}は取り込みませんでした` : ''
  const wiringNote = result.source === 'predictions' ? '／端子と配線は「AI 解析」を実行すると取り込めます' : ''
  if (aiImportAddedCount(result) === 0) {
    return (
      (counts.existing_symbols > 0 ? 'AI の結果はすべて取り込み済みです' : '取り込める検出がありませんでした') +
      regionNote +
      wiringNote
    )
  }
  const parts = [`AI 検出を ${result.symbols.length} 件`]
  if (result.source === 'structure') parts.push(`端子を ${terminalCount} 件`, `配線を ${result.connections.length} 件`)
  return (
    `${parts.join('、')}取り込みました（破線）。内容を確認・修正して保存してください` +
    (counts.existing_symbols > 0 ? `／${counts.existing_symbols} 件は既存のシンボルと重なったため追加していません` : '') +
    skippedNote +
    regionNote +
    wiringNote
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

export const SYMBOL_SORTS = [
  ['registered', '登録順'],
  ['position', '位置順'],
  ['class', '種別順'],
] as const
export type SymbolSort = (typeof SYMBOL_SORTS)[number][0]

/* 位置順の1行の高さ（ページ高さ比）。中心が少しずれただけの同じ段の記号を左から読む順に並べる */
const POSITION_ROW_HEIGHT = 0.02

export function classRank(classOrder: readonly string[], key: string) {
  const index = classOrder.indexOf(key)
  return index < 0 ? classOrder.length : index
}

export function sortSymbols(symbols: SymbolBox[], sort: SymbolSort, classOrder: readonly string[]) {
  if (sort === 'registered') return symbols
  if (sort === 'position') {
    const row = (s: SymbolBox) => Math.floor(s.cy / POSITION_ROW_HEIGHT)
    return [...symbols].sort((a, b) => row(a) - row(b) || a.cx - b.cx)
  }
  return [...symbols].sort(
    (a, b) =>
      classRank(classOrder, a.class_key) - classRank(classOrder, b.class_key) ||
      a.ref.localeCompare(b.ref, undefined, { numeric: true }),
  )
}

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
