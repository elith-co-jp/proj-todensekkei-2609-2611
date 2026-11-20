// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { TraceBackward, TraceForward, TraceTargets } from '../../types'
import TracePanel from './TracePanel'

const api = vi.hoisted(() => ({
  traceTargets: vi.fn(),
  traceBackward: vi.fn(),
  traceForward: vi.fn(),
  downloadAnnotationTable: vi.fn(),
  downloadCvat: vi.fn(),
}))
vi.mock('../../api/client', () => ({ api }))

const targets: TraceTargets = {
  loads: [{ name: 'X R100', device: 'X R100' }],
  devices: [
    { device: 'PB1', source: 'input', source_label: '外部入力（図面範囲外）', reason: '', contact_count: 1 },
    { device: 'X R100', source: 'relay', source_label: 'リレー（図面内にコイル）', reason: '', contact_count: 2 },
  ],
}

const backward: TraceBackward = {
  mode: 'backward',
  target: 'X R100',
  kind: 'relay',
  status: '判定不能',
  formula: '!STOP & (PB1 | "X R100")',
  text: '¬STOP ∧ (PB1 ∨ X R100)',
  branches: [{
    status: '判定不能', supply: 'P', open: [], unknown: [], contradictory: false,
    contacts: [{ project_id: 7, symbol_ref: 'PB', page: 'ER1', label: 'PB1', device: 'PB1', kind: 'a', side: '電源側',
      device_state: null, how: 'input', conducts: null }],
  }],
  reasons: ['PB1（ER1 PB）は外部入力で、状態が与えられていません'],
  devices: [],
  explain: { target: 'X R100', kind: 'relay', text: '¬STOP ∧ (PB1 ∨ X R100)', inputs: [], relays: [] },
  truncated: false,
}

const forward: TraceForward = {
  mode: 'forward',
  device: 'PB1',
  source: 'input',
  affected: [{ target: 'X R100', kind: 'relay', depth: 1, via: 'PB1', role: '一部の枝だけ（ほかの枝しだい）', formula: '', contacts: [] }],
  state_change: { from: false, to: true, targets: [{ target: 'X R100', before: '不成立', after: '成立', changed: true }] },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TracePanel', () => {
  it('逆引きで条件・判定・理由を示し、入力の状態を送る', async () => {
    api.traceTargets.mockResolvedValue(targets)
    api.traceBackward.mockResolvedValue(backward)
    const view = render(<MemoryRouter><TracePanel projectIds={[7]} /></MemoryRouter>)
    await waitFor(() => expect(api.traceTargets).toHaveBeenCalledWith([7]))
    fireEvent.change(view.getByLabelText('対象'), { target: { value: 'X R100' } })
    fireEvent.change(await view.findByLabelText('PB1 の状態'), { target: { value: 'on' } })
    fireEvent.click(view.getByText('条件算出'))
    await view.findByText('¬STOP ∧ (PB1 ∨ X R100)')
    expect(api.traceBackward).toHaveBeenCalledWith([7], 'X R100', { PB1: true })
    expect(view.getAllByText('判定不能').length).toBeGreaterThan(0)
    expect(view.getByText(/外部入力で、状態が与えられていません/)).toBeTruthy()
  })

  it('順引きで影響先と状態の変化を示す', async () => {
    api.traceTargets.mockResolvedValue(targets)
    api.traceForward.mockResolvedValue(forward)
    const view = render(<MemoryRouter><TracePanel projectIds={[7]} /></MemoryRouter>)
    await waitFor(() => expect(api.traceTargets).toHaveBeenCalled())
    fireEvent.click(view.getByLabelText(/順引き/))
    await waitFor(() => expect(view.getByLabelText('起点')).toBeTruthy())
    fireEvent.change(view.getByLabelText('起点'), { target: { value: 'PB1' } })
    fireEvent.click(view.getByText('影響追跡'))
    await view.findByText('PB1 が変わると影響する出力（1）')
    expect(view.getByText('一部の枝だけ（ほかの枝しだい）')).toBeTruthy()
    expect(view.getByText('成立')).toBeTruthy()
  })
})
