import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'

import { api } from '../../api/client'
import type { ReviewSummary, WiringReviewCounts } from '../../types'

function wiringLine(label: string, counts: WiringReviewCounts) {
  if (!counts.imported) return `${label} ${counts.ai_count}（未取り込み）`
  return `${label} ${counts.ai_count} → 採用 ${counts.kept}・削除 ${counts.deleted}・手動追加 ${counts.manual_added}`
}

function statusLine(summary: ReviewSummary) {
  if (!summary.imported) return { tone: 'text-slate-600', text: 'AI 検出はまだ取り込まれていません。' }
  if (summary.in_next_training) {
    return {
      tone: 'text-emerald-800',
      text: `この図面は次回の学習に含まれます。削除したAI検出（${summary.deleted}件）の位置は『シンボルではない（背景）』として学習され、同じ誤検出が出にくくなります。`,
    }
  }
  return {
    tone: 'text-amber-800',
    text: '状態を『完了』にすると次回の学習に含まれます。完了にする前に見落としたシンボルが無いか確認してください（未登録のシンボルも『背景』として学習されます）。',
  }
}

/* 集計はサーバーに保存済みの内容から出すため、表示中かつ保存が追いついた時点で取り直す */
export function ReviewSummaryCard({
  projectId,
  visible,
  serverSynced,
}: {
  projectId: number
  visible: boolean
  serverSynced: boolean
}) {
  const [summary, setSummary] = useState<ReviewSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const latestRequest = useRef(0)

  const load = useCallback(async () => {
    const request = ++latestRequest.current
    setLoading(true)
    try {
      const next = await api.getReviewSummary(projectId)
      if (request !== latestRequest.current) return
      setSummary(next)
      setError(null)
    } catch (caught) {
      if (request !== latestRequest.current) return
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (request === latestRequest.current) setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    if (visible && serverSynced) void load()
  }, [load, serverSynced, visible])

  const line = summary ? statusLine(summary) : null

  return (
    <section
      aria-labelledby="review-summary-title"
      aria-busy={loading}
      className="rounded-xl border border-cyan-200 bg-cyan-50/60 p-3 text-xs leading-relaxed"
    >
      <div className="flex items-center gap-2">
        <h3 id="review-summary-title" className="flex-1 font-bold text-slate-800">
          AI 検出の確認状況
        </h3>
        <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={12} className={loading ? 'animate-spin' : undefined} aria-hidden="true" />
          更新
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-rose-700">
          確認状況を取得できませんでした: {error}
        </p>
      )}
      {!summary && !error && <p className="mt-2 text-slate-500">読み込み中…</p>}
      {summary && line && (
        <>
          {summary.imported ? (
            <p className="mt-2 font-semibold text-slate-800">
              AI検出 {summary.ai_detection_count} → 採用 {summary.kept}・種別修正 {summary.relabeled}・削除{' '}
              {summary.deleted}・手動追加 {summary.manual_added}
            </p>
          ) : (
            <p className="mt-2 font-semibold text-slate-800">AI検出 {summary.ai_detection_count}</p>
          )}
          {summary.region_excluded_count > 0 && (
            <p className="mt-1 text-slate-600">
              領域枠 {summary.region_excluded_count} 件はシンボル対象外として除外
            </p>
          )}
          {summary.wiring && (
            <>
              <p className="mt-1 font-semibold text-slate-800">{wiringLine('AI端子', summary.wiring.terminals)}</p>
              <p className="mt-1 font-semibold text-slate-800">{wiringLine('AI配線', summary.wiring.connections)}</p>
            </>
          )}
          {summary.coils && summary.coils.count > 0 && (
            <div className="mt-1">
              <p className={`font-semibold ${summary.coils.issues.length ? 'text-amber-800' : 'text-slate-800'}`}>
                コイルの配線 {summary.coils.count - summary.coils.issues.length}/{summary.coils.count} 個が取り決めどおり
              </p>
              {summary.coils.issues.length > 0 && (
                <ul aria-label="取り決めに合わないコイル" className="mt-1 list-disc pl-4 text-amber-800">
                  {summary.coils.issues.map((issue) => (
                    <li key={issue.symbol_ref}>
                      {issue.label || issue.symbol_ref}: {issue.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <p className={`mt-2 ${line.tone}`}>{line.text}</p>
        </>
      )}
    </section>
  )
}
