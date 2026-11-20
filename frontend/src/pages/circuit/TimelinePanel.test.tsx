// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CircuitSummary, CircuitTimeline } from '../../types'
import TimelinePanel from './TimelinePanel'

const api = vi.hoisted(() => ({ circuitTimeline: vi.fn() }))
vi.mock('../../api/client', () => ({ api }))

const summary: CircuitSummary = {
  engine: 'builtin',
  pages: [{ id: 7, name: 'ER21216' }],
  element_count: 5,
  node_count: 4,
  supplies: ['P'],
  coils: [{ device: '2T TM1', symbols: [{ project_id: 7, symbol_ref: 'TC' }], contacts: [] }],
  inputs: [{ device: 'PB1', contacts: [] }],
  diagnostics: [],
  timers: [{ device: '2T TM1', mode: 'on_delay', delay_s: 5 }],
}

const noChange = { coils: [], loads: [], contacts: [] }
const timeline: CircuitTimeline = {
  timers: [{ device: '2T TM1', mode: 'on_delay', delay_s: 8 }],
  timeline: [
    { t: 0, label: '初期状態', inputs: {}, coils: {}, timers: { '2T TM1': { output: false, due: null } }, changes: noChange, warnings: [] },
    { t: 1, label: 'PB1=ON', inputs: { PB1: true }, coils: { '2T TM1': true }, timers: { '2T TM1': { output: false, due: 9 } },
      changes: { ...noChange, coils: [{ device: '2T TM1', before: false, after: true }] }, warnings: [] },
    { t: 9, label: '2T TM1 の時間が経過', inputs: { PB1: true }, coils: { '2T TM1': true }, timers: { '2T TM1': { output: true, due: null } },
      changes: { ...noChange, loads: [{ project_id: 7, symbol_ref: 'L', label: 'RL', device: null, page: 'ER21216', before: false, after: true }] },
      warnings: [] },
  ],
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TimelinePanel', () => {
  it('sends timed inputs with a changed timer delay and lists what happens over time', async () => {
    api.circuitTimeline.mockResolvedValue(timeline)
    const view = render(<TimelinePanel projectIds={[7]} summary={summary} />)
    expect(view.getByText('オンディレー')).toBeTruthy()
    fireEvent.change(view.getByLabelText('2T TM1 の時間（秒）'), { target: { value: '8' } })
    expect(view.getByText('（図面は 5 秒）')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /実行/ }))
    await waitFor(() => expect(view.getByText('2T TM1 の時間が経過')).toBeTruthy())
    expect(api.circuitTimeline).toHaveBeenCalledWith([7], [{ at: 1, inputs: { PB1: true } }], { '2T TM1': 8 }, 20)
    expect(view.getByText('2T TM1 は 9 秒に動く予定')).toBeTruthy()
    expect(view.getByText('負荷 RL: OFF → ON')).toBeTruthy()
  })

  it('adds and removes operations', () => {
    const view = render(<TimelinePanel projectIds={[7]} summary={summary} />)
    fireEvent.click(view.getByRole('button', { name: /行を追加/ }))
    expect(view.getAllByLabelText('時刻（秒）')).toHaveLength(2)
    fireEvent.click(view.getAllByLabelText('行を削除')[0])
    expect(view.getAllByLabelText('時刻（秒）')).toHaveLength(1)
  })
})
