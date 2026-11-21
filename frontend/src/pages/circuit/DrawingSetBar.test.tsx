// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DrawingSetBar from './DrawingSetBar'

const api = vi.hoisted(() => ({
  listDrawingSets: vi.fn(),
  drawingSetCoverage: vi.fn(),
  createDrawingSet: vi.fn(),
  updateDrawingSet: vi.fn(),
  suggestDrawingSets: vi.fn(),
}))
vi.mock('../../api/client', () => ({ api, BASE: '' }))

const set = { id: 3, name: 'DS01', note: null, project_ids: [1], created_at: null, updated_at: null }

beforeEach(() => {
  vi.clearAllMocks()
  api.listDrawingSets.mockResolvedValue([set])
  api.drawingSetCoverage.mockResolvedValue({
    set,
    missing_sheets: [{
      sheet: 'ER21212',
      referenced_by: [{ project_id: 1, name: 'a', target: 'ER21212', symbol_ref: 'T1', label: 'ER21212へ', source: 'symbol' }],
      candidates: [{ project_id: 2, name: 'b', sheet: 'ER21212' }],
    }],
    pages_without_sheet: [],
    complete: false,
  })
})
afterEach(cleanup)

describe('DrawingSetBar', () => {
  it('セットを選ぶと図面を選び、足りないシートを追加できる', async () => {
    const onSelect = vi.fn()
    render(<DrawingSetBar selected={[]} onSelect={onSelect} />)
    await screen.findByRole('option', { name: 'DS01（1 枚）' })
    fireEvent.change(screen.getByRole('combobox', { name: '図面セット' }), { target: { value: '3' } })
    expect(onSelect).toHaveBeenCalledWith([1])
    await screen.findByText(/ER21212 がセットにありません/)
    fireEvent.click(screen.getByRole('button', { name: '登録済みの 1 枚を選択に追加' }))
    expect(onSelect).toHaveBeenLastCalledWith([2])
  })

  it('選んだ図面に名前を付けて保存する', async () => {
    api.createDrawingSet.mockResolvedValue({ ...set, id: 4, name: 'DS02', project_ids: [1, 2] })
    render(<DrawingSetBar selected={[1, 2]} onSelect={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('セットの名前'), { target: { value: 'DS02' } })
    fireEvent.click(screen.getByRole('button', { name: /選択をセットとして保存/ }))
    await waitFor(() => expect(api.createDrawingSet).toHaveBeenCalledWith('DS02', [1, 2]))
    await waitFor(() => expect(api.drawingSetCoverage).toHaveBeenCalledWith(4))
  })

  it('行先札でつながる組を候補に出す', async () => {
    api.suggestDrawingSets.mockResolvedValue([
      { name: 'ER21211', project_ids: [1, 2], sheets: ['ER21211', 'ER21212'], missing_sheets: [] },
      { name: 'ER30000', project_ids: [5], sheets: ['ER30000'], missing_sheets: [] },
    ])
    const onSelect = vi.fn()
    render(<DrawingSetBar selected={[]} onSelect={onSelect} />)
    fireEvent.click(screen.getByRole('button', { name: /行先札から候補/ }))
    fireEvent.click(await screen.findByRole('button', { name: /ER21211 ほか 2 枚/ }))
    expect(onSelect).toHaveBeenCalledWith([1, 2])
    expect(screen.queryByText(/ER30000/)).toBeNull()
  })
})
