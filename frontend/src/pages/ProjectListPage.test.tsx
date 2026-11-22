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

  it('遅れて失敗した旧検索のエラーで後発の結果を消さない', async () => {
    let rejectFirst: (reason: Error) => void = () => undefined
    apiMocks.listProjects.mockImplementation((query?: string) =>
      query === 'A' ? new Promise<ProjectRow[]>((_, reject) => { rejectFirst = reject }) : Promise.resolve([projectRow(2, `${query}の結果`)]))
    const view = renderPage()
    const search = await view.findByPlaceholderText('ID・名称・シート番号で検索')
    fireEvent.change(search, { target: { value: 'A' } })
    await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalledWith('A'), { timeout: 1000 })
    fireEvent.change(search, { target: { value: 'AB' } })
    expect(await view.findByText('ABの結果', {}, { timeout: 1000 })).toBeTruthy()
    await act(async () => rejectFirst(new Error('古い失敗')))
    expect(view.queryByRole('alert')).toBeNull()
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

  describe('登録・選択・削除・書き出し', () => {
    const rows = () => [projectRow(1, '図面A'), projectRow(2, '図面B', 'review'), projectRow(3, '図面C', 'done')]

    it('作業状況の数字と完了率を出し、図面が無ければ 0% にする', async () => {
      apiMocks.stats.mockResolvedValue({
        project_count: 4, symbol_count: 12, connection_count: 7, by_status: { draft: 1, review: 1, done: 2 }, by_class: {},
      })
      const view = renderPage()
      const bar = await view.findByRole('progressbar', { name: '完了率' })
      expect(bar.getAttribute('aria-valuenow')).toBe('50')
      expect(view.getByText('作業中 1 ・ レビュー待ち 1')).toBeTruthy()
      view.unmount()

      apiMocks.stats.mockResolvedValue({ project_count: 0, symbol_count: 0, connection_count: 0, by_status: {}, by_class: {} })
      const empty = renderPage()
      expect((await empty.findByRole('progressbar', { name: '完了率' })).getAttribute('aria-valuenow')).toBe('0')
      expect(empty.getByText('作業中 0 ・ レビュー待ち 0')).toBeTruthy()
      // 図面が無いときは登録を促し、ボタンでファイル選択を開く
      const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => undefined)
      fireEvent.click(empty.getAllByRole('button', { name: /図面を登録/ })[0])
      fireEvent.click(empty.getAllByRole('button', { name: /図面を登録/ })[1])
      expect(click).toHaveBeenCalledTimes(2)
      empty.unmount()

      // 完了の内訳が無い集計でも 0% として出す
      apiMocks.stats.mockResolvedValue({ project_count: 3, symbol_count: 0, connection_count: 0, by_status: {}, by_class: {} })
      const noDone = renderPage()
      expect((await noDone.findByRole('progressbar', { name: '完了率' })).getAttribute('aria-valuenow')).toBe('0')
    })

    it('一覧を読めなければ理由を出し、更新で読み直す', async () => {
      apiMocks.listProjects.mockRejectedValueOnce(new Error('サーバーに繋がりません')).mockRejectedValueOnce('文字列の失敗')
      const view = renderPage()
      expect((await view.findByRole('alert')).textContent).toContain('サーバーに繋がりません')
      fireEvent.click(view.getByRole('button', { name: /更新/ }))
      await waitFor(() => expect(view.getByRole('alert').textContent).toContain('文字列の失敗'))
      apiMocks.listProjects.mockResolvedValue(rows())
      fireEvent.click(view.getByRole('button', { name: /更新/ }))
      expect(await view.findByText('図面A')).toBeTruthy()
      expect(view.queryByRole('alert')).toBeNull()
    })

    it('登録すると件数を出し、推論できるなら登録した図面の AI 解析を勧める', async () => {
      apiMocks.createProjects.mockResolvedValue({ count: 2, project_ids: [8, 9] })
      apiMocks.mlStatus.mockResolvedValue({ ultralytics: true, active_model: 'best.pt' })
      const view = renderPage()
      await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalled())
      const input = view.container.querySelector('input[type="file"]') as HTMLInputElement
      // 何も選ばずに閉じたときは何もしない
      fireEvent.change(input, { target: { files: [] } })
      expect(apiMocks.createProjects).not.toHaveBeenCalled()
      const file = new File(['%PDF'], 'a.pdf', { type: 'application/pdf' })
      fireEvent.change(input, { target: { files: [file] } })
      expect(await view.findByText('2 件の図面を登録しました')).toBeTruthy()
      expect(apiMocks.createProjects).toHaveBeenCalledWith([file])
      expect(view.getByRole('button', { name: /登録した 2 件/ })).toBeTruthy()
    })

    it('推論の状態が分からない・モデルが無いときは AI 解析を勧めない', async () => {
      apiMocks.createProjects.mockResolvedValue({ count: 1, project_ids: [8] })
      apiMocks.mlStatus.mockRejectedValueOnce(new Error('x')).mockResolvedValueOnce({ ultralytics: true, active_model: null })
      const view = renderPage()
      await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalled())
      const input = view.container.querySelector('input[type="file"]') as HTMLInputElement
      for (let round = 0; round < 2; round += 1) {
        fireEvent.change(input, { target: { files: [new File(['x'], 'a.png')] } })
        expect(await view.findByText('1 件の図面を登録しました')).toBeTruthy()
        expect(view.queryByRole('button', { name: /登録した/ })).toBeNull()
      }
      expect(apiMocks.mlStatus).toHaveBeenCalledTimes(2)
    })

    it('登録に失敗したら理由を出す', async () => {
      apiMocks.createProjects.mockRejectedValueOnce(new Error('PDF を読めません')).mockRejectedValueOnce(42)
      const view = renderPage()
      await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalled())
      const input = view.container.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, { target: { files: [new File(['x'], 'a.pdf')] } })
      expect((await view.findByRole('alert')).textContent).toContain('PDF を読めません')
      fireEvent.change(input, { target: { files: [new File(['x'], 'a.pdf')] } })
      await waitFor(() => expect(view.getByRole('alert').textContent).toContain('42'))
    })

    it('Shift で範囲選択し、選んだ図面を書き出し・回路検証へ渡す', async () => {
      apiMocks.listProjects.mockResolvedValue(rows())
      apiMocks.exportBulk.mockResolvedValue({ name: 'drawings.zip', size: 2048 })
      const view = renderPage()
      await view.findByText('図面A')
      // 一つ目を選ばずに Shift で押したら、普通の選択として扱う
      fireEvent.click(view.getByRole('checkbox', { name: '図面Aを選択' }), { shiftKey: true })
      fireEvent.click(view.getByRole('checkbox', { name: '図面Cを選択' }), { shiftKey: true })
      expect((view.getByRole('checkbox', { name: '図面Bを選択' }) as HTMLInputElement).checked).toBe(true)
      expect(view.getByRole('link', { name: /回路検証（3 件）/ }).getAttribute('href')).toBe('/circuit?ids=1,2,3')
      // もう一度押すと外れる
      fireEvent.click(view.getByRole('checkbox', { name: '図面Bを選択' }))
      expect((view.getByRole('checkbox', { name: '図面Bを選択' }) as HTMLInputElement).checked).toBe(false)

      fireEvent.click(view.getByRole('button', { name: /書き出し（2 件）/ }))
      expect(await view.findByText('drawings.zip を書き出しました（2.0 KB）')).toBeTruthy()
      expect(apiMocks.exportBulk).toHaveBeenCalledWith({ ids: [1, 3] })

      // 絞り込みで基点が見えなくなったら Shift でも一件だけ切り替える
      fireEvent.click(view.getByRole('checkbox', { name: '図面Aを選択' }))
      fireEvent.click(view.getByRole('button', { name: /^レビュー待ち/ }))
      fireEvent.click(view.getByRole('checkbox', { name: '図面Bを選択' }), { shiftKey: true })
      expect((view.getByRole('checkbox', { name: '図面Bを選択' }) as HTMLInputElement).checked).toBe(true)
    })

    it('書き出しに失敗したら理由を出す', async () => {
      apiMocks.exportBulk.mockRejectedValueOnce(new Error('容量不足')).mockRejectedValueOnce('文字列')
      const view = renderPage()
      await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalled())
      fireEvent.click(await view.findByRole('button', { name: /書き出し（全件）/ }))
      expect((await view.findByRole('alert')).textContent).toContain('容量不足')
      fireEvent.click(view.getByRole('button', { name: /書き出し（全件）/ }))
      await waitFor(() => expect(view.getByRole('alert').textContent).toContain('文字列'))
    })

    it('削除は確認してから行い、キャンセル・失敗・成功を扱う', async () => {
      apiMocks.listProjects.mockResolvedValue(rows())
      apiMocks.bulkDelete.mockRejectedValueOnce(new Error('使用中です')).mockRejectedValueOnce('x').mockResolvedValueOnce({ deleted: 1 })
      const view = renderPage()
      await view.findByText('図面A')
      const remove = view.getByRole('button', { name: /^削除$/ })
      expect((remove as HTMLButtonElement).disabled).toBe(true)
      fireEvent.click(view.getByRole('checkbox', { name: '図面Aを選択' }))
      fireEvent.click(remove)
      const dialog = view.getByRole('dialog')
      expect(dialog.textContent).toContain('1件の図面')
      fireEvent.click(view.getByRole('button', { name: 'キャンセル' }))
      expect(view.queryByRole('dialog')).toBeNull()

      fireEvent.click(remove)
      fireEvent.click(view.getByRole('button', { name: /削除する/ }))
      expect((await view.findAllByText('使用中です')).length).toBeGreaterThan(0)
      fireEvent.click(view.getByRole('button', { name: /削除する/ }))
      await waitFor(() => expect(view.getByRole('dialog').textContent).toContain('x'))
      fireEvent.click(view.getByRole('button', { name: /削除する/ }))
      expect(await view.findByText('選択した図面を削除しました')).toBeTruthy()
      expect(view.queryByRole('dialog')).toBeNull()
      expect(apiMocks.bulkDelete).toHaveBeenLastCalledWith([1])
    })

    it('サムネイルが読めなければ図面の記号に置き換え、表・スマホ表示でも選べる', async () => {
      apiMocks.listProjects.mockResolvedValue([
        { ...projectRow(1, '図面A', 'unknown'), sheet_no: 'S-1', page_no: 3, analysis_wire_count: 4, analysis_connection_count: null },
      ])
      const view = renderPage()
      await view.findByText('図面A')
      expect(view.getByText(/シート S-1/)).toBeTruthy()
      expect(view.getAllByText('unknown').length).toBeGreaterThan(0)
      expect(view.getByText('AI配線 4').getAttribute('title')).not.toContain('接続')
      const img = view.container.querySelector('img') as HTMLImageElement
      fireEvent.error(img)
      expect(view.container.querySelector('img')).toBeNull()

      fireEvent.click(view.getByRole('button', { name: '表で表示' }))
      const boxes = view.getAllByRole('checkbox', { name: '図面Aを選択' })
      expect(boxes).toHaveLength(2)
      fireEvent.click(boxes[1])
      expect((boxes[0] as HTMLInputElement).checked).toBe(true)
      fireEvent.click(boxes[0])
      expect((boxes[1] as HTMLInputElement).checked).toBe(false)
      fireEvent.click(view.getByRole('button', { name: 'カード表示' }))
      expect(view.container.querySelector('table')).toBeNull()
    })

    it('条件に合う図面が無いときは絞り込みの結果として伝える', async () => {
      const view = renderPage()
      expect(await view.findByText('図面がありません。PDFまたは画像を登録してください。')).toBeTruthy()
      fireEvent.click(view.getByRole('button', { name: /^完了/ }))
      expect(view.getByText('条件に一致する図面がありません。')).toBeTruthy()
      fireEvent.click(view.getByRole('button', { name: '表で表示' }))
      expect(view.getAllByText('条件に一致する図面がありません。')).toHaveLength(2)
      fireEvent.click(view.getByRole('button', { name: /^すべて/ }))
      expect(view.getAllByText('図面がありません。PDFまたは画像を登録してください。')).toHaveLength(2)
    })

    it('表示形式を保存できない環境でも切り替えられる', async () => {
      const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new Error('denied')
      })
      vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new Error('denied')
      })
      const view = renderPage()
      await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalled())
      expect(getItem).toHaveBeenCalled()
      fireEvent.click(view.getByRole('button', { name: '表で表示' }))
      expect(view.container.querySelector('table')).toBeTruthy()
    })

    it('解析の開始・中止に失敗したら理由を出す', async () => {
      apiMocks.startAnalysisJob.mockRejectedValueOnce(new Error('モデルがありません')).mockResolvedValueOnce(job())
      apiMocks.getAnalysisJob.mockResolvedValue(job())
      apiMocks.cancelAnalysisJob.mockRejectedValue('止められません')
      const view = renderPage()
      fireEvent.click(await view.findByRole('button', { name: /AI 解析（全件）/ }))
      expect((await view.findByRole('alert')).textContent).toContain('モデルがありません')
      fireEvent.click(view.getByRole('button', { name: /AI 解析（全件）/ }))
      fireEvent.click(await view.findByRole('button', { name: '中止' }))
      await waitFor(() => expect(view.getByRole('alert').textContent).toContain('止められません'))
    })
  })
})

