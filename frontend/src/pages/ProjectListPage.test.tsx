// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { AnalysisJob, ProjectRow } from '../types'
import ProjectListPage from './ProjectListPage'

const apiMocks = vi.hoisted(() => ({
  bulkDelete: vi.fn(),
  cancelAnalysisJob: vi.fn(),
  getAnalysisJob: vi.fn(),
  listAnalysisJobs: vi.fn(),
  mlStatus: vi.fn(),
  startAnalysisJob: vi.fn(),
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
  apiMocks.listAnalysisJobs.mockResolvedValue([])
})

function job(patch: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    id: 5, status: 'running', total: 2, done: 0, current: '図面1', conf: 0.5, connector_conf: 0.6, results: [], failures: [],
    error: null, cancel_requested: false, detection_count: 0, started_at: null, finished_at: null, ...patch,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ProjectListPage />
    </MemoryRouter>,
  )
}

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

  describe('AI 解析（裏のジョブ・DD-0039）', () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it('全件の解析を裏のジョブで始め、画面をふさがずに進み具合と中止を出し、終わったら件数を出す', async () => {
      apiMocks.listProjects.mockResolvedValue([projectRow(1, '図面1'), projectRow(2, '図面2')])
      apiMocks.startAnalysisJob.mockResolvedValue(job())
      apiMocks.cancelAnalysisJob.mockResolvedValue(job({ done: 1, cancel_requested: true, current: '図面2' }))
      const view = renderPage()
      await view.findByText('図面1')
      fireEvent.click(view.getByRole('button', { name: /AI 解析（全件）/ }))
      await waitFor(() => expect(apiMocks.startAnalysisJob).toHaveBeenCalledWith([], undefined, undefined))
      const progress = await view.findByRole('status', { name: '解析の進み具合' })
      expect(progress.textContent).toContain('解析中 0 / 2 図面')
      expect(progress.textContent).toContain('いま: 図面1')
      expect(progress.textContent).toContain('EXE は終わるまで閉じないでください')
      expect(view.container.querySelector('[inert]')).toBeNull()
      expect((view.getByRole('button', { name: /AI 解析（全件）/ }) as HTMLButtonElement).disabled).toBe(true)

      fireEvent.click(view.getByRole('button', { name: '中止' }))
      await waitFor(() => expect(apiMocks.cancelAnalysisJob).toHaveBeenCalledWith(5))
      expect(await view.findByRole('button', { name: '中止します…' })).toBeTruthy()

      apiMocks.getAnalysisJob.mockResolvedValue(job({
        status: 'done', done: 2, current: null, detection_count: 3,
        results: [
          { project_id: 1, project_name: '図面1', detections: 2, wire_count: 3 },
          { project_id: 2, project_name: '図面2', detections: 1, wire_count: 1, warnings: ['ラベルの読み取り'] },
        ] as unknown as AnalysisJob['results'],
      }))
      const loadsBefore = apiMocks.listProjects.mock.calls.length
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect(apiMocks.getAnalysisJob).toHaveBeenCalledWith(5)
      expect(await view.findByText(/解析完了: シンボル 3 件、配線 4 本（2 図面）。一部のラベル読み取りに問題があります/)).toBeTruthy()
      expect(apiMocks.listProjects.mock.calls.length).toBeGreaterThan(loadsBefore)
      expect(view.queryByRole('status', { name: '解析の進み具合' })).toBeNull()
    })

    it('選んだ図面だけを解析し、失敗した図面があれば名前を出す', async () => {
      apiMocks.listProjects.mockResolvedValue([projectRow(1, '図面1'), projectRow(2, '図面2')])
      apiMocks.startAnalysisJob.mockResolvedValue(job({ total: 1 }))
      const view = renderPage()
      await view.findByText('図面1')
      fireEvent.click(view.getAllByRole('checkbox')[1])
      fireEvent.click(view.getByRole('button', { name: /AI 解析（1 件）/ }))
      await waitFor(() => expect(apiMocks.startAnalysisJob).toHaveBeenCalledWith([2], undefined, undefined))
      apiMocks.getAnalysisJob.mockResolvedValue(job({
        status: 'done', total: 1, done: 1, failures: [{ project_id: 2, name: '図面2', message: '画像がありません' }],
      }))
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect((await view.findByRole('alert')).textContent).toContain('1 図面は解析できませんでした（図面2）')
    })

    it('解析を始められなければ理由を出し、中止できなければその理由も出す', async () => {
      apiMocks.listProjects.mockResolvedValue([projectRow(1, '図面1')])
      apiMocks.startAnalysisJob.mockRejectedValueOnce(new Error('推論に使うモデルがありません'))
      const view = renderPage()
      await view.findByText('図面1')
      fireEvent.click(view.getByRole('button', { name: /AI 解析（全件）/ }))
      expect((await view.findByRole('alert')).textContent).toContain('推論に使うモデルがありません')

      apiMocks.startAnalysisJob.mockRejectedValueOnce('文字列')
      fireEvent.click(view.getByRole('button', { name: /AI 解析（全件）/ }))
      await waitFor(() => expect(view.getByRole('alert').textContent).toContain('文字列'))

      apiMocks.startAnalysisJob.mockResolvedValue(job())
      apiMocks.cancelAnalysisJob.mockRejectedValueOnce(new Error('解析のジョブが見つかりません')).mockRejectedValueOnce('x')
      fireEvent.click(view.getByRole('button', { name: /AI 解析（全件）/ }))
      fireEvent.click(await view.findByRole('button', { name: '中止' }))
      await waitFor(() => expect(view.getByRole('alert').textContent).toContain('解析のジョブが見つかりません'))
      fireEvent.click(view.getByRole('button', { name: '中止' }))
      await waitFor(() => expect(view.getByRole('alert').textContent).toBe('x'))
    })

    it('開いたときに裏で解析中のジョブがあれば進み具合を出し、失敗で終われば理由を出す', async () => {
      apiMocks.listAnalysisJobs.mockResolvedValue([job({ id: 4, status: 'done' }), job({ id: 9, done: 1 })])
      const view = renderPage()
      expect((await view.findByRole('status', { name: '解析の進み具合' })).textContent).toContain('解析中 1 / 2 図面')
      apiMocks.getAnalysisJob.mockRejectedValueOnce(new Error('一時的な失敗'))
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect(view.getByRole('status', { name: '解析の進み具合' })).toBeTruthy()
      apiMocks.getAnalysisJob.mockResolvedValue(job({ id: 9, status: 'failed', error: 'モデルを読み込めません' }))
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect(apiMocks.getAnalysisJob).toHaveBeenCalledWith(9)
      expect((await view.findByRole('alert')).textContent).toContain('モデルを読み込めません')
    })

    it('登録した図面だけをそのまま解析できる', async () => {
      apiMocks.createProjects.mockResolvedValue({ count: 2, project_ids: [11, 12] })
      apiMocks.mlStatus.mockResolvedValue({ ultralytics: true, active_model: { id: 1 } })
      apiMocks.startAnalysisJob.mockResolvedValue(job({ project_ids: [11, 12] } as Partial<AnalysisJob>))
      const view = renderPage()
      await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalled())
      const file = new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })
      fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [file] } })
      fireEvent.click(await view.findByRole('button', { name: /AI 解析（登録した 2 件）/ }))
      await waitFor(() => expect(apiMocks.startAnalysisJob).toHaveBeenCalledWith([11, 12], undefined, undefined))
      await waitFor(() => expect(view.queryByRole('button', { name: /登録した 2 件/ })).toBeNull())
    })
  })
})
