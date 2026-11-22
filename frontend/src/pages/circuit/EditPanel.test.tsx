// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CircuitEditPreview } from '../../types'
import EditPanel from './EditPanel'

const api = vi.hoisted(() => ({
  circuitEditPreview: vi.fn(),
  circuitEditApply: vi.fn(),
  circuitEditHistory: vi.fn(),
  circuitEditUndo: vi.fn(),
}))
vi.mock('../../api/client', () => ({ api, BASE: '' }))

const ops = '[{"op": "set_label", "symbol_ref": "S1", "label": "R2"}]'
const preview: CircuitEditPreview = {
  ok: true, base_hash: 'h0', after_hash: 'h1', notes: ['S1 の名前を R1 → R2'],
  diff: [], netlist_diff: '--- 変更前\n+++ 変更後\n-a\n+b',
  circuit: { relays_added: ['R2'], relays_removed: [], inputs_added: [], inputs_removed: [], timers_changed: [] },
  behaviour: [{ inputs: 'PB1=ON', coils: [{ device: 'R1', before: true, after: false }], loads: [], warnings: [] }],
  scenarios: 4, impact: [{ device: 'R1', devices: ['R3'], loads: ['RL'] }],
  validation: { diagnostics_added: [], issues_added: [], issues_resolved: [], errors: 0 },
}
const history = [{ id: 3, title: '名前の変更', reason: null, author: 'llm', status: 'applied', project_ids: [7],
  ops: [{ op: 'set_label' }], notes: [], summary: {}, created_at: null, undone_at: null }]

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('EditPanel', () => {
  it('試すと差分・動作の違い・波及範囲を出し、適用すると試したハッシュで送る', async () => {
    api.circuitEditHistory.mockResolvedValue({ change_sets: [] })
    api.circuitEditPreview.mockResolvedValue(preview)
    api.circuitEditApply.mockResolvedValue({ change_set: history[0], preview })
    const onApplied = vi.fn()
    const view = render(<EditPanel projectIds={[7]} onApplied={onApplied} />)
    const apply = view.getByRole('button', { name: /適用/ })
    fireEvent.change(view.getByLabelText('操作（JSON）'), { target: { value: ops } })
    expect(apply).toHaveProperty('disabled', true)
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    await waitFor(() => expect(view.getByText(/1 通りで違う/)).toBeTruthy())
    expect(api.circuitEditPreview).toHaveBeenCalledWith([7], [{ op: 'set_label', symbol_ref: 'S1', label: 'R2' }])
    expect(view.getByText('R1 ON→OFF')).toBeTruthy()
    expect(view.getByText(/R1 → R3、RL/)).toBeTruthy()
    expect(view.getByText(/リレーが増える: R2/)).toBeTruthy()
    fireEvent.click(apply)
    await waitFor(() => expect(onApplied).toHaveBeenCalled())
    expect(api.circuitEditApply.mock.calls[0][4]).toBe('h0')
  })

  it('操作の誤りを番号つきで出し、JSON が読めなければ送らない', async () => {
    api.circuitEditHistory.mockResolvedValue({ change_sets: [] })
    api.circuitEditPreview.mockResolvedValue({ ok: false, base_hash: 'h0', errors: [{ index: 1, op: 'connect', message: '記号 X がありません' }] })
    const view = render(<EditPanel projectIds={[7]} />)
    fireEvent.change(view.getByLabelText('操作（JSON）'), { target: { value: '[{' } })
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    expect(view.getByRole('alert').textContent).toContain('JSON を読めません')
    expect(api.circuitEditPreview).not.toHaveBeenCalled()
    fireEvent.change(view.getByLabelText('操作（JSON）'), { target: { value: ops } })
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    await waitFor(() => expect(view.getByText(/操作 2（connect）: 記号 X がありません/)).toBeTruthy())
    expect(view.getByRole('button', { name: /適用/ })).toHaveProperty('disabled', true)
  })

  it('履歴から取り消し、競合したら「それでも戻す」で強制できる', async () => {
    api.circuitEditHistory.mockResolvedValue({ change_sets: history })
    api.circuitEditUndo.mockRejectedValueOnce(new Error('その後に図面が変わっています')).mockResolvedValue({ change_set: history[0] })
    const view = render(<EditPanel projectIds={[7]} />)
    await waitFor(() => expect(view.getByText('名前の変更')).toBeTruthy())
    fireEvent.click(view.getByRole('button', { name: /元に戻す/ }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('図面が変わっています'))
    fireEvent.click(view.getByRole('button', { name: 'それでも戻す' }))
    await waitFor(() => expect(api.circuitEditUndo).toHaveBeenLastCalledWith(3, true))
  })

  it('全部の種類の結果を出し、{"ops": [...]} の形・件名と理由も受け付ける', async () => {
    api.circuitEditHistory.mockRejectedValueOnce(new Error('履歴なし'))
    api.circuitEditPreview.mockResolvedValue({
      ...preview,
      notes: undefined,
      circuit: { relays_added: ['R2'], relays_removed: ['R1'], inputs_added: ['PB2'], inputs_removed: ['PB1'], timers_changed: ['T1'] },
      behaviour: [
        { inputs: 'a', coils: [], loads: [], warnings: [] },
        { inputs: 'b', coils: [{ device: 'R1', before: null, after: true }], loads: [{ symbol_ref: 'L1', label: null, before: false, after: true }],
          warnings: ['短絡'], resolved_warnings: ['無限ループ'] },
        { inputs: 'c', coils: [], loads: [{ symbol_ref: 'L2', label: 'ランプ', before: true, after: false }], warnings: [] },
        { inputs: 'd', coils: [], loads: [], warnings: ['w'] },
        { inputs: 'e', coils: [], loads: [], warnings: [], resolved_warnings: ['r'] },
      ],
      scenarios: undefined,
      impact: [{ device: 'R9', devices: [], loads: [] }],
      validation: {
        diagnostics_added: [{ kind: 'short', message: null }, { kind: 'loop', message: '輪になっています' }],
        issues_added: [{ title: '指摘', message: '内容' }],
        issues_resolved: [{ title: '直った' }],
        errors: 1,
      },
      netlist_diff: null,
    } as unknown as CircuitEditPreview)
    api.circuitEditApply.mockResolvedValue({ change_set: history[0], preview })
    api.circuitEditHistory.mockResolvedValue({ change_sets: [{ ...history[0], reason: '試験のため', status: 'undone' }] })
    const view = render(<EditPanel projectIds={[7]} />)
    fireEvent.change(view.getByLabelText('操作（JSON）'), { target: { value: `{"ops": ${ops}}` } })
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    await waitFor(() => expect(view.getByText(/4 通りで違う/)).toBeTruthy())
    const result = view.getByLabelText('変更の結果').textContent!
    for (const line of ['リレーが無くなる: R1', '入力が増える: PB2', '入力が無くなる: PB1', 'タイマーが変わる: T1',
      'R1 —→ON', 'L1 OFF→ON', 'ランプ ON→OFF', '⚠ 短絡', '✓ 解消: 無限ループ', '入力の組み合わせ 0 通り', 'R9 → なし',
      '＋ 指摘: 内容', '✓ 直った', '⚠ short', '⚠ 輪になっています', '新しい指摘 1 件（うち誤り 1）']) {
      expect(result).toContain(line)
    }
    expect(view.queryByText(/つながりの差分/)).toBeNull()

    fireEvent.change(view.getByLabelText('件名'), { target: { value: '名前の変更' } })
    fireEvent.change(view.getByLabelText('理由'), { target: { value: '試験のため' } })
    fireEvent.click(view.getByRole('button', { name: /適用/ }))
    await waitFor(() => expect(view.getByText('戻し済み')).toBeTruthy())
    expect(api.circuitEditApply).toHaveBeenCalledWith([7], [{ op: 'set_label', symbol_ref: 'S1', label: 'R2' }], '名前の変更', '試験のため', 'h0')
    expect(view.getByText('名前の変更（試験のため）')).toBeTruthy()
  })

  it('変化が無い試行・誤りの中身が無い試行・配列でない JSON・送信の失敗を扱う', async () => {
    api.circuitEditHistory.mockResolvedValue({ change_sets: [] })
    api.circuitEditPreview
      .mockResolvedValueOnce({ ok: true, base_hash: 'h0', notes: [], behaviour: undefined, circuit: null, impact: undefined, validation: null })
      .mockResolvedValueOnce({ ok: false, base_hash: 'h0', errors: undefined })
      .mockResolvedValueOnce({ ok: false, base_hash: 'h0', errors: [{ index: 0, op: null, message: '形が違います' }] })
      .mockRejectedValueOnce('文字列の失敗')
    const view = render(<EditPanel projectIds={[7]} />)
    fireEvent.change(view.getByLabelText('操作（JSON）'), { target: { value: ops } })
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    expect(await view.findByText(/違いはありません/)).toBeTruthy()
    // 試した後に JSON を変えたら、試し直すまで適用できない
    expect(view.getByRole('button', { name: /適用/ })).toHaveProperty('disabled', false)
    fireEvent.change(view.getByLabelText('操作（JSON）'), { target: { value: `${ops} ` } })
    expect(view.getByRole('button', { name: /適用/ })).toHaveProperty('disabled', true)

    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    await waitFor(() => expect(view.getByLabelText('操作の誤り').children).toHaveLength(0))
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    expect(await view.findByText('操作 1（?）: 形が違います')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('文字列の失敗'))

    fireEvent.change(view.getByLabelText('操作（JSON）'), { target: { value: '{"op": "x"}' } })
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    expect(view.getByRole('alert').textContent).toContain('操作の配列（または {"ops": [...]}）を入れてください')
    fireEvent.change(view.getByLabelText('操作（JSON）'), { target: { value: 'null' } })
    fireEvent.click(view.getByRole('button', { name: /試行/ }))
    expect(view.getByRole('alert').textContent).toContain('操作の配列')
  })
})

