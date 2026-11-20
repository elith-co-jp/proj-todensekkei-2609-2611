import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, FileCode2, Loader2, Network, Power, PowerOff } from 'lucide-react'

import { api } from '../../api/client'
import type { CircuitChange, CircuitImpact, CircuitSummary, CircuitWhatIf } from '../../types'

/** 該当の記号を選んだ状態でエディタを開くリンク */
function editorLink(projectId: number, symbolRef: string) {
  return `/projects/${projectId}?symbol=${encodeURIComponent(symbolRef)}`
}

const onOff = (v: boolean | null) => (v == null ? '—' : v ? 'ON' : 'OFF')

type Props = {
  projectIds: number[]
  summary: CircuitSummary
  inputs: Record<string, boolean>
  energized: Record<string, boolean>
}

/** 「A を変えたらどうなるか」と波及範囲（DD-0029）。今の状態から機器を動かす・止めたときの変化と、回路のつながりから見た影響範囲。 */
export default function WhatIfPanel({ projectIds, summary, inputs, energized }: Props) {
  const devices = [...summary.inputs.map((i) => i.device), ...summary.coils.map((c) => c.device)]
  const [device, setDevice] = useState(devices[0] ?? '')
  const [impact, setImpact] = useState<CircuitImpact | null>(null)
  const [whatIf, setWhatIf] = useState<{ title: string; result: CircuitWhatIf } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const guard = async (work: () => Promise<void>) => {
    setBusy(true)
    try {
      await work()
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const showImpact = () => guard(async () => setImpact(await api.circuitImpact(projectIds, device)))
  const force = (on: boolean) =>
    guard(async () => {
      const change: CircuitChange = { devices: { [device]: on } }
      const result = await api.circuitWhatIf(projectIds, inputs, energized, change)
      setWhatIf({ title: `${device} を強制 ${on ? 'ON' : 'OFF'} したとき`, result })
    })

  if (devices.length === 0) return null
  return (
    <section className="card p-4" aria-label="もしも・波及範囲">
      <div className="mb-2 text-sm font-black">波及・もしも</div>
      <select
        aria-label="機器"
        className="mb-2 w-full rounded-xl border border-slate-200 px-2 py-1.5 text-xs"
        value={device}
        onChange={(e) => setDevice(e.target.value)}
      >
        {devices.map((d) => <option key={d} value={d}>{d}</option>)}
      </select>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn btn-sm" onClick={() => void showImpact()} disabled={busy || !device}>
          <Network size={12} /> 波及範囲
        </button>
        <button type="button" className="btn btn-sm" onClick={() => void force(true)} disabled={busy || !device}
          title="今の状態から、この機器を動作させたときの変化（溶着・誤動作の確認）">
          <Power size={12} /> 強制 ON
        </button>
        <button type="button" className="btn btn-sm" onClick={() => void force(false)} disabled={busy || !device}
          title="今の状態から、この機器が動かなくなったときの変化（断線・故障の確認）">
          <PowerOff size={12} /> 強制 OFF
        </button>
        {busy && <Loader2 size={14} className="animate-spin text-slate-400" />}
      </div>
      {error && <p className="mt-2 text-[11px] font-bold text-rose-700" role="alert">{error}</p>}

      {whatIf && (
        <div className="mt-3 rounded-xl bg-slate-50 p-2 text-[11px]" aria-label="もしもの結果">
          <div className="mb-1 font-bold text-slate-700">{whatIf.title}</div>
          {whatIf.result.changes.coils.length + whatIf.result.changes.loads.length === 0 && (
            <p className="text-slate-400">リレー・負荷の状態は変わりません</p>
          )}
          <ul className="space-y-0.5">
            {whatIf.result.changes.coils.map((c) => (
              <li key={c.device}>リレー <b>{c.device}</b>: {onOff(c.before)} → <b>{onOff(c.after)}</b></li>
            ))}
            {whatIf.result.changes.loads.filter((x) => !x.device).map((x) => (
              <li key={`${x.project_id}:${x.symbol_ref}`}>
                負荷 <b>{x.label || x.symbol_ref}</b>{x.page && <span className="text-slate-400">（{x.page}）</span>}: {onOff(x.before)} → <b>{onOff(x.after)}</b>
                <Link className="ml-1 text-cyan-700 underline" to={editorLink(x.project_id, x.symbol_ref)}>図面</Link>
              </li>
            ))}
          </ul>
          {whatIf.result.warnings.map((w) => (
            <p key={w} className="mt-1 flex gap-1 font-bold text-rose-700"><AlertTriangle size={12} /> {w}</p>
          ))}
        </div>
      )}

      {impact && (
        <div className="mt-3 rounded-xl bg-amber-50 p-2 text-[11px]" aria-label="波及範囲">
          <div className="mb-1 font-bold text-slate-700">
            {impact.device} の波及範囲（リレー {impact.devices.length}・負荷 {impact.loads.length}）
          </div>
          {impact.devices.length + impact.loads.length === 0 && <p className="text-slate-400">ありません</p>}
          <ul className="thin-scroll max-h-56 space-y-0.5 overflow-auto">
            {impact.loads.map((x) => (
              <li key={`${x.project_id}:${x.symbol_ref}`}>
                <span className="mr-1 rounded bg-white px-1 font-mono text-[10px] text-slate-500">{x.depth} 段</span>
                {x.device ? <>リレー <b>{x.device}</b></> : <>負荷 <b>{x.label || x.symbol_ref}</b></>}
                <span className="text-slate-400">（{x.via} の接点から{x.page ? `・${x.page}` : ''}）</span>
                <Link className="ml-1 text-cyan-700 underline" to={editorLink(x.project_id, x.symbol_ref)}>図面</Link>
              </li>
            ))}
          </ul>
          {impact.upstream.length > 0 && (
            <p className="mt-1 text-slate-500">上流の機器: {impact.upstream.join('、')}</p>
          )}
        </div>
      )}

      <button type="button" className="btn btn-sm mt-3" onClick={() => void api.downloadCircuitCode(projectIds)} title="回路を Python のコード（.py）で書き出す">
        <FileCode2 size={12} /> 回路コード出力
      </button>
    </section>
  )
}
