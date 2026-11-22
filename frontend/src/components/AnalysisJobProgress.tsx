import { Loader2 } from 'lucide-react'

import type { AnalysisJob } from '../types'

/** 裏の解析のジョブ（DD-0039）の進み具合: 何枚目か・いまの図面・中止。画面はふさがない。 */
export function AnalysisJobProgress({ job, onCancel }: { job: AnalysisJob; onCancel: () => void }) {
  const percent = job.total ? Math.round((100 * job.done) / job.total) : 0
  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50/70 p-3 text-xs text-blue-900" role="status" aria-label="解析の進み具合">
      <div className="flex flex-wrap items-center gap-2">
        <Loader2 size={14} className="animate-spin" aria-hidden="true" />
        <span className="font-bold">解析中 {job.done} / {job.total} 図面</span>
        {job.failures.length > 0 && <span className="font-bold text-rose-600">失敗 {job.failures.length}</span>}
        <button type="button" className="btn btn-sm ml-auto" onClick={onCancel} disabled={job.cancel_requested}>
          {job.cancel_requested ? '中止します…' : '中止'}
        </button>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white" aria-hidden="true">
        <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-1 text-[11px] text-blue-800">
        {job.current ? `いま: ${job.current}。` : ''}図面ごとに保存します。画面を離れても解析は続きます（EXE は終わるまで閉じないでください）。
      </p>
    </div>
  )
}
