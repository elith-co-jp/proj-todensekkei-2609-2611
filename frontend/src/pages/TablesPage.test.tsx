// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'

import TablesPage from './TablesPage'
import { tableLayout } from './tables/layout'
import type { ProjectTables, ReviewTable } from '../types'

const api = vi.hoisted(() => ({
  getTables: vi.fn(), getProject: vi.fn(), listProjects: vi.fn(), saveTable: vi.fn(), resetTable: vi.fn(),
  imageUrl: vi.fn((id) => `/api/projects/${id}/image`),
}))
vi.mock('../api/client', () => ({ api }))

// 2 行 × 3 列（器具番号・端子・接点）。2 行目の接点は AI が読んでいない
const grid: ReviewTable['grid'] = {
  bbox: [50, 950, 200, 1000],
  rows: [{ y0: 960, y1: 977 }, { y0: 980, y1: 997 }],
  cols: [{ x0: 60, x1: 100 }, { x0: 110, x1: 150 }, { x0: 160, x1: 172 }],
  cells: [
    { row: 0, col: 0, colspan: 1, text: '33HB', score: 0.99, box: [60, 960, 100, 977], parts: 1 },
    { row: 0, col: 1, colspan: 1, text: '5-9-1', score: 0.99, box: [110, 960, 150, 977], parts: 1 },
    { row: 0, col: 2, colspan: 1, text: 'A', score: 0.5, box: [160, 960, 172, 977], parts: 1 },
    { row: 1, col: 1, colspan: 1, text: '8-12-4', score: 0.95, box: [110, 980, 150, 997], parts: 1 },
  ],
}

function tables(status: ReviewTable['status'], values = grid.cells.map((c) => ({ row: c.row, col: c.col, text: c.text }))): ProjectTables {
  return {
    project_id: 1, analyzed: true, image_size: { width: 1000, height: 1200 },
    tables: [{
      key: 'relay_table', label: 'リレー表', status, updated_at: status === 'unreviewed' ? null : '2026-11-27 04:00',
      confirmed_at: status === 'confirmed' ? '2026-11-27 04:00' : null, grid, values,
      cell_count: 4, low_score_count: 1, changed_count: 0, ocr_changed: false,
    }],
    relay_rows: [{ device: '33HB', terminal: '5-9-1', contact: 'a', sheet: '', position: '' }],
    relay_source: status === 'confirmed' ? 'confirmed' : 'ocr',
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getTables.mockResolvedValue(tables('unreviewed'))
  api.getProject.mockResolvedValue({ id: 1, name: '図面1', image_width: 1000, image_height: 1200 })
  api.listProjects.mockResolvedValue([{ id: 1 }])
  api.saveTable.mockImplementation(async (_id, _key, values, confirm) => tables(confirm ? 'confirmed' : 'draft', values))
})

afterEach(() => cleanup())

function openPage() {
  const router = createMemoryRouter([
    { path: '/projects/:id/tables', element: <TablesPage /> },
    { path: '/projects/:id/analysis', element: <div>解析結果</div> },
  ], { initialEntries: ['/projects/1/tables'] })
  return render(<RouterProvider router={router} />)
}

describe('tableLayout', () => {
  it('tiles every row and column, splitting gaps in the middle', () => {
    const { rects, view } = tableLayout(grid)
    expect(rects).toHaveLength(6)
    const first = rects[0]
    expect([first.x, first.y, first.w]).toEqual([57, 957, 48])  // 左端は 3 px 広げ、右は次の列との間の真ん中
    expect(view.x).toBe(50)
  })
})

describe('TablesPage', () => {
  it('shows the OCR in the table layout and confirms the corrected cells', async () => {
    const view = openPage()
    await view.findByRole('heading', { name: '表の読み取り確認' })
    expect(view.getByRole('tab', { name: /リレー表/ }).textContent).toContain('未確認')
    // 6 欄すべてに入力欄がある（AI が読んでいない欄も直せる）
    const contact = view.getByLabelText('1 行 3 列') as HTMLInputElement
    expect(contact.value).toBe('A')
    expect(contact.dataset.state).toBe('low')
    const empty = view.getByLabelText('2 行 3 列') as HTMLInputElement
    expect(empty.value).toBe('')
    expect(empty.dataset.state).toBe('empty')

    fireEvent.change(contact, { target: { value: 'a' } })
    fireEvent.change(empty, { target: { value: 'b' } })
    expect(contact.dataset.state).toBe('edited')
    expect(view.getByRole('region', { name: '直した欄' }).querySelectorAll('tbody tr')).toHaveLength(2)
    expect(view.getByText('未保存の変更あり')).toBeTruthy()

    fireEvent.click(view.getByRole('button', { name: '確定' }))
    await waitFor(() => expect(api.saveTable).toHaveBeenCalledWith(1, 'relay_table', [
      { row: 0, col: 0, text: '33HB' }, { row: 0, col: 1, text: '5-9-1' }, { row: 0, col: 2, text: 'a' },
      { row: 1, col: 1, text: '8-12-4' }, { row: 1, col: 2, text: 'b' },
    ], true))
    await view.findByText('リレー表を確定しました')
    expect(view.getByRole('tab', { name: /リレー表/ }).textContent).toContain('確定')
    expect(view.getByText(/確定した読みから 1 行/)).toBeTruthy()
  })

  it('moves down with Enter and reverts edits to the AI reading', async () => {
    const view = openPage()
    const top = await view.findByLabelText('1 行 2 列')
    top.focus()
    fireEvent.keyDown(top, { key: 'Enter' })
    expect(document.activeElement).toBe(view.getByLabelText('2 行 2 列'))
    fireEvent.change(top, { target: { value: '5-9-7' } })
    fireEvent.click(view.getByRole('button', { name: 'AI の読みに戻す' }))
    expect((view.getByLabelText('1 行 2 列') as HTMLInputElement).value).toBe('5-9-1')
  })

  it('explains when the drawing has not been analyzed', async () => {
    api.getTables.mockResolvedValue({ project_id: 1, analyzed: false, image_size: null, tables: [], relay_rows: [] })
    const view = openPage()
    expect(await view.findByText(/まだ解析していません/)).toBeTruthy()
  })
})
