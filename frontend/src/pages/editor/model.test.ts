import { describe, expect, it } from 'vitest'

import type { AiImportResult, Connection, ProjectDetail, SymbolBox } from '../../types'
import {
  aiImportNotice, buildAnnotationPayload, buildMetaForm, classRank, clamp01, isAnnotationPayload, isMetaForm, layerStateOf, nextSymbolRef,
  sortSymbols,
} from './model'

const symbol = (patch: Partial<SymbolBox> = {}): SymbolBox => ({
  ref: 'S1', class_key: 'relay', label: null, cx: 0.5, cy: 0.5, w: 0.1, h: 0.1, note: null, terminals: [], ...patch,
} as SymbolBox)
const wire = (patch: Partial<Connection> = {}): Connection => ({
  from_symbol_ref: 'S1', from_terminal_ref: null, to_symbol_ref: 'S2', to_terminal_ref: null, wire_no: null, net_id: null,
  kind: 'wire', external_ref: null, note: null, ...patch,
} as Connection)

describe('isAnnotationPayload（下書きの復元で壊れた内容を捨てる）', () => {
  const valid = () => buildAnnotationPayload(
    [symbol({ origin: 'inference', confidence: 0.9, terminals: [{ ref: 'T1', name: '1', tx: 0.1, ty: 0.2, origin: 'inference' }] })],
    [wire({ origin: 'inference' })],
  )

  it('正しい内容を受け付け、参照名の無い配線は null にする', () => {
    const payload = valid()
    expect(payload.connections[0].ref).toBeNull()
    expect(isAnnotationPayload(payload)).toBe(true)
    expect(isAnnotationPayload({ ...payload, connections: [{ ...payload.connections[0], ref: 'W-0001', kind: 'sheet_ref' }] })).toBe(true)
  })

  it('形の違うものを捨てる', () => {
    const payload = valid()
    const s = payload.symbols[0]
    const c = payload.connections[0]
    const bad: unknown[] = [
      null, [], { symbols: [] }, { symbols: [], connections: {} },
      { ...payload, symbols: [null] },
      { ...payload, symbols: [{ ...s, terminals: null }] },
      { ...payload, symbols: [{ ...s, ref: 1 }] },
      { ...payload, symbols: [{ ...s, cx: Number.NaN }] },
      { ...payload, symbols: [{ ...s, origin: 'robot' }] },
      { ...payload, symbols: [{ ...s, confidence: 'high' }] },
      { ...payload, symbols: [{ ...s, attributes: { a: {} } }] },
      { ...payload, symbols: [{ ...s, attributes: 'x' }] },
      { ...payload, symbols: [{ ...s, terminals: [null] }] },
      { ...payload, symbols: [{ ...s, terminals: [{ ...s.terminals[0], ref: 1 }] }] },
      { ...payload, symbols: [{ ...s, terminals: [{ ...s.terminals[0], name: 1 }] }] },
      { ...payload, symbols: [{ ...s, terminals: [{ ...s.terminals[0], tx: 'x' }] }] },
      { ...payload, symbols: [{ ...s, terminals: [{ ...s.terminals[0], ty: 'x' }] }] },
      { ...payload, symbols: [{ ...s, terminals: [{ ...s.terminals[0], origin: 'x' }] }] },
      { ...payload, connections: [null] },
      { ...payload, connections: [{ ...c, from_symbol_ref: 1 }] },
      { ...payload, connections: [{ ...c, kind: 'cable' }] },
      { ...payload, connections: [{ ...c, ref: 3 }] },
      { ...payload, connections: [{ ...c, attributes: [1] }] },
    ]
    for (const value of bad) expect(isAnnotationPayload(value), JSON.stringify(value)).toBe(false)
    expect(isAnnotationPayload({ ...payload, symbols: [{ ...s, confidence: undefined, attributes: undefined }] })).toBe(true)
  })
})

const result = (patch: Partial<AiImportResult> = {}): AiImportResult => ({
  source: 'structure', symbols: [], terminals: [], connections: [],
  counts: { existing_symbols: 0, region_excluded: 0, duplicate_dots: 0, text_zone: 0 },
  ...patch,
} as unknown as AiImportResult)

describe('aiImportNotice', () => {
  it('解析結果が無い・領域枠だけ・取り込み済み・取り込めるものが無いときの文', () => {
    expect(aiImportNotice(result({ source: null }))).toBe('この図面の解析結果がありません。図面一覧の「AI 解析」を実行してください')
    expect(aiImportNotice(result({ source: null, counts: { existing_symbols: 0, region_excluded: 2, duplicate_dots: 0, text_zone: 0 } } as Partial<AiImportResult>)))
      .toBe('取り込める検出がありませんでした／領域枠 2 件はシンボル対象外として除外')
    expect(aiImportNotice(result({ source: 'predictions', counts: { existing_symbols: 3, region_excluded: 0, duplicate_dots: 0, text_zone: 0 } } as Partial<AiImportResult>)))
      .toBe('AI の結果はすべて取り込み済みです／端子と配線は「AI 解析」を実行すると取り込めます')
    expect(aiImportNotice(result())).toBe('取り込める検出がありませんでした')
  })

  it('取り込んだ数と、取り込まなかったものを並べる', () => {
    const added = result({
      symbols: [symbol({ terminals: [{ ref: 'T', name: '1', tx: 0, ty: 0 }] })],
      terminals: [{ symbol_ref: 'S0', ref: 'T2', name: '2', tx: 0, ty: 0 }],
      connections: [wire()],
      counts: { existing_symbols: 1, region_excluded: 1, duplicate_dots: 2, text_zone: 3 },
    } as Partial<AiImportResult>)
    expect(aiImportNotice(added)).toBe(
      'AI 検出を 1 件、端子を 2 件、配線を 1 件取り込みました（破線）。内容を確認・修正して保存してください'
      + '／1 件は既存のシンボルと重なったため追加していません／重複した黒丸 2 件・リレー表・表題欄の中の文字 3 件は取り込みませんでした'
      + '／領域枠 1 件はシンボル対象外として除外',
    )
    expect(aiImportNotice(result({ source: 'predictions', symbols: [symbol()] }))).toBe(
      'AI 検出を 1 件取り込みました（破線）。内容を確認・修正して保存してください／端子と配線は「AI 解析」を実行すると取り込めます',
    )
  })
})

describe('小さな関数', () => {
  it('図面情報の入力欄を作り、形を確かめる', () => {
    const form = buildMetaForm({ name: 'a', sheet_no: 'S', page_no: '2', revision: 'B', status: 'done', assignee: '山田', note: 'x' } as ProjectDetail)
    expect(form).toEqual({ name: 'a', sheet_no: 'S', page_no: '2', revision: 'B', status: 'done', assignee: '山田', note: 'x' })
    expect(isMetaForm(form)).toBe(true)
    expect(isMetaForm({ ...form, note: null })).toBe(false)
    expect(isMetaForm(null)).toBe(false)
  })

  it('並べ替え・範囲・レイヤーの状態・次の参照名', () => {
    expect(classRank(['a', 'b'], 'z')).toBe(2)
    const a = symbol({ ref: 'S10', class_key: 'z', cx: 0.9, cy: 0.1 })
    const b = symbol({ ref: 'S2', class_key: 'relay', cx: 0.1, cy: 0.1 })
    const c = symbol({ ref: 'S3', class_key: 'relay', cx: 0.1, cy: 0.9 })
    expect(sortSymbols([a, b, c], 'registered', [])).toEqual([a, b, c])
    expect(sortSymbols([c, a, b], 'position', []).map((s) => s.ref)).toEqual(['S2', 'S10', 'S3'])
    expect(sortSymbols([a, c, b], 'class', ['relay']).map((s) => s.ref)).toEqual(['S2', 'S3', 'S10'])
    expect([clamp01(-1), clamp01(0.5), clamp01(2)]).toEqual([0, 0.5, 1])
    expect([layerStateOf(0, 0), layerStateOf(2, 2), layerStateOf(1, 2)]).toEqual(['on', 'off', 'partial'])
    expect(nextSymbolRef([symbol({ ref: 'SYM-0002' })])).toBe('SYM-0003')
    expect(nextSymbolRef([symbol({ ref: 'SYM-0001' })])).toBe('SYM-0002')
  })
})
