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

  it('操作タブの ON / OFF を初期値にし、入力の状態を変えると出ている結果を計算し直す', async () => {
    api.traceTargets.mockResolvedValue({
      ...targets,
      devices: [...targets.devices, { device: 'STOP', source: 'input', source_label: '押しボタン（入力）', reason: '人が操作する', contact_count: 1 }],
    })
    api.traceBackward.mockResolvedValue(backward)
    api.traceForward.mockResolvedValue(forward)
    const view = render(<MemoryRouter><TracePanel projectIds={[7]} inputs={{ PB1: true, STOP: false }} /></MemoryRouter>)
    const pb1 = await view.findByLabelText('PB1 の状態') as HTMLSelectElement
    expect(pb1.value).toBe('on')
    expect((view.getByLabelText('STOP の状態') as HTMLSelectElement).value).toBe('off')
    // 結果が出ていなければ、状態を変えても計算しない
    fireEvent.change(pb1, { target: { value: 'off' } })
    expect(api.traceBackward).not.toHaveBeenCalled()

    fireEvent.change(view.getByLabelText('対象'), { target: { value: 'X R100' } })
    fireEvent.click(view.getByText('条件算出'))
    await view.findByText('¬STOP ∧ (PB1 ∨ X R100)')
    fireEvent.change(pb1, { target: { value: 'unknown' } })
    await waitFor(() => expect(api.traceBackward).toHaveBeenLastCalledWith([7], 'X R100', { PB1: null, STOP: false }))

    fireEvent.click(view.getByLabelText(/順引き/))
    fireEvent.change(view.getByLabelText('PB1 の状態'), { target: { value: 'on' } })
    expect(api.traceForward).not.toHaveBeenCalled()
    fireEvent.change(view.getByLabelText('起点'), { target: { value: 'PB1' } })
    fireEvent.click(view.getByText('影響追跡'))
    await view.findByText('PB1 が変わると影響する出力（1）')
    fireEvent.change(view.getByLabelText('PB1 の状態'), { target: { value: 'off' } })
    await waitFor(() => expect(api.traceForward).toHaveBeenLastCalledWith([7], 'PB1', { PB1: false, STOP: false }))
    expect(api.traceBackward).toHaveBeenCalledTimes(2)
  })

  it('枝の接点の状態・戻り側・b 接点・電源直結と、辿った元の木を出す', async () => {
    api.traceTargets.mockResolvedValue(targets)
    const contact = backward.branches[0].contacts[0]
    api.traceBackward.mockResolvedValue({
      ...backward,
      status: '成立',
      reasons: [],
      branches: [
        { ...backward.branches[0], status: '成立', contacts: [
          { ...contact, symbol_ref: 'A', conducts: true },
          { ...contact, symbol_ref: 'B', device: 'STOP', kind: 'b', conducts: false, side: '戻り側' },
        ] },
        { ...backward.branches[0], status: '不成立', contacts: [] },
      ],
      explain: {
        target: 'X R100', kind: 'relay', text: '', note: '自己保持',
        inputs: [
          { device: 'PB1', source: 'input', source_label: '押しボタン（入力）', reason: '人が操作する' },
          { device: 'LS1', source: 'input', source_label: '外部入力', reason: '' },
        ],
        relays: [{ target: 'Y1', kind: 'relay', text: 'LS1', inputs: undefined, relays: undefined }],
      },
    } as unknown as TraceBackward)
    const view = render(<MemoryRouter><TracePanel projectIds={[7]} /></MemoryRouter>)
    await waitFor(() => expect(api.traceTargets).toHaveBeenCalled())
    fireEvent.change(view.getByLabelText('対象'), { target: { value: 'X R100' } })
    fireEvent.click(view.getByText('条件算出'))
    const result = await view.findByLabelText('逆引きの結果')
    expect(view.getByTitle('ER1 B（戻り側）').textContent).toBe('¬STOP↓')
    expect(view.getByTitle('ER1 A（電源側）').className).toContain('emerald')
    expect(view.getByTitle('ER1 B（戻り側）').className).toContain('rose')
    expect(view.getByText('接点なし（電源に直結）')).toBeTruthy()
    expect(result.textContent).toContain('（自己保持）')
    expect(result.textContent).toContain('PB1: 押しボタン（入力）（人が操作する）')
    expect(result.textContent).toContain('Y1= LS1')
  })

  it('深すぎる説明の木は 8 段で止め、状態の変化が無い影響先は - にする', async () => {
    type Node = { target: string; kind: string; text: string; relays?: Node[] }
    let node: Node = { target: 'R10', kind: 'relay', text: '' }
    for (let i = 9; i >= 0; i -= 1) node = { target: `R${i}`, kind: 'relay', text: '', relays: [node] }
    api.traceTargets.mockResolvedValue(targets)
    api.traceBackward.mockResolvedValue({ ...backward, explain: node } as unknown as TraceBackward)
    api.traceForward.mockResolvedValue({
      ...forward,
      affected: [...forward.affected, { ...forward.affected[0], target: 'Z9' }],
      state_change: { from: false, to: true, targets: undefined, skipped: '入力ではないので状態の変化は出しません' },
    } as unknown as TraceForward)
    const view = render(<MemoryRouter><TracePanel projectIds={[7]} /></MemoryRouter>)
    await waitFor(() => expect(api.traceTargets).toHaveBeenCalled())
    fireEvent.change(view.getByLabelText('対象'), { target: { value: 'X R100' } })
    fireEvent.click(view.getByText('条件算出'))
    const result = await view.findByLabelText('逆引きの結果')
    expect(result.textContent).toContain('R8')
    expect(result.textContent).not.toContain('R9')

    fireEvent.click(view.getByLabelText(/順引き/))
    fireEvent.change(view.getByLabelText('起点'), { target: { value: 'PB1' } })
    fireEvent.click(view.getByText('影響追跡'))
    expect(await view.findByText('入力ではないので状態の変化は出しません')).toBeTruthy()
    expect(view.getAllByText('-')).toHaveLength(2)
    fireEvent.click(view.getByLabelText(/逆引き/))
    expect(view.getByLabelText('逆引きの結果')).toBeTruthy()
  })

  it('対応表・CVAT を書き出し、失敗は理由を出す。図面が無ければ候補を読まない', async () => {
    api.traceTargets.mockRejectedValueOnce(new Error('回路を組めません'))
    const view = render(<MemoryRouter><TracePanel projectIds={[7]} /></MemoryRouter>)
    expect((await view.findByRole('alert')).textContent).toBe('回路を組めません')
    api.downloadAnnotationTable.mockResolvedValue({ name: 'a.xlsx' })
    api.downloadCvat.mockRejectedValueOnce('文字列の失敗')
    fireEvent.click(view.getByRole('button', { name: /対応表/ }))
    await waitFor(() => expect(view.queryByRole('alert')).toBeNull())
    expect(api.downloadAnnotationTable).toHaveBeenCalledWith([7])
    fireEvent.click(view.getByRole('button', { name: /CVAT/ }))
    expect((await view.findByRole('alert')).textContent).toBe('文字列の失敗')
    view.unmount()

    api.traceTargets.mockRejectedValueOnce('文字列')
    const second = render(<MemoryRouter><TracePanel projectIds={[7]} /></MemoryRouter>)
    expect((await second.findByRole('alert')).textContent).toBe('文字列')
    second.unmount()

    const empty = render(<MemoryRouter><TracePanel projectIds={[]} /></MemoryRouter>)
    expect(api.traceTargets).toHaveBeenCalledTimes(2)
    expect((empty.getByRole('button', { name: /対応表/ }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(empty.getByLabelText(/順引き/))
    expect(empty.getByLabelText('起点').children).toHaveLength(1)
    empty.unmount()

    api.traceTargets.mockResolvedValue(targets)
    api.downloadAnnotationTable.mockRejectedValueOnce(new Error('書き出せません'))
    const third = render(<MemoryRouter><TracePanel projectIds={[7]} /></MemoryRouter>)
    fireEvent.click(third.getByRole('button', { name: /対応表/ }))
    expect((await third.findByRole('alert')).textContent).toBe('書き出せません')
  })
})

