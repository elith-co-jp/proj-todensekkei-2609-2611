// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { CircuitImpact, CircuitSummary, CircuitWhatIf } from '../../types'
import WhatIfPanel from './WhatIfPanel'

const api = vi.hoisted(() => ({
  circuitImpact: vi.fn(),
  circuitWhatIf: vi.fn(),
  downloadCircuitCode: vi.fn(),
}))
vi.mock('../../api/client', () => ({ api }))

const summary: CircuitSummary = {
  engine: 'builtin',
  pages: [{ id: 7, name: 'ER21216' }],
  element_count: 5,
  node_count: 4,
  supplies: ['P'],
  coils: [{ device: 'X R100', symbols: [{ project_id: 7, symbol_ref: 'XC' }], contacts: [] }],
  inputs: [{ device: 'PB1', contacts: [] }],
  diagnostics: [],
}

const impact: CircuitImpact = {
  device: 'PB1',
  kind: 'input',
  devices: [{ device: 'X R100', depth: 1, via: 'PB1' }],
  loads: [
    { project_id: 7, symbol_ref: 'XC', label: 'X R100', depth: 1, device: 'X R100', via: 'PB1', page: 'ER21216' },
    { project_id: 7, symbol_ref: 'L', label: 'RL', depth: 2, device: null, via: 'X R100', page: 'ER21216' },
  ],
  upstream: [],
  contacts: [],
}

const whatIf: CircuitWhatIf = {
  before: { coils: { 'X R100': false } },
  after: { coils: { 'X R100': true } },
  changes: {
    coils: [{ device: 'X R100', before: false, after: true }],
    loads: [{ project_id: 7, symbol_ref: 'L', label: 'RL', device: null, page: 'ER21216', before: false, after: true }],
    contacts: [],
  },
  unknown_symbols: [],
  warnings: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.circuitImpact.mockResolvedValue(impact)
  api.circuitWhatIf.mockResolvedValue(whatIf)
})

afterEach(() => cleanup())

describe('WhatIfPanel', () => {
  it('波及範囲と「動作させたら」の変化を今の状態から調べる', async () => {
    const view = render(
      <MemoryRouter>
        <WhatIfPanel projectIds={[7]} summary={summary} inputs={{ PB1: false }} energized={{ 'X R100': false }} />
      </MemoryRouter>,
    )
    fireEvent.click(view.getByRole('button', { name: /波及範囲/ }))
    await waitFor(() => expect(view.getByText(/リレー 1・負荷 2/)).toBeTruthy())
    expect(api.circuitImpact).toHaveBeenCalledWith([7], 'PB1')
    expect(view.getByText('2 段')).toBeTruthy()

    fireEvent.change(view.getByRole('combobox', { name: '機器' }), { target: { value: 'X R100' } })
    fireEvent.click(view.getByRole('button', { name: /動作させたら/ }))
    await waitFor(() => expect(view.getByText('X R100 を動作させたとき')).toBeTruthy())
    expect(api.circuitWhatIf).toHaveBeenCalledWith([7], { PB1: false }, { 'X R100': false }, { devices: { 'X R100': true } })
    expect(view.getByLabelText('もしもの結果').textContent).toContain('RL')

    fireEvent.click(view.getByRole('button', { name: /回路コード/ }))
    expect(api.downloadCircuitCode).toHaveBeenCalledWith([7])
  })
})
