import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeftRight, Download, FileSpreadsheet, Loader2, Search } from 'lucide-react'

import { api } from '../../api/client'
import type { TraceBackward, TraceExplain, TraceForward, TraceStatus, TraceTargets } from '../../types'

const STATUS_TONE: Record<TraceStatus, string> = {
  成立: 'bg-emerald-100 text-emerald-700',
  不成立: 'bg-rose-100 text-rose-700',
  判定不能: 'bg-amber-100 text-amber-700',
}

type Tri = 'on' | 'off' | 'unknown'
const TRI_LABEL: Record<Tri, string> = { on: '動作', off: '非動作', unknown: '不明' }

function editorLink(projectId: number, symbolRef: string) {
  return `/projects/${projectId}?symbol=${encodeURIComponent(symbolRef)}`
}

function Status({ value }: { value: TraceStatus }) {
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-black ${STATUS_TONE[value]}`}>{value}</span>
}

function ExplainTree({ node, depth = 0 }: { node: TraceExplain; depth?: number }) {
  return (
    <li className="mt-1">
      <span className="font-bold">{node.target}</span>
      {node.text && <span className="ml-1 font-mono text-slate-600">= {node.text}</span>}
      {node.note && <span className="ml-1 text-slate-500">（{node.note}）</span>}
      {(node.inputs?.length ?? 0) > 0 && (
        <ul className="ml-4 list-disc text-slate-600">
          {node.inputs!.map((i) => (
            <li key={i.device}>
              {i.device}: <span className="font-bold">{i.source_label}</span>
              {i.reason && <span className="text-slate-500">（{i.reason}）</span>}
            </li>
          ))}
        </ul>
      )}
      {(node.relays?.length ?? 0) > 0 && depth < 8 && (
        <ul className="ml-4 border-l border-slate-200 pl-2">
          {node.relays!.map((r) => <ExplainTree key={r.target} node={r} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  )
}

type Props = { projectIds: number[] }

/** 展開接続図を読む手順: 逆引き（このコイル・出力が動く条件は？）と順引き（この器具が変わったら何に影響する？）。DD-0035 */
export default function TracePanel({ projectIds }: Props) {
  const [targets, setTargets] = useState<TraceTargets | null>(null)
  const [mode, setMode] = useState<'backward' | 'forward'>('backward')
  const [target, setTarget] = useState('')
  const [device, setDevice] = useState('')
  const [states, setStates] = useState<Record<string, Tri>>({})
  const [backward, setBackward] = useState<TraceBackward | null>(null)
  const [forward, setForward] = useState<TraceForward | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (projectIds.length === 0) return
    api.traceTargets(projectIds).then(setTargets).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [projectIds])

  const stateValues = () =>
    Object.fromEntries(Object.entries(states).map(([k, v]) => [k, v === 'unknown' ? null : v === 'on']))

  const run = async (work: () => Promise<void>) => {
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

  const traceBack = () => run(async () => setBackward(await api.traceBackward(projectIds, target, stateValues())))
  const traceForward = () => run(async () => setForward(await api.traceForward(projectIds, device, stateValues())))
  const inputDevices = (targets?.devices ?? []).filter((d) => d.source !== 'relay')

  return (
    <section className="card mt-5 p-4 sm:p-5" aria-label="図面を読む手順">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-black">図面を読む手順（逆引き・順引き）</div>
        <span className="flex-1" />
        {busy && <Loader2 size={14} className="animate-spin text-slate-400" />}
        <button type="button" className="btn btn-sm" onClick={() => void run(async () => void (await api.downloadAnnotationTable(projectIds)))}
          disabled={busy || projectIds.length === 0}>
          <FileSpreadsheet size={12} /> 対応表（Excel）
        </button>
        <button type="button" className="btn btn-sm" onClick={() => void run(async () => void (await api.downloadCvat(projectIds)))}
          disabled={busy || projectIds.length === 0}>
          <Download size={12} /> CVAT
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-5 text-slate-500">
        電源から戻りまでの経路を全部たどり、直列は AND・並列は OR で成立条件を出します。コイルより下（戻り側）の接点も含めます。
        入力の状態を「不明」にした接点や、参照先のシートが無い接点があると「判定不能」になり、理由と場所を示します。
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px]" role="radiogroup" aria-label="問い">
        <label className="flex items-center gap-1">
          <input type="radio" checked={mode === 'backward'} onChange={() => setMode('backward')} /> 起動条件を知る（逆引き）
        </label>
        <label className="flex items-center gap-1">
          <input type="radio" checked={mode === 'forward'} onChange={() => setMode('forward')} /> 影響を知る（順引き）
        </label>
      </div>
      {mode === 'backward' ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select className="input input-sm" aria-label="対象" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">対象のコイル・出力を選ぶ</option>
            {(targets?.loads ?? []).map((l) => <option key={l.name} value={l.name}>{l.name}</option>)}
          </select>
          <button type="button" className="btn btn-sm btn-accent" onClick={() => void traceBack()} disabled={busy || !target}>
            <Search size={12} /> 条件を求める
          </button>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select className="input input-sm" aria-label="起点" value={device} onChange={(e) => setDevice(e.target.value)}>
            <option value="">変化した器具を選ぶ</option>
            {(targets?.devices ?? []).map((d) => <option key={d.device} value={d.device}>{d.device}（{d.source_label}）</option>)}
          </select>
          <button type="button" className="btn btn-sm btn-accent" onClick={() => void traceForward()} disabled={busy || !device}>
            <ArrowLeftRight size={12} /> 影響を辿る
          </button>
        </div>
      )}
      {inputDevices.length > 0 && (
        <details className="mt-2 text-[11px]">
          <summary className="cursor-pointer font-bold text-slate-600">入力の状態（{inputDevices.length}）</summary>
          <div className="mt-1 grid gap-1 sm:grid-cols-2">
            {inputDevices.map((d) => (
              <label key={d.device} className="flex items-center gap-2">
                <span className="w-40 truncate" title={d.reason}>{d.device}</span>
                <select className="input input-sm" aria-label={`${d.device} の状態`} value={states[d.device] ?? 'unknown'}
                  onChange={(e) => setStates({ ...states, [d.device]: e.target.value as Tri })}>
                  {(Object.keys(TRI_LABEL) as Tri[]).map((k) => <option key={k} value={k}>{TRI_LABEL[k]}</option>)}
                </select>
                <span className="text-slate-400">{d.source_label}</span>
              </label>
            ))}
          </div>
        </details>
      )}
      {error && <p className="mt-2 text-[11px] font-bold text-rose-700" role="alert">{error}</p>}

      {mode === 'backward' && backward && (
        <div className="mt-3 text-[12px]" aria-label="逆引きの結果">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-black">{backward.target}</span>
            <Status value={backward.status} />
            <code className="rounded bg-slate-100 px-1.5 py-0.5">{backward.text}</code>
          </div>
          {backward.reasons.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-[11px] text-amber-700">
              {backward.reasons.map((r) => <li key={r}>{r}</li>)}
            </ul>
          )}
          <table className="mt-2 w-full text-[11px]">
            <thead><tr className="text-left text-slate-500"><th>枝</th><th>判定</th><th>接点（電源側 → 戻り側）</th></tr></thead>
            <tbody>
              {backward.branches.map((b, i) => (
                <tr key={i} className="border-t border-slate-100 align-top">
                  <td className="py-1 pr-2">{i + 1}</td>
                  <td className="py-1 pr-2"><Status value={b.status} /></td>
                  <td className="py-1">
                    {b.contacts.map((c) => (
                      <Link key={`${c.project_id}:${c.symbol_ref}`} to={editorLink(c.project_id, c.symbol_ref)}
                        className={`mr-1 inline-block rounded px-1 ${c.conducts === false ? 'bg-rose-50 text-rose-700' : c.conducts === null ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'}`}
                        title={`${c.page} ${c.symbol_ref}（${c.side}）`}>
                        {c.kind === 'b' ? '¬' : ''}{c.device}
                        {c.side === '戻り側' && <span className="text-slate-400">↓</span>}
                      </Link>
                    ))}
                    {b.contacts.length === 0 && <span className="text-slate-400">接点なし（電源に直結）</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] font-bold text-slate-600">接点を動かす元へ辿った結果</summary>
            <ul className="text-[11px]"><ExplainTree node={backward.explain} /></ul>
          </details>
        </div>
      )}

      {mode === 'forward' && forward && (
        <div className="mt-3 text-[12px]" aria-label="順引きの結果">
          <div className="font-black">{forward.device} が変わると影響する出力（{forward.affected.length}）</div>
          {forward.state_change?.skipped && <p className="text-[11px] text-slate-500">{forward.state_change.skipped}</p>}
          <table className="mt-2 w-full text-[11px]">
            <thead><tr className="text-left text-slate-500"><th>段</th><th>出力</th><th>経由</th><th>接点の位置づけ</th><th>状態の変化</th></tr></thead>
            <tbody>
              {forward.affected.map((a) => {
                const change = forward.state_change?.targets?.find((t) => t.target === a.target)
                return (
                  <tr key={a.target} className="border-t border-slate-100">
                    <td className="py-1 pr-2">{a.depth}</td>
                    <td className="py-1 pr-2 font-bold">{a.target}</td>
                    <td className="py-1 pr-2">{a.via}</td>
                    <td className="py-1 pr-2">{a.role}</td>
                    <td className="py-1">{change ? <><Status value={change.before} /> → <Status value={change.after} /></> : '-'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
