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

  it('選び直した図面でセットを更新し、選ばないに戻せる', async () => {
    const other = { ...set, id: 9, name: 'A01', project_ids: [7, 8] }
    api.listDrawingSets.mockResolvedValue([set, other])
    api.updateDrawingSet.mockResolvedValue({ ...set, project_ids: [1, 5] })
    api.drawingSetCoverage.mockResolvedValue({ set, missing_sheets: [], pages_without_sheet: [], complete: true })
    const onSelect = vi.fn()
    const view = render(<DrawingSetBar selected={[1]} onSelect={onSelect} />)
    await screen.findByRole('option', { name: 'DS01（1 枚）' })
    fireEvent.change(screen.getByRole('combobox', { name: '図面セット' }), { target: { value: '3' } })
    expect(await screen.findByText(/すべてセットに入っています/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /今の選択でセットを更新/ })).toBeNull()

    view.rerender(<DrawingSetBar selected={[1, 5]} onSelect={onSelect} />)
    fireEvent.click(screen.getByRole('button', { name: /今の選択でセットを更新/ }))
    await waitFor(() => expect(api.updateDrawingSet).toHaveBeenCalledWith(3, { project_ids: [1, 5] }))
    expect(await screen.findByRole('option', { name: 'DS01（2 枚）' })).toBeTruthy()
    // 名前順に並ぶ
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['（選ばない）', 'A01（2 枚）', 'DS01（2 枚）'])

    view.rerender(<DrawingSetBar selected={[1, 2, 3]} onSelect={onSelect} />)
    view.rerender(<DrawingSetBar selected={[]} onSelect={onSelect} />)
    expect((screen.getByRole('button', { name: /今の選択でセットを更新/ }) as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(screen.getByRole('combobox', { name: '図面セット' }), { target: { value: '' } })
    expect(screen.queryByRole('status')).toBeNull()
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('未登録のシート・シート番号の無い図面・参照の名前が無いときを出す', async () => {
    api.drawingSetCoverage.mockResolvedValue({
      set,
      missing_sheets: [{
        sheet: 'ER9',
        referenced_by: [{ project_id: 1, name: 'a', target: 'ER9', symbol_ref: 'T7', label: '', source: 'symbol' }],
        candidates: [],
      }],
      pages_without_sheet: [{ project_id: 4, name: '名無し図面' }],
      complete: false,
    })
    render(<DrawingSetBar selected={[]} onSelect={vi.fn()} />)
    await screen.findByRole('option', { name: 'DS01（1 枚）' })
    fireEvent.change(screen.getByRole('combobox', { name: '図面セット' }), { target: { value: '3' } })
    expect(await screen.findByText(/T7 が参照）。 この図面はまだ登録されていません。/)).toBeTruthy()
    expect(screen.getByText(/名無し図面 はシート番号が分からないため/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /選択に追加/ })).toBeNull()
  })

  it('読み込み・保存・候補の失敗を出し、候補が無いときと未登録のある候補を出す', async () => {
    api.listDrawingSets.mockRejectedValueOnce(new Error('一覧を読めません'))
    api.createDrawingSet.mockRejectedValueOnce('文字列の失敗')
    api.suggestDrawingSets
      .mockRejectedValueOnce(new Error('候補を出せません'))
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ name: 'ER1', project_ids: [1, 2], sheets: ['ER1'], missing_sheets: ['ER3', 'ER4'] }])
    const view = render(<DrawingSetBar selected={[1]} onSelect={vi.fn()} />)
    expect(await screen.findByText('一覧を読めません')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('セットの名前'), { target: { value: '  ' } })
    expect((screen.getByRole('button', { name: /選択をセットとして保存/ }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('セットの名前'), { target: { value: 'X' } })
    fireEvent.click(screen.getByRole('button', { name: /選択をセットとして保存/ }))
    expect(await screen.findByText('文字列の失敗')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /行先札から候補/ }))
    expect(await screen.findByText('候補を出せません')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /行先札から候補/ }))
    expect(await screen.findByText('行先札でつながる図面の組は見つかりませんでした。')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /行先札から候補/ }))
    expect(await screen.findByText('（未登録 2）')).toBeTruthy()
    view.unmount()

    // セットの網羅を読めないとき
    api.drawingSetCoverage.mockRejectedValueOnce(new Error('網羅を読めません'))
    render(<DrawingSetBar selected={[]} onSelect={vi.fn()} />)
    await screen.findByRole('option', { name: 'DS01（1 枚）' })
    fireEvent.change(screen.getByRole('combobox', { name: '図面セット' }), { target: { value: '3' } })
    expect(await screen.findByText('網羅を読めません')).toBeTruthy()
  })
})

