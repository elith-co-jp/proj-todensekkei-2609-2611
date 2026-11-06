import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, ListChecks, TriangleAlert, XCircle } from 'lucide-react'

import { api } from '../api/client'
import type { AdoptionReview, ClassMetric, ClassStats, HardDrawings, TrainingRun } from '../types'

const VERDICT = {
  adopt: { label: '採用をおすすめします', tone: 'border-emerald-200 bg-emerald-50 text-emerald-900', Icon: CheckCircle2 },
  hold: { label: '見送りをおすすめします', tone: 'border-rose-200 bg-rose-50 text-rose-900', Icon: XCircle },
  caution: { label: 'この評価だけでは判断できません', tone: 'border-amber-200 bg-amber-50 text-amber-900', Icon: TriangleAlert },
  unknown: { label: '自動では判定できません', tone: 'border-slate-200 bg-slate-50 text-slate-700', Icon: TriangleAlert },
} as const

/** 採用判定の基準（学習に使っていない図面で、全体とクラス別の mAP50-95 が下がらないか）による推奨。 */
export function AdoptionReviewNote({ review }: { review: AdoptionReview }) {
  const v = VERDICT[review.verdict]
  return (
    <div className={`flex gap-2 rounded-2xl border px-3 py-2.5 ${v.tone}`} role="status">
      <v.Icon size={16} className="mt-0.5 flex-none" aria-hidden="true" />
      <div className="min-w-0 text-[12px] leading-5">
        <div className="font-black">{v.label}</div>
        <ul className="mt-0.5 space-y-0.5">
          {review.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </div>
    </div>
  )
}

/** 新旧モデルのクラス別 mAP50-95。下がったクラスを赤で示す。 */
export function PerClassComparison({ run }: { run: TrainingRun }) {
  const current = run.per_class ?? {}
  const baseline = run.baseline_per_class ?? {}
  const keys = Array.from(new Set([...Object.keys(baseline), ...Object.keys(current)]))
  if (keys.length === 0) return null
  const row = (key: string): ClassMetric | undefined => current[key] ?? baseline[key]
  return (
    <div className="overflow-x-auto rounded-2xl border border-slate-200">
      <table className="w-full min-w-[520px] text-xs" aria-label="クラス別の評価">
        <thead>
          <tr className="bg-slate-50 text-left text-[10px] font-bold uppercase tracking-wider text-slate-400">
            <th className="whitespace-nowrap px-3 py-2">クラス</th>
            <th className="whitespace-nowrap px-3 py-2 text-right">評価用の件数</th>
            <th className="whitespace-nowrap px-3 py-2 text-right">新モデル mAP50-95</th>
            <th className="whitespace-nowrap px-3 py-2 text-right">比較対象</th>
            <th className="whitespace-nowrap px-3 py-2 text-right">差分</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => {
            const now = current[key]?.map50_95
            const before = baseline[key]?.map50_95
            const delta = now !== undefined && before !== undefined ? now - before : null
            return (
              <tr key={key} className="border-t border-slate-100">
                <td className="px-3 py-1.5 font-bold text-slate-700">{row(key)?.label ?? key}</td>
                <td className="px-3 py-1.5 text-right font-mono text-slate-500">{row(key)?.instances ?? '—'}</td>
                <td className="px-3 py-1.5 text-right font-mono text-slate-800">{now?.toFixed(3) ?? '—'}</td>
                <td className="px-3 py-1.5 text-right font-mono text-slate-500">{before?.toFixed(3) ?? '—'}</td>
                <td className="px-3 py-1.5 text-right font-mono">
                  {delta === null ? (
                    <span className="text-slate-300">—</span>
                  ) : (
                    <span className={delta > 0 ? 'font-bold text-emerald-600' : delta < 0 ? 'font-bold text-rose-600' : 'text-slate-400'}>
                      {delta > 0 ? '+' : ''}
                      {delta.toFixed(3)}
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** 学習に使う図面のクラス別の件数と、AI と正解の食い違いが大きい図面（難例）。 */
export function TrainingDataPanel({ refreshKey }: { refreshKey: unknown }) {
  const [stats, setStats] = useState<ClassStats | null>(null)
  const [hard, setHard] = useState<HardDrawings | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    Promise.all([api.trainingClassStats(), api.trainingHardDrawings()])
      .then(([s, h]) => {
        if (!alive) return
        setStats(s)
        setHard(h)
        setError(null)
      })
      .catch((caught: unknown) => alive && setError(caught instanceof Error ? caught.message : String(caught)))
    return () => {
      alive = false
    }
  }, [refreshKey])

  const max = Math.max(1, ...(stats?.classes.map((c) => c.done_count) ?? [1]))
  const thin = stats?.classes.filter((c) => c.thin) ?? []
  const hardRows = hard?.drawings.filter((d) => d.hard) ?? []

  return (
    <div className="space-y-4 border-t border-slate-100 p-4 sm:p-5" aria-label="学習データの状態">
      <div className="flex items-center gap-2 text-[11px] font-bold text-slate-500">
        <ListChecks size={14} aria-hidden="true" /> 学習データの状態
      </div>
      {error && <p className="text-[11px] text-rose-600">学習データの状態を読み込めませんでした: {error}</p>}
      {stats && (
        <div>
          <p className="mb-2 text-[11px] leading-5 text-slate-600">
            完了の図面 {stats.done_drawings} 枚のクラス別の正解件数です。
            {thin.length > 0
              ? ` ${stats.thin_threshold} 件未満のクラス（${thin.map((c) => c.label).join('・')}）は精度が安定しにくいため、そのクラスが描かれた図面を優先して正解を付けてください。`
              : ' すべてのクラスに十分な件数があります。'}
          </p>
          <div className="thin-scroll max-h-64 overflow-auto rounded-2xl border border-slate-200">
            <table className="w-full text-xs" aria-label="クラス別の正解件数">
              <thead>
                <tr className="bg-slate-50 text-left text-[10px] font-bold text-slate-400">
                  <th className="whitespace-nowrap px-3 py-2">クラス</th>
                  <th className="whitespace-nowrap px-3 py-2">完了の図面の件数</th>
                  <th className="whitespace-nowrap px-3 py-2 text-right">図面数</th>
                  <th className="whitespace-nowrap px-3 py-2 text-right">全図面</th>
                </tr>
              </thead>
              <tbody>
                {stats.classes.map((c) => (
                  <tr key={c.key} className="border-t border-slate-100">
                    <td className="px-3 py-1.5">
                      <span className="inline-flex items-center gap-1.5 font-bold text-slate-700">
                        <span className="h-2.5 w-2.5 flex-none rounded-sm" style={{ background: c.color }} aria-hidden="true" />
                        {c.label}
                      </span>
                    </td>
                    <td className="px-3 py-1.5">
                      <span className="flex items-center gap-2">
                        <span className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-slate-100 sm:block" aria-hidden="true">
                          <span
                            className={`block h-full rounded-full ${c.thin ? 'bg-amber-400' : 'bg-emerald-500'}`}
                            style={{ width: `${(100 * c.done_count) / max}%` }}
                          />
                        </span>
                        <span className="font-mono text-slate-700">{c.done_count}</span>
                        {c.thin && <span className="whitespace-nowrap rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-700">少ない</span>}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-slate-500">{c.done_drawings}</td>
                    <td className="px-3 py-1.5 text-right font-mono text-slate-400">{c.total_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {hard && (
        <div>
          <p className="mb-2 text-[11px] leading-5 text-slate-600">
            {hard.drawings.length === 0
              ? 'AI の結果がある完了の図面はまだありません。解析して直した図面がここに並びます。'
              : hardRows.length > 0
                ? `AI の検出と直した後の正解の一致が ${hard.threshold} 未満の図面が ${hardRows.length} 枚あります（難例）。見落としや誤検出の多い図面を見直し、似た図面の正解を増やしてください。`
                : `AI の検出と直した後の正解の一致は、どの図面も ${hard.threshold} 以上です。`}
          </p>
          {hard.drawings.length > 0 && (
            <div className="thin-scroll max-h-56 overflow-auto rounded-2xl border border-slate-200">
              <table className="w-full text-xs" aria-label="AI と正解の一致が低い図面">
                <thead>
                  <tr className="bg-slate-50 text-left text-[10px] font-bold text-slate-400">
                    <th className="whitespace-nowrap px-3 py-2">図面</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right">一致</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right">見落とし</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right">誤検出</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right">種別違い</th>
                  </tr>
                </thead>
                <tbody>
                  {hard.drawings.slice(0, 10).map((d) => (
                    <tr key={d.project_id} className="border-t border-slate-100">
                      <td className="px-3 py-1.5">
                        <span className="flex items-center gap-1.5">
                          <Link to={`/projects/${d.project_id}`} className="whitespace-nowrap font-bold text-slate-700 hover:text-cyan-700">
                            {d.sheet_no || d.name}
                          </Link>
                          {d.hard && <span className="flex-none whitespace-nowrap rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] font-bold text-rose-700">難例</span>}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-slate-800">{d.f1.toFixed(3)}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-slate-500">{d.missed}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-slate-500">{d.false_detections}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-slate-500">{d.relabeled}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

