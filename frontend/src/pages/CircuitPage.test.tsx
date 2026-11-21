// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { CircuitResult, CircuitSummary, ProjectRow } from '../types'
import CircuitPage from './CircuitPage'

const api = vi.hoisted(() => ({
  listProjects: vi.fn(),
  buildCircuit: vi.fn(),
  simulateCircuit: vi.fn(),
  downloadNetlist: vi.fn(),
  imageUrl: (id: number) => `/img/${id}`,
  circuitEditHistory: vi.fn(async () => ({ change_sets: [] })),
  traceTargets: vi.fn(async () => ({ loads: [], devices: [] })),
  listDrawingSets: vi.fn(async () => []),
  drawingSetCoverage: vi.fn(),
  createDrawingSet: vi.fn(),
  updateDrawingSet: vi.fn(),
  suggestDrawingSets: vi.fn(async () => []),
}))
vi.mock('../api/client', () => ({ api, BASE: '' }))

const row = { id: 7, name: 'p.png', sheet_no: 'ER21216', image_width: 1000, image_height: 700, connection_count: 12 } as ProjectRow

const summary: CircuitSummary = {
  engine: 'builtin',
  pages: [{ id: 7, name: 'ER21216' }],
  element_count: 5,
  node_count: 4,
  supplies: ['P'],
  coils: [{ device: 'X R100', symbols: [{ project_id: 7, symbol_ref: 'XC' }], contacts: [{ project_id: 7, symbol_ref: 'XA', kind: 'a' }] }],
  inputs: [{ device: 'PB1', contacts: [{ project_id: 7, symbol_ref: 'PB1', kind: 'a' }] }],
  sheet_links: [{
    how: 'guessed',
    from: { project_id: 7, symbol_ref: 'T1', sheet: 'ER21216', target: 'ER21217', label: 'ER21217へ', page: 'ER21216' },
    to: { project_id: 8, symbol_ref: 'R1', sheet: 'ER21217', target: 'ER21216', label: '(ER21216)', page: 'ER21217' },
  }],
  diagnostics: [{ kind: 'terminals_missing', message: 'リレーコイルの端子が 1 個のため回路に含めません（2 個必要）', page: 'ER21216', symbol_ref: 'Y' }],
}

function result(energized: boolean): CircuitResult {
  return {
    ...summary,
    steps: 2,
    warnings: [],
    coil_states: { 'X R100': { energized, voltage: energized ? 110 : 0 } },
    overlay: [{
      project_id: 7,
      wires: [{ x1: 0.1, y1: 0.1, x2: 0.1, y2: 0.5, live: energized }],
      contacts: [
        { project_id: 7, symbol_ref: 'XA', device: 'X R100', kind: 'a', closed: energized, label: 'X R100', cx: 0.2, cy: 0.3, w: 0.02, h: 0.02 },
        { project_id: 7, symbol_ref: 'PB1', device: 'PB1', kind: 'a', closed: false, label: 'PB1', cx: 0.4, cy: 0.3, w: 0.02, h: 0.02 },
      ],
      coils: [{ device: 'X R100', symbol_ref: 'XC', energized, cx: 0.2, cy: 0.6, w: 0.02, h: 0.02 }],
    }],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listProjects.mockResolvedValue([row])
  api.buildCircuit.mockResolvedValue(summary)
  api.simulateCircuit.mockImplementation(async (_ids: number[], inputs: Record<string, boolean>, energized: Record<string, boolean>) =>
    result(Boolean(inputs.PB1) || Boolean(energized['X R100'])))
})

afterEach(() => cleanup())

describe('CircuitPage', () => {
  it('入力を切り替えると前の励磁状態から解き、図面に結果を重ねる', async () => {
    const view = render(<MemoryRouter><CircuitPage /></MemoryRouter>)
    fireEvent.click(await view.findByRole('checkbox'))
    fireEvent.click(view.getByRole('button', { name: /回路を作成/ }))
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenCalledWith([7], {}, {}))
    expect(view.getByText('無励磁')).toBeTruthy()
    // 回路にできなかった箇所は図面の上に知らせ、「検査」でまとめて見る
    expect(view.getByRole('button', { name: /警告 1/ })).toBeTruthy()

    fireEvent.click(view.getByRole('button', { name: /^PB1(?! を)/ }))
    await waitFor(() => expect(view.getByText('励磁')).toBeTruthy())
    expect(api.simulateCircuit).toHaveBeenLastCalledWith([7], { PB1: true }, { 'X R100': false })
    expect(view.container.querySelector('line')?.getAttribute('stroke')).toBe('#e11d48')

    // 押しボタンを離すときは、励磁している状態を渡す（自己保持の回路はそのまま保持される）。
    fireEvent.click(view.getByRole('button', { name: /^PB1(?! を)/ }))
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenLastCalledWith([7], { PB1: false }, { 'X R100': true }))

    fireEvent.click(view.getByRole('button', { name: /警告 1/ }))
    expect(view.getByRole('tab', { name: /検査/ }).getAttribute('aria-selected')).toBe('true')
    expect(view.getByText(/端子が 1 個のため/)).toBeTruthy()
    expect(view.getByLabelText('図面間のつながり').textContent).toContain('ER21217へ')
    expect(view.getByText('推定')).toBeTruthy()
  })

  it('一覧から ids を付けて開くと、その図面で回路を組み、選択欄を畳む', async () => {
    const view = render(<MemoryRouter initialEntries={['/circuit?ids=7']}><CircuitPage /></MemoryRouter>)
    await waitFor(() => expect(api.buildCircuit).toHaveBeenCalledWith([7]))
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenCalledWith([7], {}, {}))
    expect(view.getByLabelText('対象の図面').textContent).toContain('ER21216')
    expect(view.queryByRole('button', { name: /回路を作成/ })).toBeNull()

    fireEvent.click(view.getByRole('button', { name: /図面選択/ }))
    expect(view.getByRole('checkbox')).toHaveProperty('checked', true)
  })

  it('図面の上の入力の接点をクリックすると ON / OFF が切り替わり、全画面で見られる', async () => {
    const view = render(<MemoryRouter initialEntries={['/circuit?ids=7']}><CircuitPage /></MemoryRouter>)
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenCalledWith([7], {}, {}))
    fireEvent.click(await view.findByRole('button', { name: 'PB1 を ON にする' }))
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenLastCalledWith([7], { PB1: true }, { 'X R100': false }))

    fireEvent.click(view.getByRole('button', { name: '全画面' }))
    expect(view.getByLabelText('図面上の結果').className).toContain('fixed')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(view.getByLabelText('図面上の結果').className).not.toContain('fixed')
  })
})
