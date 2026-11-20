import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Loader2, ScanSearch, Wrench } from 'lucide-react'

import { api } from '../../api/client'
import type { ConsistencyIssue, ConsistencyResult } from '../../types'

const SEVERITY = {
  error: { label: '矛盾', tone: 'bg-rose-100 text-rose-700' },
  warning: { label: '要確認', tone: 'bg-amber-100 text-amber-700' },
  info: { label: '参考', tone: 'bg-slate-100 text-slate-600' },
}

const STATUS = { ok: '一致', issues: '矛盾あり', skipped: '比べられない' }

function editorLink(projectId: number, symbolRef: string) {
  return `/projects/${projectId}?symbol=${encodeURIComponent(symbolRef)}`
}

type Props = {
  projectIds: number[]
  /** 直し方を適用した後（回路を組み直す） */
  onApplied?: () => void
}

/** 複数の解析方法の結果を突き合わせ、矛盾を精査して直す（DD-0031）。 */
export default function ConsistencyPanel({ projectIds, onApplied }: Props) {
  const [result, setResult] = useState<ConsistencyResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const guard = async (work: () => Promise<ConsistencyResult>) => {
    setBusy(true)
    try {
      setResult(await work())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const check = () => guard(() => api.checkConsistency(projectIds))
  const apply = (issues: ConsistencyIssue[]) =>
    guard(async () => {
      const r = await api.applyConsistencyFixes(projectIds, issues.map((i) => i.id))
      onApplied?.()
      return r
    })
  // まとめて直すのは「矛盾」（根拠が 2 つの方法でそろうもの）だけ。「要確認」の直し方は 1 件ずつ見て適用する。
  const fixable = result?.issues.filter((i) => i.fix && i.severity === 'error') ?? []

  return (
    <section className="card mt-5 p-4 sm:p-5" aria-label="解析結果の整合性">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-black">整合性チェック</div>
        <span className="flex-1" />
        {busy && <Loader2 size={14} className="animate-spin text-slate-400" />}
        <button type="button" className="btn btn-sm" onClick={() => void check()} disabled={busy || projectIds.length === 0}>
          <ScanSearch size={12} /> {result ? '再チェック' : 'チェック実行'}
        </button>
        {fixable.length > 0 && (
          <button type="button" className="btn btn-sm btn-accent" onClick={() => void apply(fixable)} disabled={busy}>
            <Wrench size={12} /> 一括修正（{fixable.length}）
          </button>
        )}
      </div>
      <p className="mt-1 text-[11px] leading-5 text-slate-500" title="根拠が 2 つの方法でそろう矛盾はここで直せます。直した内容は記号のメモに残します">
        記号とリレー表、配線と電線番号、計算結果どうしを突き合わせます。
      </p>
      {error && <p className="mt-2 text-[11px] font-bold text-rose-700" role="alert">{error}</p>}

      {result && (
        <>
          {result.applied && result.applied.length > 0 && (
            <p className="mt-2 flex items-center gap-1 text-[11px] font-bold text-emerald-700">
              <CheckCircle2 size={13} /> {result.applied.length} 件を修正し、再チェックしました
            </p>
          )}
          <table className="mt-3 w-full text-left text-[11px]" aria-label="突き合わせた組み合わせ">
            <thead className="text-slate-400">
              <tr><th className="py-1">方法 A</th><th>方法 B</th><th>比べた数</th><th>結果</th></tr>
            </thead>
            <tbody>
              {result.checks.map((c) => (
                <tr key={c.check} className="border-t border-slate-100">
                  <td className="py-1">{c.method_a}</td>
                  <td>{c.method_b}</td>
                  <td className="font-mono">{c.compared}</td>
                  <td className={c.status === 'issues' ? 'font-bold text-rose-700' : c.status === 'ok' ? 'text-emerald-700' : 'text-slate-400'}>
                    {c.status === 'issues' ? `${STATUS.issues}（${c.issues}）` : STATUS[c.status]}
                    {c.skipped_reason && <span className="ml-1 text-slate-400">{c.skipped_reason}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {result.issues.length === 0 && <p className="mt-3 text-[11px] font-bold text-emerald-700">矛盾は見つかりませんでした</p>}
          <ul className="mt-3 space-y-2" aria-label="矛盾の一覧">
            {result.issues.map((issue) => (
              <li key={issue.id} className="rounded-xl border border-slate-200 p-3 text-[11px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${SEVERITY[issue.severity].tone}`}>
                    {SEVERITY[issue.severity].label}
                  </span>
                  <span className="font-bold text-slate-800">{issue.title}</span>
                </div>
                <p className="mt-1 text-slate-700">{issue.message}</p>
                <details className="mt-1">
                  <summary className="cursor-pointer text-slate-500">調べた内容</summary>
                  <ol className="ml-4 mt-1 list-decimal space-y-0.5 text-slate-600">
                    {issue.review.map((r, i) => <li key={i}>{r}</li>)}
                  </ol>
                </details>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="text-slate-600">修正案: <b>{issue.proposal}</b></span>
                  {issue.locations.filter((l) => l.symbol_ref).map((l) => (
                    <Link key={`${l.project_id}:${l.symbol_ref}`} className="text-cyan-700 underline" to={editorLink(l.project_id, l.symbol_ref!)}>
                      {l.page} {l.symbol_ref}
                    </Link>
                  ))}
                  {issue.fix && (
                    <button type="button" className="btn btn-sm" onClick={() => void apply([issue])} disabled={busy}>
                      <Wrench size={12} /> 修正を適用
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
