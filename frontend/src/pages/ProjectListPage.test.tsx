// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { DeletePreview, ProjectRow } from '../types'
import ProjectListPage from './ProjectListPage'

const apiMocks = vi.hoisted(() => ({
  bulkDelete: vi.fn(),
  clearAnalysis: vi.fn(),
  deletePreview: vi.fn(),
  createProjects: vi.fn(),
  exportBulk: vi.fn(),
  listProjects: vi.fn(),
  stats: vi.fn(),
  thumbnailUrl: vi.fn((id: number) => `/api/projects/${id}/thumbnail`),
}))

vi.mock('../api/client', () => ({ api: apiMocks }))

function projectRow(id: number, name: string, status = 'draft'): ProjectRow {
  return {
    id,
    name,
    sheet_no: null,
    page_no: null,
    revision: null,
    status,
    assignee: null,
    image_width: 1000,
    image_height: 800,
    symbol_count: 0,
    connection_count: 0,
    terminal_count: 0,
    prediction_count: 0,
    analysis_wire_count: null,
    analysis_connection_count: null,
    updated_at: null,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  apiMocks.stats.mockResolvedValue({
    project_count: 0,
    symbol_count: 0,
    connection_count: 0,
    by_status: {},
    by_class: {},
  })
  apiMocks.listProjects.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ProjectListPage', () => {
  it('後発検索の結果を、遅れて返った旧検索で上書きしない', async () => {
    let resolveFirstSearch: (rows: ProjectRow[]) => void = () => {
      throw new Error('先行検索が開始されていません')
    }
    let resolveLatestSearch: (rows: ProjectRow[]) => void = () => {
      throw new Error('後発検索が開始されていません')
    }
    apiMocks.listProjects.mockImplementation((query?: string) => {
      if (query === 'A') {
        return new Promise<ProjectRow[]>((resolve) => {
          resolveFirstSearch = resolve
        })
      }
      if (query === 'AB') {
        return new Promise<ProjectRow[]>((resolve) => {
          resolveLatestSearch = resolve
        })
      }
      return Promise.resolve([])
    })

    const { findAllByText, findByPlaceholderText, queryAllByText } = render(
      <MemoryRouter>
        <ProjectListPage />
      </MemoryRouter>,
    )
    const search = await findByPlaceholderText('ID・名称・シート番号で検索')

    fireEvent.change(search, { target: { value: 'A' } })
    await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalledWith('A'), { timeout: 1000 })
    fireEvent.change(search, { target: { value: 'AB' } })
    await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalledWith('AB'), { timeout: 1000 })

    resolveLatestSearch([projectRow(2, 'ABの結果')])
    expect(await findAllByText('ABの結果')).toHaveLength(1)
    resolveFirstSearch([projectRow(1, 'Aの古い結果')])
    await new Promise((resolve) => window.setTimeout(resolve, 20))

    expect(queryAllByText('Aの古い結果')).toHaveLength(0)
    expect(queryAllByText('ABの結果')).toHaveLength(1)
  })

  it('状態タブで絞り込み、表示形式の選択を保存する', async () => {
    apiMocks.listProjects.mockResolvedValue([
      projectRow(1, '作業中の図面', 'draft'),
      projectRow(2, '完了した図面', 'done'),
      projectRow(3, 'もう1枚の完了図面', 'done'),
    ])

    const { findByText, getByRole, queryByText, container, unmount } = render(
      <MemoryRouter>
        <ProjectListPage />
      </MemoryRouter>,
    )
    expect(await findByText('作業中の図面')).toBeTruthy()
    expect(container.querySelector('img[src="/api/projects/1/thumbnail"]')).toBeTruthy()

    const doneTab = getByRole('button', { name: /^完了\s*2$/ })
    fireEvent.click(doneTab)
    expect(doneTab.getAttribute('aria-pressed')).toBe('true')
    expect(queryByText('作業中の図面')).toBeNull()
    expect(queryByText('完了した図面')).toBeTruthy()

    fireEvent.click(getByRole('button', { name: '表で表示' }))
    expect(container.querySelector('table')).toBeTruthy()
    expect(window.localStorage.getItem('todenyolo.workspace.view')).toBe('list')
    unmount()

    const again = render(
      <MemoryRouter>
        <ProjectListPage />
      </MemoryRouter>,
    )
    await again.findAllByText('作業中の図面')
    expect(again.container.querySelector('table')).toBeTruthy()
  })

  it('読み込み中は背面コンテンツを操作対象から外す', async () => {
    apiMocks.listProjects.mockImplementation(() => new Promise<ProjectRow[]>(() => undefined))

    const { container, findByRole } = render(
      <MemoryRouter>
        <ProjectListPage />
      </MemoryRouter>,
    )

    expect(await findByRole('status')).toBeTruthy()
    const background = container.querySelector('[inert]')
    expect(background).toBeTruthy()
    expect(background?.getAttribute('aria-hidden')).toBe('true')
  })

  it('AI の件数を保存済みの件数と分け、ラベル付きで表示する', async () => {
    apiMocks.listProjects.mockResolvedValue([
      {
        ...projectRow(1, 'AI解析済みの図面'),
        symbol_count: 3,
        connection_count: 2,
        prediction_count: 29,
        analysis_wire_count: 41,
        analysis_connection_count: 18,
      },
      projectRow(2, '未解析の図面'),
    ])

    const { findByText, getAllByText, getByRole, getByText, queryByText } = render(
      <MemoryRouter>
        <ProjectListPage />
      </MemoryRouter>,
    )
    expect(await findByText('AI解析済みの図面')).toBeTruthy()
    expect(getByText('AI検出 29').getAttribute('title')).toContain('取り込むまで保存件数に含まれない')
    expect(getByText('AI配線 41').getAttribute('title')).toContain('接続 18 件')
    expect(getAllByText(/^AI検出 /)).toHaveLength(1)
    expect(queryByText('+29')).toBeNull()
    expect(getByText(/人が確認して保存したアノテーション（学習に使う）/)).toBeTruthy()

    fireEvent.click(getByRole('button', { name: '表で表示' }))
    expect(getAllByText('AI検出 29')).toHaveLength(2)
    expect(getAllByText('AI配線 41')).toHaveLength(2)
  })

  describe('削除の確認（DD-0052）', () => {
    const preview: DeletePreview = {
      project_ids: [1],
      names: ['A.png'],
      missing_ids: [],
      counts: {
        symbols: 3, terminals: 2, connections: 4, predictions: 5, structures: 1,
        drawing_sets: 1, change_sets: 0, image_files: 1, training_runs: 2,
      },
      blockers: [],
      analysis_blockers: [],
    }

    const openDialog = async () => {
      apiMocks.listProjects.mockResolvedValue([projectRow(1, 'A.png'), projectRow(2, 'B.png')])
      const ui = render(<MemoryRouter><ProjectListPage /></MemoryRouter>)
      await ui.findAllByText('A.png')
      fireEvent.click(ui.getAllByRole('checkbox', { name: /A\.png/ })[0])
      fireEvent.click(ui.getByRole('button', { name: /^削除$/ }))
      const dialog = await ui.findByRole('dialog')
      await within(dialog).findByText(/アノテーションの記号 3 件/)
      return { ui, dialog }
    }

    it('消えるものと残るものを数えて見せ、確かめてから図面を消す', async () => {
      apiMocks.deletePreview.mockResolvedValue(preview)
      apiMocks.bulkDelete.mockResolvedValue({ deleted: [1] })
      const { dialog } = await openDialog()
      expect(apiMocks.deletePreview).toHaveBeenCalledWith([1])
      expect(within(dialog).getByRole('list', { name: '一緒に消えるもの' }).textContent).toContain('AI の検出候補 5 件')
      expect(within(dialog).getByRole('list', { name: '残るもの' }).textContent).toContain('学習の履歴（2 件）')
      expect(apiMocks.bulkDelete).not.toHaveBeenCalled()
      fireEvent.click(within(dialog).getByRole('button', { name: /図面を削除する/ }))
      await waitFor(() => expect(apiMocks.bulkDelete).toHaveBeenCalledWith([1]))
    })

    it('AI の解析結果だけを消すこともできる', async () => {
      apiMocks.deletePreview.mockResolvedValue(preview)
      apiMocks.clearAnalysis.mockResolvedValue({ predictions: 5, structures: 1 })
      const { ui, dialog } = await openDialog()
      fireEvent.click(within(dialog).getByLabelText(/AI の解析結果だけ削除/))
      expect(within(dialog).getByRole('list', { name: '残るもの' }).textContent).toContain('アノテーション')
      fireEvent.click(within(dialog).getByRole('button', { name: /解析結果を削除する/ }))
      await waitFor(() => expect(apiMocks.clearAnalysis).toHaveBeenCalledWith([1]))
      expect(apiMocks.bulkDelete).not.toHaveBeenCalled()
      expect(await ui.findByText(/検出候補 5 件・構造解析 1 枚/)).toBeTruthy()
    })

    it('実行中の解析が使っている図面は消せない', async () => {
      apiMocks.deletePreview.mockResolvedValue({ ...preview, blockers: ['AI 解析を実行中の図面が含まれています。'] })
      const { dialog } = await openDialog()
      expect(within(dialog).getByRole('alert').textContent).toContain('AI 解析を実行中')
      expect((within(dialog).getByRole('button', { name: /図面を削除する/ }) as HTMLButtonElement).disabled).toBe(true)
    })
  })
})
