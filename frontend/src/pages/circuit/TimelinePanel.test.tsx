// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CircuitSummary, CircuitTimeline } from '../../types'
import realSummary from '../../test/fixtures/circuit-summary-timer.json'
import realTimeline from '../../test/fixtures/circuit-timeline.json'
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

  it('renders the real API response including the empty initial/end records (tests/test_api_contract.py)', async () => {
    api.circuitTimeline.mockResolvedValue(realTimeline)
    const view = render(<TimelinePanel projectIds={[1]} summary={realSummary as unknown as CircuitSummary} />)
    fireEvent.click(view.getByRole('button', { name: /実行/ }))
    await waitFor(() => expect(view.getByText('終了')).toBeTruthy())
    expect(view.getByText('初期状態')).toBeTruthy()
    expect(view.getByText('リレー 2T TM1: OFF → ON')).toBeTruthy()
    expect(view.getByText('負荷 RL: OFF → ON')).toBeTruthy()
  })

  it('still renders records whose changes are missing (older servers)', async () => {
    api.circuitTimeline.mockResolvedValue({ timers: [], timeline: [{ ...timeline.timeline[0], changes: {} }] })
    const view = render(<TimelinePanel projectIds={[7]} summary={summary} />)
    fireEvent.click(view.getByRole('button', { name: /実行/ }))
    await waitFor(() => expect(view.getByText('初期状態')).toBeTruthy())
  })

  it('shows the API error and edits the rows', async () => {
    api.circuitTimeline.mockRejectedValueOnce(new Error('タイマー NOPE が回路にありません')).mockRejectedValueOnce('文字列の失敗')
    const view = render(<TimelinePanel projectIds={[7]} summary={summary} />)
    fireEvent.change(view.getByLabelText('時刻（秒）'), { target: { value: '2.5' } })
    fireEvent.change(view.getByLabelText('ON/OFF'), { target: { value: 'off' } })
    fireEvent.change(view.getByLabelText('入力'), { target: { value: 'PB1' } })
    fireEvent.change(view.getByLabelText('終了（秒）'), { target: { value: '30' } })
    fireEvent.click(view.getByRole('button', { name: /実行/ }))
    expect((await view.findByRole('alert')).textContent).toBe('タイマー NOPE が回路にありません')
    expect(api.circuitTimeline).toHaveBeenCalledWith([7], [{ at: 2.5, inputs: { PB1: false } }], {}, 30)
    fireEvent.click(view.getByRole('button', { name: /実行/ }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toBe('文字列の失敗'))
  })

  it('renders nothing without inputs and adds the first row from 0 s when all rows were removed', () => {
    const empty = render(<TimelinePanel projectIds={[7]} summary={{ ...summary, inputs: [], timers: undefined }} />)
    expect(empty.container.innerHTML).toBe('')
    empty.unmount()
    const view = render(<TimelinePanel projectIds={[7]} summary={{ ...summary, timers: undefined }} />)
    fireEvent.click(view.getByLabelText('行を削除'))
    fireEvent.click(view.getByRole('button', { name: /行を追加/ }))
    expect((view.getByLabelText('時刻（秒）') as HTMLInputElement).value).toBe('1')
  })

  it('adds and removes operations', () => {
    const view = render(<TimelinePanel projectIds={[7]} summary={summary} />)
    fireEvent.click(view.getByRole('button', { name: /行を追加/ }))
    expect(view.getAllByLabelText('時刻（秒）')).toHaveLength(2)
    fireEvent.click(view.getAllByLabelText('行を削除')[0])
    expect(view.getAllByLabelText('時刻（秒）')).toHaveLength(1)
  })
})
