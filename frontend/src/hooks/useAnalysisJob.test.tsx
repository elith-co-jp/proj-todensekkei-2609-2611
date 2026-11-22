// @vitest-environment jsdom

import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AnalysisJob } from '../types'
import { ANALYSIS_POLL_MS, analysisJobMessage, useAnalysisJob } from './useAnalysisJob'

const apiMocks = vi.hoisted(() => ({
  listAnalysisJobs: vi.fn(),
  getAnalysisJob: vi.fn(),
  startAnalysisJob: vi.fn(),
  cancelAnalysisJob: vi.fn(),
}))
vi.mock('../api/client', () => ({ api: apiMocks }))

function job(patch: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    id: 3, status: 'running', total: 2, done: 0, current: null, conf: 0.5, connector_conf: 0.6, results: [], failures: [],
    error: null, cancel_requested: false, detection_count: 0, started_at: null, finished_at: null, ...patch,
  }
}

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve() })
const tick = () => act(async () => { vi.advanceTimersByTime(ANALYSIS_POLL_MS); await Promise.resolve(); await Promise.resolve() })

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  apiMocks.listAnalysisJobs.mockResolvedValue([])
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useAnalysisJob', () => {
  it('開いたときの解析中ジョブを引き継ぎ、終わるまで追い、終わったら一度だけ知らせる', async () => {
    apiMocks.listAnalysisJobs.mockResolvedValue([job({ id: 1, status: 'done' }), job({ id: 3 })])
    apiMocks.getAnalysisJob
      .mockResolvedValueOnce(job({ done: 1 }))
      .mockResolvedValueOnce(job({ status: 'done', done: 2 }))
    const onFinish = vi.fn()
    const { result } = renderHook(() => useAnalysisJob(onFinish))
    await flush()
    expect(result.current.job?.id).toBe(3)
    expect(result.current.running).toBe(true)
    await tick()
    expect(result.current.job?.done).toBe(1)
    expect(onFinish).not.toHaveBeenCalled()
    await tick()
    expect(result.current.running).toBe(false)
    expect(onFinish).toHaveBeenCalledOnce()
    await tick()
    expect(apiMocks.getAnalysisJob).toHaveBeenCalledTimes(2)
  })

  it('一覧が読めない・閉じた後に返った場合は何もしない', async () => {
    apiMocks.listAnalysisJobs.mockRejectedValueOnce(new Error('x'))
    const first = renderHook(() => useAnalysisJob(vi.fn()))
    await flush()
    expect(first.result.current.job).toBeNull()

    let resolve: (jobs: AnalysisJob[]) => void = () => undefined
    apiMocks.listAnalysisJobs.mockReturnValueOnce(new Promise((r) => { resolve = r }))
    const second = renderHook(() => useAnalysisJob(vi.fn()))
    second.unmount()
    await act(async () => resolve([job()]))
    expect(second.result.current.job).toBeNull()
  })

  it('始めたときのモデルを引き継ぎ、問い合わせの失敗では止まらない', async () => {
    const model = { name: 'best.pt' } as unknown as AnalysisJob['model']
    apiMocks.startAnalysisJob.mockResolvedValue(job({ model }))
    apiMocks.getAnalysisJob.mockRejectedValueOnce(new Error('一時的')).mockResolvedValueOnce(job({ status: 'done' }))
    const onFinish = vi.fn()
    const { result } = renderHook(() => useAnalysisJob(onFinish))
    await act(async () => { await result.current.start([1, 2], 0.4, null) })
    expect(apiMocks.startAnalysisJob).toHaveBeenCalledWith([1, 2], 0.4, null)
    await tick()
    expect(result.current.running).toBe(true)
    await tick()
    expect(result.current.job?.model).toBe(model)
    expect(onFinish).toHaveBeenCalledWith(expect.objectContaining({ model }))
  })

  it('画面を離れた後に返った進み具合は捨てる', async () => {
    let resolve: (j: AnalysisJob) => void = () => undefined
    apiMocks.listAnalysisJobs.mockResolvedValue([job()])
    apiMocks.getAnalysisJob.mockReturnValue(new Promise((r) => { resolve = r }))
    const onFinish = vi.fn()
    const view = renderHook(() => useAnalysisJob(onFinish))
    await flush()
    await tick()
    view.unmount()
    await act(async () => resolve(job({ status: 'done' })))
    expect(onFinish).not.toHaveBeenCalled()
  })

  it('中止はジョブがあるときだけ送る', async () => {
    apiMocks.startAnalysisJob.mockResolvedValue(job())
    apiMocks.cancelAnalysisJob.mockResolvedValue(job({ cancel_requested: true }))
    const { result } = renderHook(() => useAnalysisJob(vi.fn()))
    await act(async () => { await result.current.cancel() })
    expect(apiMocks.cancelAnalysisJob).not.toHaveBeenCalled()
    await act(async () => { await result.current.start([]) })
    await act(async () => { await result.current.cancel() })
    expect(apiMocks.cancelAnalysisJob).toHaveBeenCalledWith(3)
    expect(result.current.job?.cancel_requested).toBe(true)
  })
})

describe('analysisJobMessage', () => {
  it('完了・中止・失敗・一部失敗・警告を一文にする', () => {
    const page = (wire_count: number | undefined, warnings?: string[]) =>
      ({ wire_count, warnings }) as unknown as AnalysisJob['results'][number]
    expect(analysisJobMessage(job({ status: 'done', detection_count: 5, results: [page(3), page(undefined)] }))).toEqual({
      ok: true, text: '解析完了: シンボル 5 件、配線 3 本（2 図面）。',
    })
    expect(analysisJobMessage(job({ status: 'cancelled', done: 1, total: 4, results: [page(2, ['x'])] })).text).toBe(
      '解析を中止しました（1 / 4 図面）。シンボル 0 件、配線 2 本（1 図面）。一部のラベル読み取りに問題があります。解析結果を確認してください。',
    )
    expect(analysisJobMessage(job({ status: 'failed', error: 'モデルがありません' }))).toEqual({ ok: false, text: 'モデルがありません' })
    expect(analysisJobMessage(job({ status: 'failed' })).text).toBe('解析に失敗しました')
    const partial = analysisJobMessage(job({
      status: 'done',
      failures: [{ project_id: 7, name: null, message: 'x' }, { project_id: 8, name: '図面8', message: 'y' }],
    }))
    expect(partial.ok).toBe(false)
    expect(partial.text).toContain('2 図面は解析できませんでした（7、図面8）。')
  })
})
