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
}))
vi.mock('../api/client', () => ({ api }))

const row = { id: 7, name: 'p.png', sheet_no: 'ER21216', image_width: 1000, image_height: 700, connection_count: 12 } as ProjectRow

const summary: CircuitSummary = {
  engine: 'builtin',
  pages: [{ id: 7, name: 'ER21216' }],
  element_count: 5,
  node_count: 4,
  supplies: ['P'],
  coils: [{ device: 'X R100', symbols: [{ project_id: 7, symbol_ref: 'XC' }], contacts: [{ project_id: 7, symbol_ref: 'XA', kind: 'a' }] }],
  inputs: [{ device: 'PB1', contacts: [{ project_id: 7, symbol_ref: 'PB1', kind: 'a' }] }],
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
      contacts: [{ project_id: 7, symbol_ref: 'XA', device: 'X R100', kind: 'a', closed: energized, label: 'X R100', cx: 0.2, cy: 0.3, w: 0.02, h: 0.02 }],
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
    fireEvent.click(view.getByRole('button', { name: /回路を組んで/ }))
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenCalledWith([7], {}, {}))
    expect(view.getByText('無励磁')).toBeTruthy()
    expect(view.getByText(/端子が 1 個のため/)).toBeTruthy()

    fireEvent.click(view.getByRole('button', { name: /PB1/ }))
    await waitFor(() => expect(view.getByText('励磁')).toBeTruthy())
    expect(api.simulateCircuit).toHaveBeenLastCalledWith([7], { PB1: true }, { 'X R100': false })
    expect(view.container.querySelector('line')?.getAttribute('stroke')).toBe('#e11d48')

    // 押しボタンを離すときは、励磁している状態を渡す（自己保持の回路はそのまま保持される）。
    fireEvent.click(view.getByRole('button', { name: /PB1/ }))
    await waitFor(() => expect(api.simulateCircuit).toHaveBeenLastCalledWith([7], { PB1: false }, { 'X R100': true }))
  })
})
