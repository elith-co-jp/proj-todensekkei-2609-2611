import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CheckCircle2, Download, Loader2, Maximize2, Play, Plus, RefreshCw, Trash2, X } from 'lucide-react'

import { api } from '../../api/client'
import type { LogicDiagramView, LogicSimulation } from '../../types'

type Row = { at: number; signal: string; on: boolean }

type Props = { projectIds: number[] }

const onOff = (v: boolean | undefined) => (v ? 'ON' : 'OFF')

/** タイムチャート（出力とコネクタの信号）。時刻ごとの ON / OFF を段の線で描く。 */
function Waves({ sim, signals, names, selected }: { sim: LogicSimulation; signals: string[]; names: Record<string, string>; selected: number | null }) {
  const end = Math.max(sim.end, 1)
  const W = 300
  const H = 18
  const x = (t: number) => 96 + (t / end) * (W - 100)
  const cursor = selected != null ? sim.timeline[selected]?.t : null
  return (
    <svg viewBox={`0 0 ${W} ${signals.length * (H + 6) + 16}`} className="w-full" role="img" aria-label="タイムチャート">
      {signals.map((sig, i) => {
        const wave = sim.waves[sig] ?? []
        const y0 = 4 + i * (H + 6)
        let d = ''
        wave.forEach(([t, v], k) => {
          const yy = v ? y0 + 2 : y0 + H
          const nextT = k + 1 < wave.length ? wave[k + 1][0] : end
          d += `${k === 0 ? 'M' : 'L'}${x(t)},${yy}L${x(nextT)},${yy}`
        })
        return (
          <g key={sig}>
            <text x={0} y={y0 + H / 2 + 4} fontSize={9} fill="#475569">{(names[sig] ?? sig).slice(0, 12)}</text>
            <path d={d} fill="none" stroke="#e11d48" strokeWidth={1.5} />
          </g>
        )
      })}
      {cursor != null && <line x1={x(cursor)} x2={x(cursor)} y1={0} y2={signals.length * (H + 6)} stroke="#0ea5e9" strokeDasharray="3 2" />}
      <text x={96} y={signals.length * (H + 6) + 12} fontSize={9} fill="#94a3b8">0 秒</text>
      <text x={W - 4} y={signals.length * (H + 6) + 12} fontSize={9} fill="#94a3b8" textAnchor="end">{end} 秒</text>
    </svg>
  )
}

/** 論理図（DD-0045）: 出力のコイルから接点の元へ辿り、AND・OR・NOT・タイマー・キープリレーの図にする。入力を切り替え、
 *  時刻つきの操作で動かし、成立している線をピンクで示す。同じ操作を回路の計算でも解いて照合する。 */
export default function LogicPanel({ projectIds }: Props) {
  const [view, setView] = useState<LogicDiagramView | null>(null)
  const [targets, setTargets] = useState<string[] | null>(null)
  const [inputs, setInputs] = useState<Record<string, boolean>>({})
  const [rows, setRows] = useState<Row[]>([])
  const [until, setUntil] = useState(10)
  const [sim, setSim] = useState<LogicSimulation | null>(null)
  const [step, setStep] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [large, setLarge] = useState(false)
  const [showTargets, setShowTargets] = useState(false)

  const load = useCallback(async (options: { targets?: string[]; inputs?: Record<string, boolean>; values?: Record<string, boolean> }) => {
    setBusy(true)
    try {
      const next = await api.logicDiagram(projectIds, options)
      setView(next)
      setError(null)
      return next
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy(false)
    }
  }, [projectIds])

  useEffect(() => {
    setSim(null)
    setStep(null)
    setInputs({})
    setTargets(null)
    void load({}).then((v) => {
      if (!v) return
      setTargets(v.outputs)
      const first = v.inputs.find((i) => i.kind === 'input')
      setRows(first ? [{ at: 1, signal: first.signal, on: true }] : [])
    })
  }, [load])

  const inputSignals = useMemo(() => view?.inputs ?? [], [view])
  const nameOf = useMemo(() => Object.fromEntries((view?.signals ?? []).map((s) => [s.id, (s.letter ? `${s.letter} ` : '') + (s.ref_name || s.name)])), [view])

  if (!view) {
    return (
      <section className="card p-4" aria-label="論理図">
        <div className="text-sm font-black">論理図</div>
        {error ? <p className="mt-2 text-[11px] text-rose-600">{error}</p> : <p className="mt-2 flex items-center gap-1 text-[11px] text-slate-500"><Loader2 size={12} className="animate-spin" />作っています</p>}
      </section>
    )
  }

  const toggle = (signal: string) => {
    const cur = signal in inputs ? inputs[signal] : Boolean(view.values[signal])
    const next = { ...inputs, [signal]: !cur }
    setInputs(next)
    setStep(null)
    void load({ targets: targets ?? undefined, inputs: next })
  }

  const rebuild = (nextTargets: string[]) => {
    setTargets(nextTargets)
    setSim(null)
    setStep(null)
    void load({ targets: nextTargets, inputs })
  }

  const run = async () => {
    setBusy(true)
    try {
      const events = rows.map((r) => ({ at: r.at, inputs: { [r.signal]: r.on } }))
      const result = await api.logicSimulate(projectIds, events, { targets: targets ?? undefined, inputs, until })
      setSim(result)
      setError(null)
      const last = result.timeline.length - 1
      setStep(last)
      const v = await api.logicDiagram(projectIds, { targets: targets ?? undefined, values: result.timeline[last].values })
      setView(v)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const showStep = (i: number) => {
    if (!sim) return
    setStep(i)
    void load({ targets: targets ?? undefined, values: sim.timeline[i].values })
  }

  const update = (i: number, patch: Partial<Row>) => setRows(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)))
  const waveSignals = [...view.outputs, ...view.blocks.filter((b) => b.letter && !b.output).map((b) => b.signal)]
  const currentValues = step != null && sim ? sim.timeline[step].values : view.values

  return (
    <div className="space-y-4">
      <section className="card p-4" aria-label="論理図">
        <div className="flex items-baseline gap-2">
          <div className="text-sm font-black">論理図</div>
          <span className="text-[11px] text-slate-500">出力 {view.outputs.length} · 入力 {inputSignals.length}</span>
          {busy && <Loader2 size={12} className="animate-spin text-slate-400" />}
        </div>
        <p className="mb-2 mt-1 text-[11px] leading-5 text-slate-500" title="直列は AND（縦棒と小さい四角）、並列は OR（縦棒と小さい丸）、b 接点は ⊠（NOT）。何か所でも使う信号は丸に英字">
          出力のコイルから接点の元へ辿った図。ピンクの線が成立しています。
        </p>
        <div className="max-h-[420px] overflow-auto rounded-lg border border-slate-200 bg-white [&_svg]:h-auto [&_svg]:w-full"
          // 図はサーバが作る SVG（器具番号・名前は HTML に逃がしてある）
          dangerouslySetInnerHTML={{ __html: view.svg }} data-testid="logic-svg" />
        <div className="mt-2 flex flex-wrap gap-2">
          <button type="button" className="btn btn-sm" onClick={() => setLarge(true)}><Maximize2 size={12} /> 大きく表示</button>
          <button type="button" className="btn btn-sm" onClick={() => void api.downloadLogicSvg(projectIds, { targets: targets ?? undefined, values: currentValues })}>
            <Download size={12} /> SVG 出力
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setShowTargets((v) => !v)}>
            <RefreshCw size={12} /> 出力を選ぶ
          </button>
        </div>
        {showTargets && (
          <div className="mt-2 rounded-lg border border-slate-200 p-2" aria-label="出力のコイル">
            <div className="mb-1 text-[11px] text-slate-500">論理図にする出力のコイル（既定は接点が M/C・他盤へ出ていくリレー）</div>
            <div className="grid max-h-40 grid-cols-2 gap-1 overflow-auto text-[11px]">
              {view.candidates.map((c) => {
                const checked = (targets ?? []).includes(c.device)
                return (
                  <label key={c.device} className="flex items-center gap-1">
                    <input type="checkbox" checked={checked}
                      onChange={() => setTargets(checked ? (targets ?? []).filter((t) => t !== c.device) : [...(targets ?? []), c.device])} />
                    <span className="truncate">{c.device}</span>
                  </label>
                )
              })}
            </div>
            <div className="mt-2 flex gap-2">
              <button type="button" className="btn btn-sm" disabled={!targets?.length} onClick={() => rebuild(targets ?? [])}>作り直す</button>
              <button type="button" className="btn btn-sm" onClick={() => rebuild(view.default_targets)}>既定に戻す</button>
            </div>
          </div>
        )}
        {error && <p className="mt-2 text-[11px] text-rose-600">{error}</p>}
        {view.diagnostics.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-[11px] text-amber-700" aria-label="確認が必要な箇所">
            {view.diagnostics.map((d, i) => <li key={i} className="flex gap-1"><AlertTriangle size={11} className="mt-0.5 flex-none" />{d.message}</li>)}
          </ul>
        )}
        <details className="mt-2 text-[11px]">
          <summary className="cursor-pointer text-slate-500">式で見る</summary>
          <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2 font-mono text-[10px] leading-4">{view.formulas}</pre>
        </details>
      </section>

      <section className="card p-4" aria-label="論理図の入力">
        <div className="mb-1 text-sm font-black">入力</div>
        <p className="mb-2 text-[11px] leading-5 text-slate-500">押すと ON / OFF が切り替わり、図の線の色が変わります。</p>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {inputSignals.map((i) => {
            const on = Boolean(currentValues[i.signal])
            return (
              <button key={i.signal} type="button" aria-pressed={on} disabled={busy}
                className={`flex min-h-9 items-center gap-2 rounded-xl border px-2 py-1 text-left text-[11px] ${on ? 'border-rose-300 bg-rose-50' : 'border-slate-200 bg-white'}`}
                onClick={() => toggle(i.signal)} title={`${i.devices.join(' / ')}（${i.source_label || '外部入力'}）`}>
                <span className={`h-2 w-2 flex-none rounded-full ${on ? 'bg-rose-500' : 'bg-slate-300'}`} />
                <span className="min-w-0 flex-1 truncate font-bold text-slate-700">{i.name}</span>
                <span className="font-mono text-[10px] text-slate-500">{onOff(on)}</span>
              </button>
            )
          })}
        </div>
      </section>

      <section className="card p-4" aria-label="論理図のシミュレーション">
        <div className="mb-1 text-sm font-black">時間の流れ</div>
        <p className="mb-2 text-[11px] leading-5 text-slate-500">時刻・入力・ON / OFF を並べて実行。タイマーとキープリレーを含めて解き、回路の計算とも照合します。</p>
        <ul className="space-y-1 text-[11px]" aria-label="操作">
          {rows.map((r, i) => (
            <li key={i} className="flex items-center gap-1">
              <input aria-label="時刻（秒）" type="number" min={0} step={0.5} value={r.at}
                className="w-14 rounded-lg border border-slate-200 px-1 py-0.5" onChange={(e) => update(i, { at: Number(e.target.value) })} />
              <span>秒</span>
              <select aria-label="入力" value={r.signal} className="min-w-0 flex-1 rounded-lg border border-slate-200 px-1 py-0.5"
                onChange={(e) => update(i, { signal: e.target.value })}>
                {inputSignals.map((s) => <option key={s.signal} value={s.signal}>{s.name}</option>)}
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
          <button type="button" className="btn btn-sm" disabled={!inputSignals.length}
            onClick={() => setRows([...rows, { at: (rows.at(-1)?.at ?? 0) + 1, signal: inputSignals[0].signal, on: true }])}>
            <Plus size={11} /> 行を追加
          </button>
          <label className="flex items-center gap-1">終了
            <input aria-label="終了（秒）" type="number" min={0} value={until} className="w-14 rounded-lg border border-slate-200 px-1 py-0.5"
              onChange={(e) => setUntil(Number(e.target.value))} />秒
          </label>
          <button type="button" className="btn btn-sm btn-primary" onClick={() => void run()} disabled={busy}>
            <Play size={11} /> 実行
          </button>
        </div>
        {sim && (
          <div className="mt-3 space-y-2">
            {sim.compare && (
              <p className={`flex items-center gap-1 text-[11px] ${sim.compare.agree ? 'text-emerald-700' : 'text-amber-700'}`}>
                {sim.compare.agree ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
                {sim.compare.agree
                  ? `回路の計算と一致（${sim.compare.checked} 件を照合）`
                  : `回路の計算と ${sim.compare.mismatches.length} 件ちがいます（例: ${sim.compare.mismatches[0]?.t} 秒の ${sim.compare.mismatches[0]?.device}）`}
              </p>
            )}
            <Waves sim={sim} signals={waveSignals} names={nameOf} selected={step} />
            <ol className="space-y-1 text-[11px]" aria-label="記録">
              {sim.timeline.map((snap, i) => (
                <li key={i}>
                  <button type="button" onClick={() => showStep(i)}
                    className={`w-full rounded-lg border px-2 py-1 text-left ${step === i ? 'border-sky-400 bg-sky-50' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
                    <span className="font-mono font-bold">{snap.t} 秒</span> <span>{snap.label}</span>
                    {snap.changes.length > 0 && (
                      <span className="block text-[10px] text-slate-500">
                        {snap.changes.filter((c) => !inputSignals.some((s) => s.signal === c.signal)).map((c) => `${nameOf[c.signal] ?? c.name}: ${onOff(c.before ?? false)} → ${onOff(c.after)}`).join('、')}
                      </span>
                    )}
                    {snap.warnings.map((w, k) => <span key={k} className="block text-[10px] text-amber-700">{w}</span>)}
                  </button>
                </li>
              ))}
            </ol>
          </div>
        )}
      </section>

      {large && createPortal(
        <div className="fixed inset-0 z-50 flex flex-col bg-white/95 p-4" role="dialog" aria-label="論理図（大きく表示）">
          <div className="mb-2 flex items-center gap-2">
            <div className="text-sm font-black">論理図</div>
            {step != null && sim && <span className="text-[11px] text-slate-500">{sim.timeline[step].t} 秒の状態</span>}
            <button type="button" className="btn btn-sm ml-auto" onClick={() => setLarge(false)} aria-label="閉じる"><X size={14} /></button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-200 bg-white" dangerouslySetInnerHTML={{ __html: view.svg }} />
        </div>,
        document.body,
      )}
    </div>
  )
}
