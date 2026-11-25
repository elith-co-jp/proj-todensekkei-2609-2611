// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { DemoView, ProjectRow } from '../types'
import DemoPage from './DemoPage'

const api = vi.hoisted(() => ({
  listProjects: vi.fn(),
  demoView: vi.fn(),
  createProjects: vi.fn(),
  startAnalysisJob: vi.fn(),
  getAnalysisJob: vi.fn(),
  imageUrl: (id: number) => `/img/${id}`,
}))
vi.mock('../api/client', () => ({ api, BASE: '' }))

const rows = [
  { id: 1, name: 'a', sheet_no: 'EV31031', image_width: 1000, image_height: 700, symbol_count: 0, analysis_wire_count: 40 },
  { id: 2, name: 'b', sheet_no: 'EV31036', image_width: 1000, image_height: 700, symbol_count: 0, analysis_wire_count: null },
] as ProjectRow[]

const IN = 'D009 B413'
const OUT = '6X P33P22'

function demo(on: boolean): DemoView {
  return {
    ready: true,
    pages: [{ id: 1, name: 'EV31031', source: 'ai', symbols: 30, connections: 40, width: 1000, height: 700 }],
    simulation: {
      engine: 'builtin', pages: [{ id: 1, name: 'EV31031' }], element_count: 3, node_count: 3, supplies: [], coils: [],
      inputs: [{ device: IN, contacts: [{ project_id: 1, symbol_ref: 'SYM-0001', kind: 'a' }] }], diagnostics: [],
      steps: 1, warnings: [], coil_states: { [OUT]: { energized: on, voltage: on ? 110 : 0 } },
      overlay: [{ project_id: 1, wires: [{ x1: 0.1, y1: 0.1, x2: 0.1, y2: 0.5, live: on }], contacts: [], coils: [] }],
    },
    logic: {
      title: 'EV31031', outputs: [OUT], diagnostics: [], warnings: [],
      signals: [
        { id: IN, kind: 'input', devices: [IN], name: '主制御装置選択指令', ref_name: '主制御装置選択', letter: 'A', output: false, default: false },
        { id: OUT, kind: 'logic', devices: [OUT], name: '起動指令', ref_name: '起動指令', letter: null, output: true, default: false },
      ],
      blocks: [],
      inputs: [{ signal: IN, name: '主制御装置選択指令', devices: [IN], default: false, kind: 'input', source: 'input', source_label: '外部入力' }],
      svg: `<svg><g data-sig="${OUT}"><rect width="10" height="10"/><text>起動指令</text></g></svg>`,
      values: { [IN]: on, [OUT]: on },
      locations: {
        [OUT]: [
          { project_id: 1, page: 'EV31031', symbol_ref: 'SYM-0009', device: OUT, role: 'coil', contact: null, negated: false, label: '6X', cx: 0.5, cy: 0.5, w: 0.02, h: 0.02 },
          { project_id: 1, page: 'EV31031', symbol_ref: 'SYM-0010', device: OUT, role: 'contact', contact: 'a', negated: false, label: '6X', cx: 0.6, cy: 0.5, w: 0.02, h: 0.02 },
        ],
      },
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listProjects.mockResolvedValue(rows)
  api.demoView.mockImplementation(async (_ids: number[], inputs: Record<string, boolean>) => demo(Boolean(inputs[IN])))
})

afterEach(() => cleanup())

describe('DemoPage', () => {
  it('AI 解析だけの図面でデモを開き、論理図の部品から図面の位置を示し、スイッチで両方の線が通じる', async () => {
    const ui = render(<MemoryRouter><DemoPage /></MemoryRouter>)
    expect(await ui.findByText('AI 解析のみ')).toBeTruthy()
    expect(ui.getByText('未解析')).toBeTruthy()
    fireEvent.click(ui.getAllByRole('checkbox')[0])
    fireEvent.click(ui.getByRole('button', { name: /デモを開く/ }))
    await waitFor(() => expect(api.demoView).toHaveBeenCalledWith([1], {}, {}))
    expect(await ui.findByText(/人の確認前の AI 解析の結果/)).toBeTruthy()

    // 論理図の出力の箱を押すと、図面の上のコイルと接点に枠が付く
    fireEvent.click(ui.getByTestId('demo-logic').querySelector('rect')!)
    await waitFor(() => expect(ui.getAllByTestId('viewer-mark')).toHaveLength(2))
    expect(ui.getByText(/6X P33P22 のコイル/)).toBeTruthy()
    expect(ui.getByTestId('demo-logic').querySelector('g[data-selected]')).toBeTruthy()

    // スイッチを入れると、前の励磁状態を渡して解き直す
    fireEvent.click(ui.getByRole('button', { name: /主制御装置選択指令 OFF/ }))
    await waitFor(() => expect(api.demoView).toHaveBeenLastCalledWith([1], { [IN]: true }, { [OUT]: false }))
    expect(await ui.findByRole('button', { name: /主制御装置選択指令 ON/ })).toBeTruthy()
  })

  it('未解析の図面は AI 解析を始められる', async () => {
    api.startAnalysisJob.mockResolvedValue({ id: 3, status: 'running', total: 1, done: 0, current: 'b' })
    const ui = render(<MemoryRouter><DemoPage /></MemoryRouter>)
    await ui.findByText('未解析')
    fireEvent.click(ui.getAllByRole('checkbox')[1])
    fireEvent.click(ui.getByRole('button', { name: /AI 解析（1 枚）/ }))
    await waitFor(() => expect(api.startAnalysisJob).toHaveBeenCalledWith([2]))
    expect(await ui.findByText(/解析中 0 \/ 1/)).toBeTruthy()
  })
})
