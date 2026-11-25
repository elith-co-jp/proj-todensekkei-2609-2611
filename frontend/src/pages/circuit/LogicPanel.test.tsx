// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { LogicDiagramView, LogicSimulation } from '../../types'
import LogicPanel from './LogicPanel'

const api = vi.hoisted(() => ({ logicDiagram: vi.fn(), logicSimulate: vi.fn(), downloadLogicSvg: vi.fn() }))
vi.mock('../../api/client', () => ({ api }))

const view = (values: Record<string, boolean> = {}): LogicDiagramView => ({
  title: 'EV31036',
  outputs: ['6X P33P22'],
  signals: [
    { id: 'D009 B413', kind: 'input', devices: ['D009 B413', '83DX P33P22'], name: '主制御装置選択指令', ref_name: '主制御装置選択', letter: 'A', output: false, default: false },
    { id: 'D001 B405', kind: 'input', devices: ['D001 B405'], name: '起動指令（個別制御装置）', ref_name: '起動指令（個別制御装置）', letter: null, output: false, default: false },
    { id: '6X P33P22', kind: 'logic', devices: ['6X P33P22'], name: '起動指令', ref_name: '起動指令', letter: null, output: true, default: false },
  ],
  blocks: [
    { signal: 'D009 B413', letter: 'A', name: '主制御装置選択', kind: 'input', output: false, devices: ['D009 B413'], text: '入力' },
    { signal: '6X P33P22', letter: null, name: '起動指令', kind: 'logic', output: true, devices: ['6X P33P22'], text: 'A ∧ 起動指令' },
  ],
  inputs: [
    { signal: 'D009 B413', name: '主制御装置選択指令', devices: ['D009 B413'], default: false, kind: 'input', source: 'input', source_label: '外部入力' },
    { signal: 'D001 B405', name: '起動指令（個別制御装置）', devices: ['D001 B405'], default: false, kind: 'input', source: 'input', source_label: '外部入力' },
  ],
  diagnostics: [],
  svg: '<svg data-x="1"><text>起動指令</text></svg>',
  formulas: '起動指令（6X） = A:主制御装置選択 ∧ 起動指令（個別制御装置）',
  values,
  warnings: [],
  candidates: [{ device: '6X P33P22', default: true }, { device: '5X P33P22', default: false }],
  default_targets: ['6X P33P22'],
})

const sim: LogicSimulation = {
  timeline: [
    { t: 0, label: '初期状態', values: {}, changes: [], warnings: [] },
    { t: 1, label: '主制御装置選択指令=ON', values: { 'D009 B413': true, '6X P33P22': false }, changes: [], warnings: [] },
    { t: 2, label: '起動指令（個別制御装置）=ON', values: { 'D009 B413': true, 'D001 B405': true, '6X P33P22': true },
      changes: [{ signal: '6X P33P22', name: '起動指令', before: false, after: true }], warnings: [] },
  ],
  waves: { '6X P33P22': [[0, false], [2, true]], 'D009 B413': [[0, false], [1, true]] },
  end: 2,
  names: {},
  letters: { 'D009 B413': 'A' },
  compare: { agree: true, checked: 12, mismatches: [] },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('LogicPanel', () => {
  it('draws the diagram, toggles an input and re-renders with the new state', async () => {
    api.logicDiagram.mockResolvedValueOnce(view()).mockResolvedValueOnce(view({ 'D009 B413': true }))
    const ui = render(<LogicPanel projectIds={[1, 2]} />)
    await waitFor(() => expect(ui.getByTestId('logic-svg').innerHTML).toContain('起動指令'))
    expect(api.logicDiagram).toHaveBeenCalledWith([1, 2], {})
    fireEvent.click(ui.getByRole('button', { name: /主制御装置選択指令/ }))
    await waitFor(() => expect(api.logicDiagram).toHaveBeenCalledTimes(2))
    expect(api.logicDiagram.mock.calls[1][1]).toEqual({ targets: ['6X P33P22'], inputs: { 'D009 B413': true } })
    fireEvent.click(ui.getByText('式で見る'))
    expect(ui.getByText(/A:主制御装置選択 ∧/)).toBeTruthy()
  })

  it('runs timed operations, shows the comparison with the circuit and steps through the record', async () => {
    api.logicDiagram.mockResolvedValue(view())
    api.logicSimulate.mockResolvedValue(sim)
    const ui = render(<LogicPanel projectIds={[1]} />)
    await waitFor(() => expect(ui.getAllByLabelText('時刻（秒）')).toHaveLength(1))
    fireEvent.click(ui.getByRole('button', { name: /行を追加/ }))
    fireEvent.change(ui.getAllByLabelText('入力')[1], { target: { value: 'D001 B405' } })
    fireEvent.click(ui.getByRole('button', { name: /実行/ }))
    await waitFor(() => expect(ui.getByText('回路の計算と一致（12 件を照合）')).toBeTruthy())
    expect(api.logicSimulate).toHaveBeenCalledWith([1], [
      { at: 1, inputs: { 'D009 B413': true } }, { at: 2, inputs: { 'D001 B405': true } },
    ], { targets: ['6X P33P22'], inputs: {}, until: 10 })
    expect(ui.getByText('起動指令: OFF → ON')).toBeTruthy()
    fireEvent.click(ui.getByText('主制御装置選択指令=ON'))
    await waitFor(() => expect(api.logicDiagram).toHaveBeenLastCalledWith([1], { targets: ['6X P33P22'], values: sim.timeline[1].values }))
  })

  it('lets the user choose outputs and rebuild', async () => {
    api.logicDiagram.mockResolvedValue(view())
    const ui = render(<LogicPanel projectIds={[1]} />)
    await waitFor(() => expect(ui.getByRole('button', { name: /出力を選ぶ/ })).toBeTruthy())
    fireEvent.click(ui.getByRole('button', { name: /出力を選ぶ/ }))
    fireEvent.click(ui.getByLabelText('5X P33P22'))
    fireEvent.click(ui.getByRole('button', { name: '作り直す' }))
    await waitFor(() => expect(api.logicDiagram).toHaveBeenLastCalledWith([1], { targets: ['6X P33P22', '5X P33P22'], inputs: {} }))
  })
})
