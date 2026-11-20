import { useState } from 'react'
import { AlertTriangle, Clock, Loader2, Play, Plus, Trash2 } from 'lucide-react'

import { api } from '../../api/client'
import type { CircuitSummary, CircuitTimeline, CircuitTimelineEvent } from '../../types'

const MODE = { on_delay: 'オンディレー', off_delay: 'オフディレー' }
const onOff = (v: boolean | null) => (v == null ? '—' : v ? 'ON' : 'OFF')

type Row = { at: number; device: string; on: boolean }

type Props = { projectIds: number[]; summary: CircuitSummary }

/** 時刻つきの操作とタイマーで、時間の流れを解く（DD-0033）。タイマーの時間を変えたら、も試せる。 */
export default function TimelinePanel({ projectIds, summary }: Props) {
  const inputs = summary.inputs.map((i) => i.device)
  const timers = summary.timers ?? []
  const [rows, setRows] = useState<Row[]>(inputs.length ? [{ at: 1, device: inputs[0], on: true }] : [])
  const [delays, setDelays] = useState<Record<string, number>>({})
  const [until, setUntil] = useState(20)
  const [result, setResult] = useState<CircuitTimeline | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (inputs.length === 0) return null

  const update = (i: number, patch: Partial<Row>) => setRows(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)))
  const run = async () => {
    setBusy(true)
    try {
      const events: CircuitTimelineEvent[] = rows.map((r) => ({ at: r.at, inputs: { [r.device]: r.on } }))
      setResult(await api.circuitTimeline(projectIds, events, delays, until))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card mt-4 p-4" aria-label="時間の流れ">
      <div className="mb-1 text-sm font-black">タイマー</div>
      <p className="mb-2 text-[11px] leading-5 text-slate-500" title="タイマーはコイルの属性「タイマーの種類」「タイマーの時間」から読みます">
        時刻・入力・ON / OFF を並べて実行。タイマーの時間も変えられます。
      </p>
      <ul className="space-y-1 text-[11px]" aria-label="操作">
        {rows.map((r, i) => (
          <li key={i} className="flex items-center gap-1">
            <input aria-label="時刻（秒）" type="number" min={0} step={0.5} value={r.at}
              className="w-16 rounded-lg border border-slate-200 px-1 py-0.5" onChange={(e) => update(i, { at: Number(e.target.value) })} />
            <span>秒</span>
            <select aria-label="入力" value={r.device} className="min-w-0 flex-1 rounded-lg border border-slate-200 px-1 py-0.5"
              onChange={(e) => update(i, { device: e.target.value })}>
              {inputs.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <select aria-label="ON/OFF" value={r.on ? 'on' : 'off'} className="rounded-lg border border-slate-200 px-1 py-0.5"
              onChange={(e) => update(i, { on: e.target.value === 'on' })}>
              <option value="on">ON</option>
              <option value="off">OFF</option>
            </select>
            <button type="button" className="btn btn-sm" aria-label="行を削除" title="行を削除" onClick={() => setRows(rows.filter((_, k) => k !== i))}>
              <Trash2 size={11} />
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
        <button type="button" className="btn btn-sm"
          onClick={() => setRows([...rows, { at: (rows.at(-1)?.at ?? 0) + 1, device: inputs[0], on: true }])}>
          <Plus size={11} /> 行を追加
        </button>
        <label className="flex items-center gap-1">終了
          <input aria-label="終了（秒）" type="number" min={0} value={until} className="w-16 rounded-lg border border-slate-200 px-1 py-0.5"
            onChange={(e) => setUntil(Number(e.target.value))} />秒
        </label>
      </div>
      {timers.length > 0 && (
        <ul className="mt-2 space-y-1 text-[11px]" aria-label="タイマー">
          {timers.map((t) => (
            <li key={t.device} className="flex items-center gap-1">
              <Clock size={11} className="text-slate-400" /> <b>{t.device}</b> <span>{MODE[t.mode]}</span>
              <input aria-label={`${t.device} の時間（秒）`} type="number" min={0} step={0.5} value={delays[t.device] ?? t.delay_s}
                className="w-16 rounded-lg border border-slate-200 px-1 py-0.5"
                onChange={(e) => setDelays({ ...delays, [t.device]: Number(e.target.value) })} />秒
              {delays[t.device] != null && delays[t.device] !== t.delay_s && <span className="text-amber-700">（図面は {t.delay_s} 秒）</span>}
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn btn-sm btn-accent mt-2" onClick={() => void run()} disabled={busy}>
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} 実行
      </button>
      {error && <p className="mt-2 text-[11px] font-bold text-rose-700" role="alert">{error}</p>}
      {result && (
        <ol className="mt-3 space-y-1 text-[11px]" aria-label="時間の流れの結果">
          {result.timeline.map((s, i) => (
            <li key={i} className="rounded-lg bg-slate-50 p-1.5">
              <span className="mr-1 rounded bg-white px-1 font-mono text-[10px] text-slate-500">{s.t} 秒</span>
              <span className="font-bold text-slate-700">{s.label}</span>
              {[...s.changes.coils.map((c) => `リレー ${c.device}: ${onOff(c.before)} → ${onOff(c.after)}`),
                ...s.changes.loads.filter((x) => !x.device).map((x) => `負荷 ${x.label || x.symbol_ref}: ${onOff(x.before)} → ${onOff(x.after)}`)]
                .map((line) => <div key={line} className="ml-2 text-slate-600">{line}</div>)}
              {Object.entries(s.timers).filter(([, v]) => v.due != null).map(([d, v]) => (
                <div key={d} className="ml-2 text-slate-400">{d} は {v.due} 秒に動く予定</div>
              ))}
              {s.warnings.map((w) => <p key={w} className="flex gap-1 font-bold text-rose-700"><AlertTriangle size={12} /> {w}</p>)}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
