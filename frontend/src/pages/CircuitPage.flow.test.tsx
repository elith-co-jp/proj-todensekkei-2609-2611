// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { CircuitResult, CircuitSummary, ProjectRow } from '../types'
import type { LocateTarget } from './circuit/DevicePanel'
import CircuitPage from './CircuitPage'

// 画面の流れ（選ぶ・組む・タブ・パネル・全画面・場所へ移動）を見るため、道具のパネルは中身の無い代わりに置き換える
const api = vi.hoisted(() => ({
  listProjects: vi.fn(),
  buildCircuit: vi.fn(),
  simulateCircuit: vi.fn(),
  downloadNetlist: vi.fn(),
  imageUrl: (id: number) => `/img/${id}`,
}))
vi.mock('../api/client', () => ({ api, BASE: '' }))
const panels = vi.hoisted(() => ({ onLocate: null as null | ((t: LocateTarget) => void), onApplied: [] as (() => void)[] }))
vi.mock('./circuit/DevicePanel', () => ({
  default: ({ onLocate }: { onLocate: (t: LocateTarget) => void }) => {
    panels.onLocate = onLocate
    return <div>機器パネル</div>
  },
}))
vi.mock('./circuit/TracePanel', () => ({ default: () => <div>条件パネル</div> }))
vi.mock('./circuit/WhatIfPanel', () => ({ default: ({ energized }: { energized: Record<string, boolean> }) => <div>波及パネル {JSON.stringify(energized)}</div> }))
vi.mock('./circuit/TimelinePanel', () => ({ default: () => <div>タイマーパネル</div> }))
vi.mock('./circuit/ConsistencyPanel', () => ({
  default: ({ onApplied }: { onApplied: () => void }) => <button type="button" onClick={onApplied}>整合性で直した</button>,
}))
vi.mock('./circuit/EditPanel', () => ({
  default: ({ onApplied }: { onApplied: () => void }) => <button type="button" onClick={onApplied}>変更を適用した</button>,
}))
vi.mock('./circuit/DrawingSetBar', () => ({
  default: ({ onSelect }: { onSelect: (ids: number[]) => void }) => <button type="button" onClick={() => onSelect([8])}>セットを選ぶ</button>,
}))

const rows = [
  { id: 7, name: 'p7.png', sheet_no: 'ER7', image_width: 1000, image_height: 700, connection_count: 3 },
  { id: 8, name: 'p8.png', sheet_no: null, image_width: 1000, image_height: 700, connection_count: 0 },
] as ProjectRow[]

const summary: CircuitSummary = {
  engine: 'ngspice',
  pages: [{ id: 7, name: 'ER7' }, { id: 8, name: 'p8.png' }],
  element_count: 5,
  node_count: 4,
  supplies: ['P'],
  coils: [{ device: 'X1', symbols: [], contacts: [] }, { device: 'X2', symbols: [], contacts: [] }],
  inputs: [],
  diagnostics: [],
}

function result(patch: Partial<CircuitResult> = {}): CircuitResult {
  return {
    ...summary, steps: 3, warnings: ['電源が見つかりません'],
    coil_states: { X1: { energized: true, voltage: 100 } },
    overlay: [{ project_id: 7, wires: [], contacts: [], coils: [] }],
    ...patch,
  }
}

let mediaListeners: (() => void)[] = []
function stubMedia(matches: boolean) {
  mediaListeners = []
  const media = {
    matches,
    addEventListener: (_: string, cb: () => void) => mediaListeners.push(cb),
    removeEventListener: vi.fn(),
  }
  vi.stubGlobal('matchMedia', vi.fn(() => media))
  return media
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  api.listProjects.mockResolvedValue(rows)
  api.buildCircuit.mockResolvedValue(summary)
  api.simulateCircuit.mockResolvedValue(result())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const open = (entry = '/circuit') => render(<MemoryRouter initialEntries={[entry]}><CircuitPage /></MemoryRouter>)

describe('CircuitPage（画面の流れ）', () => {
  it('図面の読み込み・組み立て・計算の失敗を出す', async () => {
    api.listProjects.mockRejectedValueOnce(new Error('一覧を読めません'))
    const first = open()
    expect((await first.findByRole('alert')).textContent).toBe('一覧を読めません')
    expect(first.getByText(/まだ図面がありません/)).toBeTruthy()
    first.unmount()

    api.listProjects.mockRejectedValueOnce('文字列')
    const second = open()
    expect((await second.findByRole('alert')).textContent).toBe('文字列')
    second.unmount()

    api.buildCircuit.mockRejectedValueOnce(new Error('回路を組めません')).mockRejectedValueOnce('組めない')
    const view = open()
    fireEvent.click(await view.findByRole('checkbox', { name: /ER7/ }))
    fireEvent.click(view.getByRole('button', { name: /回路を作成/ }))
    expect((await view.findByRole('alert')).textContent).toBe('回路を組めません')
    fireEvent.click(view.getByRole('button', { name: /回路を作成/ }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toBe('組めない'))

    api.simulateCircuit.mockRejectedValueOnce(new Error('計算できません'))
    fireEvent.click(view.getByRole('button', { name: /回路を作成/ }))
    expect(await view.findByText('計算できません')).toBeTruthy()
    // 計算の結果がまだ無くても、波及は励磁なしとして開ける
    fireEvent.click(view.getByRole('tab', { name: '波及' }))
    expect(view.getByText('波及パネル {}')).toBeTruthy()
    fireEvent.click(view.getByRole('tab', { name: '操作' }))
    api.simulateCircuit.mockRejectedValueOnce('計算の失敗')
    fireEvent.click(view.getByRole('button', { name: /全 OFF/ }))
    expect(await view.findByText('計算の失敗')).toBeTruthy()
  })

  it('検索・全選択・選択解除・セットで図面を選び、進め方の段を示す', async () => {
    const view = open()
    await view.findByRole('checkbox', { name: /ER7/ })
    expect(view.getByText('図面を選んでください')).toBeTruthy()
    expect(view.getByRole('listitem', { current: 'step' }).textContent).toContain('図面選択')
    fireEvent.change(view.getByPlaceholderText('図面を検索'), { target: { value: 'p8' } })
    expect(view.queryByRole('checkbox', { name: /ER7/ })).toBeNull()
    fireEvent.click(view.getByRole('button', { name: /表示中を全選択/ }))
    expect(view.getByText('1 枚選択')).toBeTruthy()
    expect(view.getByRole('listitem', { current: 'step' }).textContent).toContain('回路作成')
    fireEvent.change(view.getByPlaceholderText('図面を検索'), { target: { value: 'どこにもない' } })
    expect((view.getByRole('button', { name: /表示中を全選択/ }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(view.getByPlaceholderText('図面を検索'), { target: { value: '' } })
    fireEvent.click(view.getByRole('button', { name: /^全選択/ }))
    expect(view.getByText('2 枚選択')).toBeTruthy()
    fireEvent.click(view.getByRole('checkbox', { name: /ER7/ }))
    expect(view.getByText('1 枚選択')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /選択解除/ }))
    expect(view.getByText('図面を選んでください')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'セットを選ぶ' }))
    expect((view.getByRole('checkbox', { name: /p8\.png/ }) as HTMLInputElement).checked).toBe(true)
  })

  it('ids の図面が一覧に無ければ組まず、同じ ids では組み直さない', async () => {
    const view = open('/circuit?ids=99,abc,-1')
    await view.findByRole('checkbox', { name: /ER7/ })
    expect(api.buildCircuit).not.toHaveBeenCalled()
  })

  it('狭い画面ではタブを下に並べ、場所へ移ると図面までスクロールする', async () => {
    stubMedia(false)
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    const view = open('/circuit?ids=7,8')
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenCalledWith([7, 8], {}, {}))
    expect(await view.findByText('電源が見つかりません')).toBeTruthy()
    expect(view.getByText('電気の計算: ngspice で計算・3 回')).toBeTruthy()
    expect(view.getByText('ありません')).toBeTruthy()
    expect(view.getByText('励磁 1 / 2')).toBeTruthy()
    expect(view.queryByRole('button', { name: /警告/ })).toBeNull()
    expect(view.queryByRole('button', { name: /パネルを/ })).toBeNull()

    fireEvent.click(view.getByRole('tab', { name: '波及' }))
    expect(view.getByText('波及パネル {"X1":true}')).toBeTruthy()
    for (const [name, text] of [['条件', '条件パネル'], ['タイマー', 'タイマーパネル']]) {
      fireEvent.click(view.getByRole('tab', { name }))
      expect(view.getByText(text)).toBeTruthy()
    }
    fireEvent.click(view.getByRole('tab', { name: /検査/ }))
    expect(view.getByText('なし')).toBeTruthy()
    expect(view.queryByLabelText('図面間のつながり')).toBeNull()

    fireEvent.click(view.getByRole('tab', { name: '機器' }))
    act(() => panels.onLocate!({ project_id: 8, cx: 0.5, cy: 0.5, w: 0.1, h: 0.1, label: 'X1 のコイル' }))
    expect(scroll).toHaveBeenCalled()
    expect(view.getByRole('tab', { name: 'p8.png', selected: true })).toBeTruthy()
    expect(view.getByTestId('viewer-focus')).toBeTruthy()
    // 別のページへ切り替えると枠は出さない
    fireEvent.click(view.getByRole('tab', { name: 'ER7' }))
    expect(view.queryByTestId('viewer-focus')).toBeNull()
    // ページの結果が無いときも図面は出す
    fireEvent.click(view.getByRole('tab', { name: 'p8.png' }))
    expect(view.getByAltText('p8.png')).toBeTruthy()
  })

  it('広い画面ではパネルを図面に重ね、P で開閉し、閉じたら縦のタブから開く', async () => {
    const media = stubMedia(true)
    api.buildCircuit.mockResolvedValue({
      ...summary,
      diagnostics: [
        { kind: 'x', message: '端子が足りません', page: 'ER7', symbol_ref: 'Y', project_id: 7 },
        { kind: 'y', message: 'ページの無い注意', page: null, symbol_ref: null },
      ],
      sheet_links: [{
        how: 'tag',
        from: { project_id: 7, symbol_ref: 'T1', sheet: 'ER7', target: 'ER8', label: '', page: 'ER7' },
        to: { project_id: 8, symbol_ref: 'R1', sheet: 'ER8', target: 'ER7', label: '', page: 'p8.png' },
      }],
    } as unknown as CircuitSummary)
    const view = open('/circuit?ids=7')
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenCalled())
    expect(await view.findByRole('button', { name: 'パネルを閉じる' })).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /警告 2/ }))
    const panel = view.getByRole('tabpanel')
    expect(within(panel).getByRole('link', { name: '編集' }).getAttribute('href')).toBe('/projects/7?symbol=Y')
    expect(within(panel).getByText('ページの無い注意')).toBeTruthy()
    expect(within(panel).getByRole('link', { name: 'ER7 T1' })).toBeTruthy()
    expect(within(panel).getByRole('link', { name: 'p8.png R1' })).toBeTruthy()
    expect(within(panel).queryByText('推定')).toBeNull()
    // 検査タブを開いている間は数字を白地で出す
    expect(view.getByRole('tab', { name: /検査/ }).querySelector('span.bg-white')).toBeTruthy()

    fireEvent.keyDown(window, { key: 'p' })
    expect(view.getByRole('button', { name: 'パネルを開く' })).toBeTruthy()
    expect(view.queryByRole('tabpanel')).toBeNull()
    // 閉じている間はタブを選んでいない。数字は警告の色
    expect(view.getByRole('tab', { name: /検査/ }).getAttribute('aria-selected')).toBe('false')
    expect(view.getByRole('tab', { name: /検査/ }).querySelector('span.bg-amber-500')).toBeTruthy()
    // 修飾キー・入力欄の P は無視する
    fireEvent.keyDown(window, { key: 'P', ctrlKey: true })
    fireEvent.keyDown(window, { key: 'P', metaKey: true })
    fireEvent.keyDown(window, { key: 'P', altKey: true })
    fireEvent.click(view.getByRole('button', { name: /図面選択/ }))
    fireEvent.keyDown(view.getByPlaceholderText('図面を検索'), { key: 'P' })
    expect(view.getByRole('button', { name: 'パネルを開く' })).toBeTruthy()
    fireEvent.keyDown(window, { key: 'Escape' })
    fireEvent.click(view.getByRole('tab', { name: '変更' }))
    expect(view.getByRole('button', { name: 'パネルを閉じる' })).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'パネルを閉じる' }))
    fireEvent.keyDown(window, { key: 'P' })
    expect(view.getByRole('button', { name: 'パネルを閉じる' })).toBeTruthy()

    // 変更・整合性の修正を適用したら回路を組み直す
    const builds = api.buildCircuit.mock.calls.length
    fireEvent.click(view.getByRole('button', { name: '変更を適用した' }))
    await waitFor(() => expect(api.buildCircuit.mock.calls.length).toBe(builds + 1))
    fireEvent.click(view.getByRole('tab', { name: /検査/ }))
    fireEvent.click(view.getByRole('button', { name: '整合性で直した' }))
    await waitFor(() => expect(api.buildCircuit.mock.calls.length).toBe(builds + 2))
    expect(api.buildCircuit).toHaveBeenLastCalledWith([7])
    // 場所へ移ってもスクロールしない（パネルは図面の上）
    Element.prototype.scrollIntoView = vi.fn()
    fireEvent.click(view.getByRole('tab', { name: '機器' }))
    act(() => panels.onLocate!({ project_id: 7, cx: 0.5, cy: 0.5, w: 0.1, h: 0.1 }))
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled()

    // 画面幅が狭くなったら下に並べる
    media.matches = false
    act(() => mediaListeners.forEach((cb) => cb()))
    expect(view.queryByRole('button', { name: /パネルを/ })).toBeNull()
    view.unmount()
    expect(media.removeEventListener).toHaveBeenCalled()
  })

  it('組んだ後に選び直すとキャンセルで戻れ、全画面ではパネルを重ねる', async () => {
    const view = open('/circuit?ids=7')
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenCalled())
    await view.findByRole('button', { name: /図面選択/ })
    fireEvent.click(view.getByRole('button', { name: /図面選択/ }))
    expect(view.getByRole('button', { name: '閉じる' })).toBeTruthy()
    fireEvent.click(view.getByRole('checkbox', { name: /p8\.png/ }))
    fireEvent.click(view.getByRole('button', { name: 'キャンセル' }))
    expect(view.queryByRole('button', { name: 'キャンセル' })).toBeNull()
    fireEvent.click(view.getByRole('button', { name: /図面選択/ }))
    fireEvent.click(view.getByRole('button', { name: /図面選択/ }))
    expect(view.queryByPlaceholderText('図面を検索')).toBeNull()

    // 選択した図面の数が同じでも、中身が違えば選び直しとみなす
    fireEvent.click(view.getByRole('button', { name: /図面選択/ }))
    fireEvent.click(view.getByRole('checkbox', { name: /ER7/ }))
    expect(view.getByRole('button', { name: 'キャンセル' })).toBeTruthy()
    fireEvent.click(view.getByRole('checkbox', { name: /ER7/ }))

    // matchMedia の無い環境（jsdom）では全画面のときだけパネルを重ねる
    fireEvent.click(view.getByRole('button', { name: '全画面' }))
    expect(view.getByRole('button', { name: 'パネルを閉じる' })).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: '全画面を終了' }))
    expect(view.queryByRole('button', { name: 'パネルを閉じる' })).toBeNull()
    fireEvent.keyDown(window, { key: 'Escape' })

    fireEvent.click(view.getByRole('button', { name: /ネットリスト出力/ }))
    expect(api.downloadNetlist).toHaveBeenCalledWith([7], {})
    fireEvent.click(view.getByRole('button', { name: /再作成/ }))
    await waitFor(() => expect(api.buildCircuit).toHaveBeenCalledTimes(2))
  })

  it('組んだ図面の一覧が無いページの結果は読み込み中と出す', async () => {
    api.buildCircuit.mockResolvedValue({ ...summary, pages: [] })
    api.simulateCircuit.mockResolvedValue(result({ coil_states: {}, overlay: [] }))
    const view = open('/circuit?ids=7')
    expect(await view.findByText('図面を読み込んでいます…')).toBeTruthy()
    expect(view.getAllByText('無励磁')).toHaveLength(2)
  })
})
