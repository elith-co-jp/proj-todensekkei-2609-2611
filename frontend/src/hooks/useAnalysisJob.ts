import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import type { AnalysisJob } from '../types'

export const ANALYSIS_POLL_MS = 1000

/**
 * 図面の解析を裏のジョブ（DD-0039）で回す。1 枚ずつ解析して図面ごとに保存し、失敗した図面は飛ばして続ける。
 * 画面を開いたときに解析中のジョブがあれば、その進み具合を出し続ける。終わったら onFinish を 1 回呼ぶ。
 */
export function useAnalysisJob(onFinish: (job: AnalysisJob) => void | Promise<void>) {
  const [job, setJob] = useState<AnalysisJob | null>(null)
  const finishRef = useRef(onFinish)
  finishRef.current = onFinish

  useEffect(() => {
    let alive = true
    void api
      .listAnalysisJobs()
      .then((jobs) => {
        const running = jobs.find((j) => j.status === 'running')
        if (alive && running) setJob((prev) => prev ?? running)
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  const running = job?.status === 'running'
  const jobId = job?.id
  useEffect(() => {
    if (!running || jobId == null) return
    let finished = false
    const timer = window.setInterval(() => {
      void api
        .getAnalysisJob(jobId)
        .then((next) => {
          if (finished) return
          setJob((prev) => ({ ...next, model: next.model ?? prev?.model }))
          if (next.status !== 'running') {
            finished = true
            void finishRef.current(next)
          }
        })
        .catch(() => undefined)
    }, ANALYSIS_POLL_MS)
    return () => {
      finished = true
      window.clearInterval(timer)
    }
  }, [running, jobId])

  /** projectIds が空なら全図面。しきい値は既定（api/defaults.ts）を使う。 */
  const start = useCallback(async (projectIds: number[]) => {
    const started = await api.startAnalysisJob(projectIds)
    setJob(started)
    return started
  }, [])

  const cancel = useCallback(async () => {
    if (jobId == null) return
    setJob(await api.cancelAnalysisJob(jobId))
  }, [jobId])

  return { job, running, start, cancel }
}

/** 終わったジョブの結果を 1 文にする（一覧・解析結果の画面の通知）。 */
export function analysisJobMessage(job: AnalysisJob): { ok: boolean; text: string } {
  if (job.status === 'failed') return { ok: false, text: job.error ?? '解析に失敗しました' }
  const wires = job.results.reduce((n, page) => n + (page.wire_count ?? 0), 0)
  const head = job.status === 'cancelled' ? `解析を中止しました（${job.done} / ${job.total} 図面）。` : '解析完了: '
  const failed = job.failures.length
    ? `${job.failures.length} 図面は解析できませんでした（${job.failures.map((f) => f.name ?? f.project_id).join('、')}）。`
    : ''
  const warn = job.results.some((page) => page.warnings?.length) ? '一部のラベル読み取りに問題があります。解析結果を確認してください。' : ''
  return {
    ok: job.failures.length === 0,
    text: `${head}シンボル ${job.detection_count} 件、配線 ${wires} 本（${job.results.length} 図面）。${failed}${warn}`,
  }
}
