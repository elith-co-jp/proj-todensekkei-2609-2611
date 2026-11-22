// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Connection, ProjectDetail, SymbolBox, SymbolClass } from '../../types'
import { EditorInspector } from './EditorInspector'
import type { InspectorTab, SymbolSort } from './model'

const api = vi.hoisted(() => ({ attributeDefinitions: vi.fn(), getReviewSummary: vi.fn(), suggestSheetNumber: vi.fn() }))
vi.mock('../../api/client', () => ({ api }))

const classes = [{ key: 'relay', label: 'リレー' }, { key: 'lamp', label: 'ランプ' }] as SymbolClass[]
const project = { id: 4, name: '図面4', status: 'draft', image_width: 10, image_height: 10, images: [] } as unknown as ProjectDetail

const initialSymbols = (): SymbolBox[] => [
  { ref: 'S1', class_key: 'relay', label: null, cx: 0.5, cy: 0.5, w: 0.1, h: 0.1, note: null, origin: 'inference', confidence: 0.87,
    terminals: [
      { ref: 'S1-T1', name: '1', tx: 0.5, ty: 0.4, origin: 'inference' },
      { ref: 'S1-T2', name: '2', tx: 0.5, ty: 0.6 },
    ] },
  { ref: 'S2', class_key: 'lamp', label: 'RL', cx: 0.2, cy: 0.2, w: 0.1, h: 0.1, note: null, origin: 'inference', terminals: [] },
] as SymbolBox[]
const initialConnections = (): Connection[] => [
  { ref: 'W-0001', from_symbol_ref: 'S1', from_terminal_ref: 'S1-T1', to_symbol_ref: 'S2', to_terminal_ref: null, wire_no: null,
    net_id: null, kind: 'wire', external_ref: null, note: null, origin: 'inference' },
  { from_symbol_ref: 'S2', from_terminal_ref: null, to_symbol_ref: 'S1', to_terminal_ref: 'S1-T2', wire_no: '101', net_id: null,
    kind: 'sheet_ref', external_ref: '(610-2F-9)', note: null },
] as Connection[]

type Spy = ReturnType<typeof vi.fn<(...args: any[]) => any>>
const spies: Record<string, Spy> = {}
let state: { symbols: SymbolBox[]; connections: Connection[] } = { symbols: [], connections: [] }

function Harness({ initialTab = 'symbols', desktop = true, symbols0 = initialSymbols(), connections0 = initialConnections() }: {
  initialTab?: InspectorTab; desktop?: boolean; symbols0?: SymbolBox[]; connections0?: Connection[]
}) {
  const ref = useRef<HTMLElement | null>(null)
  const [tab, setTab] = useState<InspectorTab>(initialTab)
  const [symbols, setSymbols] = useState(symbols0)
  const [connections, setConnections] = useState(connections0)
  const [selectedRef, setSelectedRef] = useState<string | null>(null)
  const [selectedConn, setSelectedConn] = useState<number | null>(null)
  const [sort, setSort] = useState<SymbolSort>('registered')
  const [hidden, setHidden] = useState<Set<string>>(new Set(['S2']))
  state = { symbols, connections }
  return (
    <EditorInspector
      inspectorRef={ref} desktopInspector={desktop} onClose={spies.onClose} tab={tab} onTabChange={(t) => { spies.onTabChange(t); setTab(t) }}
      symbols={symbols} connections={connections} classes={classes} colorOf={() => '#000'} labelOf={(k) => `ラベル:${k}`}
      hiddenSymbols={hidden} selectedRef={selectedRef} onSelectSymbol={setSelectedRef} symbolSort={sort} onSymbolSortChange={setSort}
      onSetSymbolHidden={(r, h) => { spies.onSetSymbolHidden(r, h); setHidden((prev) => { const n = new Set(prev); if (h) n.add(r); else n.delete(r); return n }) }}
      onDeleteSymbol={spies.onDeleteSymbol} pushHistory={spies.pushHistory} setSymbols={setSymbols} setConnections={setConnections}
      selectedConn={selectedConn} onSelectConnection={setSelectedConn} project={project} metaSaveStatus="saved"
      annotationSaveStatus="pending" onQueueMetaSave={spies.onQueueMetaSave} onSplitCoilWires={spies.onSplitCoilWires}
    />
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  for (const name of ['onClose', 'onTabChange', 'onSetSymbolHidden', 'onDeleteSymbol', 'pushHistory', 'onQueueMetaSave', 'onSplitCoilWires']) spies[name] = vi.fn()
  api.attributeDefinitions.mockResolvedValue({
    symbol: { common: [{ key: 'maker', label: 'メーカー', type: 'text' }], by_class: { relay: [{ key: 'coil_v', label: 'コイル電圧', type: 'number', unit: 'V' }] } },
    wire: [{ key: 'length', label: '長さ', type: 'number', unit: 'm' }],
  })
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('EditorInspector', () => {
  it('シンボルを選んで種別・ラベル・端子・属性を直し、隠したものは選ぶと表示に戻す', async () => {
    const view = render(<Harness />)
    expect(view.getByRole('tab', { name: 'シンボル (2)' })).toBeTruthy()
    expect(view.getByText('AI 87%')).toBeTruthy()
    // 隠しているシンボルを選ぶと表示に戻す
    fireEvent.click(view.getByRole('button', { name: /^S2AI/ }))
    expect(spies.onSetSymbolHidden).toHaveBeenCalledWith('S2', false)
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest', behavior: 'smooth' })
    expect(view.getByText('未登録（配線はシンボル単位で登録されます）')).toBeTruthy()

    fireEvent.click(view.getByRole('button', { name: /^S1AI/ }))
    const detail = view.getByRole('button', { name: /^S1AI/ }).parentElement!.parentElement!
    fireEvent.change(within(detail).getByRole('combobox'), { target: { value: 'lamp' } })
    expect(spies.pushHistory).toHaveBeenCalled()
    expect(state.symbols[0].class_key).toBe('lamp')
    expect(state.symbols[1].class_key).toBe('lamp')
    fireEvent.change(within(detail).getByPlaceholderText('33HB 等'), { target: { value: 'X1' } })
    expect(state.symbols[0].label).toBe('X1')
    fireEvent.change(within(detail).getByPlaceholderText('33HB 等'), { target: { value: '' } })
    expect(state.symbols[0].label).toBeNull()
    expect(state.symbols[1].label).toBe('RL')

    const terminalInputs = within(detail).getAllByDisplayValue(/^[12]$/)
    fireEvent.change(terminalInputs[1], { target: { value: 'B' } })
    expect(state.symbols[0].terminals.map((t) => t.name)).toEqual(['1', 'B'])
    await waitFor(() => expect(within(detail).getByLabelText('メーカー')).toBeTruthy())
    fireEvent.change(within(detail).getByLabelText('メーカー'), { target: { value: '東電' } })
    expect(state.symbols[0].attributes).toEqual({ maker: '東電' })
    expect(state.symbols[1].attributes).toBeUndefined()

    // 端子を消すと、その端子の配線も消す
    const removeButtons = within(detail).getAllByRole('button').filter((b) => !b.getAttribute('aria-label') && b.className.includes('btn-danger'))
    fireEvent.click(removeButtons[0])
    expect(state.symbols[0].terminals.map((t) => t.ref)).toEqual(['S1-T2'])
    expect(state.connections.map((c) => c.to_terminal_ref)).toEqual(['S1-T2'])

    fireEvent.click(view.getByRole('button', { name: 'S1を非表示' }))
    expect(spies.onSetSymbolHidden).toHaveBeenLastCalledWith('S1', true)
    fireEvent.click(view.getByRole('button', { name: 'S1を削除' }))
    expect(spies.onDeleteSymbol).toHaveBeenCalledWith('S1')
    fireEvent.click(view.getByRole('button', { name: '種別順' }))
    expect(view.getByRole('button', { name: '種別順' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('配線の電線番号・種類・参照先・属性を直し、消せる', async () => {
    const view = render(<Harness initialTab="connections" />)
    expect(view.getByText('S1:T1')).toBeTruthy()
    expect(view.getByText('W-0001')).toBeTruthy()
    const [first, second] = view.getAllByPlaceholderText('電線番号')
    fireEvent.change(first, { target: { value: '201' } })
    expect(state.connections.map((c) => c.wire_no)).toEqual(['201', '101'])
    fireEvent.change(first, { target: { value: '' } })
    expect(state.connections[0].wire_no).toBeNull()
    expect((second as HTMLInputElement).value).toBe('101')

    const ref = view.getByPlaceholderText('参照先（(610-2F-9) 等）')
    fireEvent.change(ref, { target: { value: '(1-2)' } })
    expect(state.connections[1].external_ref).toBe('(1-2)')
    fireEvent.change(ref, { target: { value: '' } })
    expect(state.connections[1].external_ref).toBeNull()
    expect(state.connections[0].external_ref).toBeNull()
    fireEvent.change(view.getAllByRole('combobox')[0], { target: { value: 'sheet_ref' } })
    expect(state.connections.map((c) => c.kind)).toEqual(['sheet_ref', 'sheet_ref'])
    expect(view.getAllByPlaceholderText('参照先（(610-2F-9) 等）')).toHaveLength(2)
    fireEvent.change(view.getAllByPlaceholderText('参照先（(610-2F-9) 等）')[0], { target: { value: 'X' } })
    expect(state.connections[1].external_ref).toBeNull()

    fireEvent.click(view.getByRole('button', { name: /^S2→/ }))
    await waitFor(() => expect(view.getByLabelText('長さ（m）')).toBeTruthy())
    fireEvent.change(view.getByLabelText('長さ（m）'), { target: { value: '12' } })
    expect(state.connections[1].attributes).toEqual({ length: 12 })
    expect(state.connections[0].attributes).toBeUndefined()

    fireEvent.click(view.getByRole('button', { name: 'S1からS2への配線を削除' }))
    expect(spies.pushHistory).toHaveBeenCalled()
    expect(state.connections).toHaveLength(1)
  })

  it('タブを矢印・Home・End で巡り、閉じるボタンで閉じる。狭い画面ではダイアログにする', () => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => { cb(0); return 1 })
    const view = render(<Harness desktop={false} symbols0={[]} connections0={[]} />)
    expect(view.getByRole('dialog')).toBeTruthy()
    expect(view.getByText(/でシンボルモードにして/)).toBeTruthy()
    expect(view.queryByRole('group', { name: 'シンボルの並び順' })).toBeNull()
    const tab = view.getByRole('tab', { name: 'シンボル (0)' })
    fireEvent.keyDown(tab, { key: 'ArrowRight' })
    expect(spies.onTabChange).toHaveBeenLastCalledWith('connections')
    expect(view.getByText(/で配線モードにして/)).toBeTruthy()
    fireEvent.keyDown(view.getByRole('tab', { name: '配線 (0)' }), { key: 'ArrowLeft' })
    fireEvent.keyDown(view.getByRole('tab', { name: 'シンボル (0)' }), { key: 'ArrowLeft' })
    expect(spies.onTabChange).toHaveBeenLastCalledWith('meta')
    expect(document.activeElement?.id).toBe('inspector-tab-meta')
    fireEvent.keyDown(view.getByRole('tab', { name: '図面情報' }), { key: 'Home' })
    fireEvent.keyDown(view.getByRole('tab', { name: 'シンボル (0)' }), { key: 'End' })
    expect(spies.onTabChange).toHaveBeenLastCalledWith('meta')
    fireEvent.keyDown(view.getByRole('tab', { name: '図面情報' }), { key: 'a' })
    fireEvent.click(view.getByRole('tab', { name: '配線 (0)' }))
    expect(spies.onTabChange).toHaveBeenCalledTimes(6)
    for (const button of view.getAllByRole('button', { name: '詳細パネルを閉じる' })) fireEvent.click(button)
    expect(spies.onClose).toHaveBeenCalledTimes(2)
    vi.restoreAllMocks()
  })

  it('属性の定義が読めなくても開け、閉じた後に返った定義は使わない。動きを減らす設定ならすぐ移る', async () => {
    let resolve: (value: unknown) => void = () => undefined
    api.attributeDefinitions.mockReturnValueOnce(new Promise((r) => { resolve = r }))
    const first = render(<Harness />)
    first.unmount()
    await act(async () => resolve({ symbol: { common: [], by_class: {} }, wire: [] }))

    api.attributeDefinitions.mockRejectedValueOnce(new Error('x'))
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
    const view = render(<Harness />)
    fireEvent.click(view.getByRole('button', { name: /^S1AI/ }))
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest', behavior: 'auto' })
    expect(view.queryByLabelText('メーカー')).toBeNull()
    fireEvent.click(view.getByRole('tab', { name: /配線/ }))
    fireEvent.click(view.getAllByRole('button', { expanded: false })[0])
    expect(view.getByLabelText('属性')).toBeTruthy()
  })
})
