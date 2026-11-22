// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'

import AnalysisPage from './AnalysisPage'

const api = vi.hoisted(() => ({
  getProject: vi.fn(), getPredictions: vi.fn(), getStructure: vi.fn(), listProjects: vi.fn(),
  imageUrl: vi.fn((id) => `/api/projects/${id}/image`), exportStructure: vi.fn(),
  startAnalysisJob: vi.fn(), getAnalysisJob: vi.fn(), listAnalysisJobs: vi.fn(), cancelAnalysisJob: vi.fn(),
}))
vi.mock('../api/client', () => ({ api }))

const result = {
  status: 'completed', source: { project_id: 1, image_sha256: 'test' },
  image_size: { width: 1000, height: 800 },
  wires: [{ id: 'wire1', polyline: [[100, 200], [600, 200], [600, 500]] }],
  symbols: [], from_to: { connection_count: 1, connections: [] }, warnings: [],
}

const runningJob = {
  id: 3, status: 'running', total: 1, done: 0, current: '図面1', conf: 0.5, connector_conf: 0.6, results: [], failures: [],
  error: null, cancel_requested: false, detection_count: 0, started_at: null, finished_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  api.getProject.mockImplementation(async (id) => ({
    id, name: `図面${id}`, image_width: 1000, image_height: 800,
    symbols: [{ ref: 'MANUAL-DO-NOT-USE', cx: 0.9, cy: 0.9, w: 0.5, h: 0.5 }],
    connections: [{ from_symbol_ref: 'manual', to_symbol_ref: 'manual2' }],
  }))
  api.getPredictions.mockResolvedValue({ count: 1, detections: [{
    class_key: 'connector', class_label: 'コネクタ', cx: 0.1, cy: 0.25, w: 0.04, h: 0.06, confidence: 0.97,
  }] })
  api.getStructure.mockResolvedValue({ project_id: 1, result })
  api.listProjects.mockResolvedValue([{ id: 1 }, { id: 2 }])
  api.listAnalysisJobs.mockResolvedValue([])
  api.startAnalysisJob.mockResolvedValue(runningJob)
  api.exportStructure.mockResolvedValue({ name: 'structure.json' })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function openPage() {
  const router = createMemoryRouter([
    { path: '/projects/:id/analysis', element: <AnalysisPage /> },
    { path: '/', element: <div>一覧</div> },
  ], { initialEntries: ['/projects/1/analysis'] })
  return { ...render(<RouterProvider router={router} />), router }
}

describe('AnalysisPage', () => {
  it('keeps the original visible while toggling each prediction layer independently', async () => {
    const view = openPage()
    await view.findByRole('heading', { name: '解析結果' })
    expect(view.getByRole('img', { name: '図面1' })).toBeTruthy()
    expect(view.getByTestId('symbol-layer').querySelectorAll('rect')).toHaveLength(1)
    expect(view.getByTestId('symbol-layer').querySelector('rect')?.getAttribute('x')).toBe('80')
    expect(view.getByTestId('wire-layer').querySelector('polyline')?.getAttribute('points')).toBe('100,200 600,200 600,500')
    expect(view.queryByText('MANUAL-DO-NOT-USE')).toBeNull()
    expect(view.queryByText('97%')).toBeNull()
    expect(view.queryByRole('checkbox', { name: /^領域枠/ })).toBeNull()
    fireEvent.click(view.getByRole('checkbox', { name: /^AI検出シンボル/ }))
    expect(view.queryByTestId('symbol-layer')).toBeNull()
    expect(view.getByTestId('wire-layer')).toBeTruthy()
    fireEvent.click(view.getByRole('checkbox', { name: /^AI検出配線/ }))
    expect(view.queryByTestId('wire-layer')).toBeNull()
    expect(view.getByRole('img', { name: '図面1' })).toBeTruthy()
    fireEvent.click(view.getByRole('checkbox', { name: /^AI検出シンボル/ }))
    expect(view.getByTestId('symbol-layer')).toBeTruthy()
    expect(view.queryByTestId('wire-layer')).toBeNull()
  })

  it('changes only overlay opacity and downloads the structured result', async () => {
    const view = openPage()
    await view.findByRole('heading', { name: '解析結果' })
    fireEvent.change(view.getByRole('slider', { name: '配線の濃さ' }), { target: { value: '70' } })
    expect(view.getByTestId('wire-layer').getAttribute('opacity')).toBe('0.7')
    expect(view.getByRole('img').style.opacity).toBe('')
    fireEvent.click(view.getByRole('button', { name: '構造JSON' }))
    await waitFor(() => expect(api.exportStructure).toHaveBeenCalledWith([1]))
  })

  it('shows symbol-only legacy results without implying wires were analyzed', async () => {
    api.getStructure.mockResolvedValue({ project_id: 1, result: null })
    const view = openPage()
    await view.findByText('シンボルのみ解析済み')
    expect((view.getByRole('checkbox', { name: /^AI検出配線/ }) as HTMLInputElement).disabled).toBe(true)
    expect((view.getByRole('button', { name: '構造JSON' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(view.getByRole('button', { name: '解析を実行' }))
    await waitFor(() => expect(api.startAnalysisJob).toHaveBeenCalledWith([1], undefined, undefined))
  })

  it('reanalyses with the background job, shows progress without blocking, and reloads when done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const view = openPage()
      await view.findByRole('heading', { name: '解析結果' })
      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      expect((await view.findByRole('status', { name: '解析の進み具合' })).textContent).toContain('解析中 0 / 1 図面')
      expect((view.getByRole('button', { name: '再解析' }) as HTMLButtonElement).disabled).toBe(true)
      api.cancelAnalysisJob.mockResolvedValueOnce({ ...runningJob, cancel_requested: true })
      fireEvent.click(view.getByRole('button', { name: '中止' }))
      await waitFor(() => expect(api.cancelAnalysisJob).toHaveBeenCalledWith(3))
      const fetches = api.getProject.mock.calls.length
      api.getAnalysisJob.mockResolvedValue({ ...runningJob, status: 'done', done: 1, results: [{ project_id: 1, detections: 1 }] })
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect(await view.findByText('解析結果を保存しました')).toBeTruthy()
      expect(api.getProject.mock.calls.length).toBeGreaterThan(fetches)
    } finally {
      vi.useRealTimers()
    }
  })

  it('reports a cancelled reanalysis and a reload failure', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const view = openPage()
      await view.findByRole('heading', { name: '解析結果' })
      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      await view.findByRole('status', { name: '解析の進み具合' })
      api.getAnalysisJob.mockResolvedValue({ ...runningJob, status: 'cancelled', done: 0 })
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect(await view.findByText(/解析を中止しました（0 \/ 1 図面）/)).toBeTruthy()

      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      await view.findByRole('status', { name: '解析の進み具合' })
      api.getProject.mockRejectedValueOnce(new Error('読み込めません'))
      api.getAnalysisJob.mockResolvedValue({ ...runningJob, status: 'done', done: 1 })
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect((await view.findByRole('alert')).textContent).toBe('読み込めません')

      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      await view.findByRole('status', { name: '解析の進み具合' })
      api.getProject.mockRejectedValueOnce('文字列')
      await act(() => vi.advanceTimersByTimeAsync(1000))
      await waitFor(() => expect(view.getByRole('alert').textContent).toBe('文字列'))
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps saved predictions when reanalysis fails', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const view = openPage()
      await view.findByRole('heading', { name: '解析結果' })
      api.startAnalysisJob.mockRejectedValueOnce(new Error('別の解析を実行中です')).mockRejectedValueOnce('文字列')
      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      expect((await view.findByRole('alert')).textContent).toBe('別の解析を実行中です')
      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      await waitFor(() => expect(view.getByRole('alert').textContent).toBe('文字列'))
      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      await view.findByRole('status', { name: '解析の進み具合' })
      api.getAnalysisJob.mockResolvedValue({ ...runningJob, status: 'done', done: 1, failures: [{ project_id: 1, name: '図面1', message: 'x' }] })
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect((await view.findByRole('alert')).textContent).toContain('1 図面は解析できませんでした（図面1）')
      expect(view.getByTestId('wire-layer')).toBeTruthy()
      expect(view.getByTestId('symbol-layer')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('jumps to the typed drawing number and resets an out-of-range number', async () => {
    const view = openPage()
    const jump = (await view.findByRole('textbox', { name: '図面番号を入力して移動' })) as HTMLInputElement
    expect(jump.value).toBe('1')
    expect(view.getByText('/ 2')).toBeTruthy()

    act(() => jump.focus())
    fireEvent.change(jump, { target: { value: '9' } })
    fireEvent.keyDown(jump, { key: 'Enter' })
    expect(jump.value).toBe('1')
    expect(view.router.state.location.pathname).toBe('/projects/1/analysis')

    act(() => jump.focus())
    fireEvent.change(jump, { target: { value: '２' } })
    fireEvent.keyDown(jump, { key: 'Enter' })
    await waitFor(() => expect(view.router.state.location.pathname).toBe('/projects/2/analysis'))
    expect(await view.findByText('図面2')).toBeTruthy()
  })

  it('labels AI counts apart from saved annotations and draws region frames only on request', async () => {
    api.getPredictions.mockResolvedValue({
      count: 1, region_count: 1,
      detections: [{ class_key: 'connector', class_label: 'コネクタ', cx: 0.1, cy: 0.25, w: 0.04, h: 0.06, confidence: 0.97 }],
      regions: [{ class_key: 'connector', class_label: 'コネクタ', cx: 0.5, cy: 0.5, w: 0.5, h: 0.5, confidence: 0.6 }],
    })
    const view = openPage()
    await view.findByRole('heading', { name: '解析結果' })
    expect(view.getByText('保存済みアノテーション: シンボル 1・配線 1')).toBeTruthy()

    const regionToggle = view.getByRole('checkbox', { name: /^領域枠（シンボル対象外）/ }) as HTMLInputElement
    expect(regionToggle.checked).toBe(false)
    expect(view.queryByTestId('region-layer')).toBeNull()
    expect(view.getByTestId('symbol-layer').querySelectorAll('rect')).toHaveLength(1)

    fireEvent.click(regionToggle)
    const region = view.getByTestId('region-layer').querySelector('rect')
    expect(region?.getAttribute('x')).toBe('250')
    expect(region?.getAttribute('width')).toBe('500')
  })

  it('読み込めなければ理由を出し、別の図面へ移った後に返った古い応答は捨てる', async () => {
    api.getProject.mockRejectedValueOnce(new Error('図面がありません'))
    const view = openPage()
    expect((await view.findByRole('alert')).textContent).toBe('図面がありません')
    view.unmount()

    api.getProject.mockRejectedValueOnce('文字列の失敗')
    const second = openPage()
    await waitFor(() => expect(second.getByRole('alert').textContent).toBe('文字列の失敗'))
    second.unmount()

    let resolveOld: (value: unknown) => void = () => undefined
    let rejectOld: (reason: unknown) => void = () => undefined
    api.getStructure.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
    const third = openPage()
    await act(() => third.router.navigate('/projects/2/analysis'))
    expect(await third.findByText('図面2')).toBeTruthy()
    await act(async () => resolveOld({ project_id: 1, result }))
    expect(third.getByText('図面2')).toBeTruthy()
    third.unmount()

    api.getStructure.mockReturnValueOnce(new Promise((_, reject) => { rejectOld = reject }))
    const fourth = openPage()
    await act(() => fourth.router.navigate('/projects/2/analysis'))
    await fourth.findByText('図面2')
    await act(async () => rejectOld(new Error('古い失敗')))
    expect(fourth.queryByRole('alert')).toBeNull()
  })

  it('構造 JSON を書き出せなければ理由を出し、移動後の失敗は出さない', async () => {
    api.exportStructure.mockRejectedValueOnce(new Error('書き出せません')).mockRejectedValueOnce('文字列')
    const view = openPage()
    await view.findByRole('heading', { name: '解析結果' })
    fireEvent.click(view.getByRole('button', { name: '構造JSON' }))
    expect((await view.findByRole('alert')).textContent).toBe('書き出せません')
    fireEvent.click(view.getByRole('button', { name: '構造JSON' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toBe('文字列'))

    let rejectLate: (reason: unknown) => void = () => undefined
    api.exportStructure.mockReturnValueOnce(new Promise((_, reject) => { rejectLate = reject }))
    fireEvent.click(view.getByRole('button', { name: '構造JSON' }))
    await act(() => view.router.navigate('/projects/2/analysis'))
    await view.findByText('図面2')
    await act(async () => rejectLate(new Error('遅い失敗')))
    expect(view.queryByRole('alert')).toBeNull()
    expect((view.getByRole('button', { name: '構造JSON' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('未解析の図面はそう示し、解析の警告を並べる', async () => {
    api.getPredictions.mockResolvedValue({ count: 0, detections: [] })
    api.getStructure.mockResolvedValueOnce({ project_id: 1, result: null })
    const view = openPage()
    expect(await view.findByText('未解析')).toBeTruthy()
    expect(view.getByRole('button', { name: '解析を実行' })).toBeTruthy()
    view.unmount()

    api.getStructure.mockResolvedValueOnce({ project_id: 1, result: { ...result, warnings: ['ラベルを読めません'] } })
    const warned = openPage()
    expect(await warned.findByText('ラベルを読めません')).toBeTruthy()
  })

  it('再解析の中止に失敗しても画面は落ちず、別の図面へ移った後の完了では読み直さない', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const view = openPage()
      await view.findByRole('heading', { name: '解析結果' })
      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      await view.findByRole('status', { name: '解析の進み具合' })
      api.cancelAnalysisJob.mockRejectedValueOnce(new Error('止められません'))
      fireEvent.click(view.getByRole('button', { name: '中止' }))
      await waitFor(() => expect(api.cancelAnalysisJob).toHaveBeenCalled())
      expect(view.queryByRole('alert')).toBeNull()

      // 完了の後の読み直し中に別の図面へ移ったら、古い結果も失敗も出さない
      let resolveReload: (value: unknown) => void = () => undefined
      api.getStructure.mockReturnValueOnce(new Promise((resolve) => { resolveReload = resolve }))
      api.getAnalysisJob.mockResolvedValue({ ...runningJob, status: 'done', done: 1 })
      await act(() => vi.advanceTimersByTimeAsync(1000))
      await act(() => view.router.navigate('/projects/2/analysis'))
      await view.findByText('図面2')
      await act(async () => resolveReload({ project_id: 1, result }))
      expect(view.getByText('図面2')).toBeTruthy()
      expect(view.queryByText('解析結果を保存しました')).toBeNull()

      fireEvent.click(view.getByRole('button', { name: '再解析' }))
      await view.findByRole('status', { name: '解析の進み具合' })
      let rejectReload: (reason: unknown) => void = () => undefined
      api.getStructure.mockReturnValueOnce(new Promise((_, reject) => { rejectReload = reject }))
      await act(() => vi.advanceTimersByTimeAsync(1000))
      await act(() => view.router.navigate('/projects/1/analysis'))
      await view.findByText('図面1')
      await act(async () => rejectReload(new Error('古い失敗')))
      expect(view.queryByRole('alert')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})

