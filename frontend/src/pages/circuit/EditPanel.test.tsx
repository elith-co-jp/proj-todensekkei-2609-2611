// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CircuitEditPreview } from '../../types'
import EditPanel from './EditPanel'

const api = vi.hoisted(() => ({
  circuitEditPreview: vi.fn(),
  circuitEditApply: vi.fn(),
  circuitEditHistory: vi.fn(),
  circuitEditUndo: vi.fn(),
  deleteCircuitChangeSet: vi.fn(),
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

  it('履歴は確認してから消す。適用中の変更は戻せなくなることを伝える', async () => {
    api.circuitEditHistory.mockResolvedValueOnce({ change_sets: history }).mockResolvedValue({ change_sets: [] })
    api.deleteCircuitChangeSet.mockResolvedValue({ deleted: 3 })
    const view = render(<EditPanel projectIds={[7]} />)
    fireEvent.click(await view.findByRole('button', { name: '変更 #3 を履歴から削除' }))
    const dialog = await view.findByRole('dialog')
    expect(dialog.textContent).toContain('「元に戻す」ができなくなります')
    expect(within(dialog).getByRole('list', { name: '残るもの' }).textContent).toContain('今の図面')
    fireEvent.click(within(dialog).getByRole('button', { name: /削除する/ }))
    await waitFor(() => expect(api.deleteCircuitChangeSet).toHaveBeenCalledWith(3))
    await waitFor(() => expect(view.queryByRole('list', { name: '変更の履歴' })).toBeNull())
  })
})
