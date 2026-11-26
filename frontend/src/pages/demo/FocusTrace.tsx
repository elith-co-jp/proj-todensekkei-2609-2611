import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Crosshair } from 'lucide-react'

import { api } from '../../api/client'
import type { DemoLocation, DemoPage, LogicSignal } from '../../types'
import { tourSteps, traceFrom } from './trace'
import type { LogicDeps, TraceDirection, TraceNode } from './trace'

export type TraceMode = 'path' | 'tour' | 'table'

const MODES: { key: TraceMode; label: string; caption: string }[] = [
  { key: 'path', label: '経路', caption: '注目点につながる信号を木の形で示し、論理図ではその経路だけを濃く表示します' },
  { key: 'tour', label: '順に辿る', caption: '図面のコイル・接点を 1 か所ずつ拡大して見ていきます' },
  { key: 'table', label: '一覧', caption: '関係する箇所を図面の切り抜きと並べて一覧にします' },
]
const DIRECTIONS: { key: TraceDirection; label: string }[] = [
  { key: 'up', label: '原因へ（上流）' },
  { key: 'down', label: '影響へ（下流）' },
  { key: 'both', label: '両方' },
]
const DEPTHS = [1, 2, 99]
const ROLE = { coil: 'コイル', contact: '接点' } as const

/** 図面の記号のまわりを切り抜いて見せる（CSS の背景で、図面の画像をそのまま使う） */
export function Crop({ location, page, width = 168, height = 96 }: { location: DemoLocation; page: DemoPage | undefined; width?: number; height?: number }) {
  if (!page) return null
  const W = page.width
  const H = page.height
  const regionW = Math.max(location.w * W * 8, 240)
  const scale = width / regionW
  const regionH = height / scale
  return (
    <div
      role="img"
      aria-label={`${location.page ?? ''} ${location.device} の図面`}
      className="relative flex-none overflow-hidden rounded border border-slate-200 bg-white"
      style={{
        width,
        height,
        backgroundImage: `url(${api.imageUrl(location.project_id)})`,
        backgroundSize: `${W * scale}px ${H * scale}px`,
        backgroundPosition: `${-(location.cx * W - regionW / 2) * scale}px ${-(location.cy * H - regionH / 2) * scale}px`,
        backgroundRepeat: 'no-repeat',
      }}
    >
      <span
        className={`absolute border-2 ${location.role === 'coil' ? 'border-violet-500' : 'border-amber-500'}`}
        style={{
          left: width / 2 - (location.w * W * scale) / 2 - 2,
          top: height / 2 - (location.h * H * scale) / 2 - 2,
          width: location.w * W * scale + 4,
          height: location.h * H * scale + 4,
        }}
      />
    </div>
  )
}

/**
 * 論理図で選んだ注目点から、原因（上流）・影響（下流）へ辿り、図面の上でどう書かれているかを確かめる（DD-0048）。
 * 見方は人によって違うので、経路（木）・順に辿る・一覧（切り抜きつき）を切り替えられる。
 */
export default function FocusTrace({
  focus,
  signals,
  deps,
  locations,
  values,
  pages,
  onJump,
  onChoose,
  onPath,
}: {
  focus: string
  signals: LogicSignal[]
  deps: LogicDeps
  locations: Record<string, DemoLocation[]>
  values: Record<string, boolean>
  pages: DemoPage[]
  onJump: (location: DemoLocation) => void
  onChoose: (signal: string) => void
  /** 経路に入る信号と、見方。論理図と図面の印に使う */
  onPath: (path: TraceNode[], mode: TraceMode, step: DemoLocation | null) => void
}) {
  const [mode, setMode] = useState<TraceMode>('path')
  const [direction, setDirection] = useState<TraceDirection>('up')
  const [depth, setDepth] = useState(99)
  const [step, setStep] = useState(0)
  const byId = useMemo(() => new Map(signals.map((s) => [s.id, s])), [signals])
  const label = (sig: string) => {
    const s = byId.get(sig)
    if (!s) return sig
    return `${s.letter ? `${s.letter}: ` : ''}${s.ref_name || s.name}`
  }
  const nodes = useMemo(() => traceFrom(deps, focus, direction, depth), [deps, focus, direction, depth])
  const steps = useMemo(() => tourSteps(nodes, locations, label, direction),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nodes, locations, byId, direction])
  const current = steps[Math.min(step, steps.length - 1)]

  useEffect(() => setStep(0), [focus, direction, depth])
  useEffect(() => {
    onPath(nodes, mode, mode === 'tour' && current ? current.location : null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, mode, current?.location])
  useEffect(() => {
    if (mode === 'tour' && current) onJump(current.location)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, step, focus, direction, depth])

  const pageOf = (id: number) => pages.find((p) => p.id === id)
  const state = (sig: string) => (
    <span className={`ml-1 rounded px-1 font-mono text-[10px] font-bold ${values[sig] ? 'bg-pink-100 text-pink-700' : 'bg-slate-100 text-slate-500'}`}>
      {values[sig] ? 'ON' : 'OFF'}
    </span>
  )

  return (
    <div aria-label="注目点から辿る" className="space-y-2">
      <div className="flex flex-wrap items-center gap-1 text-xs font-black text-slate-800">
        <Crosshair size={12} /> 注目点 <span className="text-violet-700">{label(focus)}</span>{state(focus)}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <div role="tablist" aria-label="見方" className="flex rounded-lg bg-slate-100 p-0.5">
          {MODES.map((m) => (
            <button key={m.key} type="button" role="tab" aria-selected={mode === m.key} title={m.caption}
              className={`rounded-md px-2 py-0.5 text-[11px] font-bold ${mode === m.key ? 'bg-white text-violet-700 shadow-sm' : 'text-slate-500'}`}
              onClick={() => setMode(m.key)}>{m.label}</button>
          ))}
        </div>
        <select aria-label="辿る向き" className="rounded-md border border-slate-200 px-1 py-0.5 text-[11px]" value={direction}
          onChange={(e) => setDirection(e.target.value as TraceDirection)}>
          {DIRECTIONS.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
        </select>
        <select aria-label="段数" className="rounded-md border border-slate-200 px-1 py-0.5 text-[11px]" value={depth}
          onChange={(e) => setDepth(Number(e.target.value))}>
          {DEPTHS.map((d) => <option key={d} value={d}>{d === 99 ? 'すべての段' : `${d} 段まで`}</option>)}
        </select>
      </div>

      {mode === 'path' && (
        <ul className="space-y-0.5" aria-label="経路">
          {nodes.map((n) => (
            <li key={`${n.side}-${n.signal}`} className="group flex items-center" style={{ paddingLeft: n.depth * 14 }}>
              <button type="button" className="min-w-0 flex-1 rounded-md px-1.5 py-0.5 text-left text-[11px] hover:bg-violet-50"
                onClick={() => {
                  const locs = locations[n.signal] ?? []
                  const first = locs.find((l) => l.role === 'coil') ?? locs[0]
                  if (first) onJump(first)
                }}
                title={n.parent ? `${label(n.parent)} ${n.side === 'up' ? 'を決める' : 'で決まる'}` : '注目点'}>
                <span className="mr-1 text-slate-400">{n.side === 'up' ? '←' : n.side === 'down' ? '→' : '●'}</span>
                {n.negated && <span className="mr-0.5 font-bold text-rose-600" title="反転して効く（b 接点・NOT）">¬</span>}
                {n.port && <span className="mr-0.5 font-mono text-[10px] text-slate-500">[{n.port}]</span>}
                <span className="font-bold text-slate-700">{label(n.signal)}</span>
                {state(n.signal)}
                <span className="ml-1 text-[10px] text-slate-400">図面 {(locations[n.signal] ?? []).length} か所</span>
              </button>
              {n.side !== 'self' && (
                <button type="button" className="ml-1 hidden rounded px-1 text-[10px] text-violet-600 hover:bg-violet-100 group-hover:block"
                  onClick={() => onChoose(n.signal)} aria-label={`${label(n.signal)} を注目点にする`}>注目点に</button>
              )}
            </li>
          ))}
        </ul>
      )}

      {mode === 'tour' && (
        steps.length === 0 ? <p className="text-[11px] text-slate-500">選んだ図面の中に、たどれる箇所がありません。</p> : (
          <div className="space-y-2" aria-label="順に辿る">
            <div className="flex items-center gap-1">
              <button type="button" className="btn btn-sm" aria-label="前へ" disabled={step === 0} onClick={() => setStep((s) => Math.max(0, s - 1))}><ChevronLeft size={12} /></button>
              <span className="font-mono text-[11px] text-slate-600">{Math.min(step, steps.length - 1) + 1} / {steps.length}</span>
              <button type="button" className="btn btn-sm" aria-label="次へ" disabled={step >= steps.length - 1} onClick={() => setStep((s) => Math.min(steps.length - 1, s + 1))}><ChevronRight size={12} /></button>
            </div>
            {current && (
              <div className="flex gap-2 rounded-lg border border-violet-200 bg-violet-50/50 p-2">
                <Crop location={current.location} page={pageOf(current.location.project_id)} />
                <div className="min-w-0 text-[11px] leading-5 text-slate-700">
                  <div className="font-bold">{current.location.page} · {current.location.device} の{ROLE[current.location.role]}{current.location.contact ? `（${current.location.contact} 接点）` : ''}</div>
                  <div>{current.why}</div>
                  <div className="text-slate-500">注目点から {current.node.depth} 段{current.node.side === 'up' ? '上流' : current.node.side === 'down' ? '下流' : ''}{state(current.node.signal)}</div>
                </div>
              </div>
            )}
          </div>
        )
      )}

      {mode === 'table' && (
        <table className="w-full text-[11px]" aria-label="関係する箇所の一覧">
          <thead>
            <tr className="text-left text-[10px] text-slate-500">
              <th className="py-1 font-bold">図面</th><th className="font-bold">信号・箇所</th><th className="font-bold">関係</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((s, i) => (
              <tr key={i} className="cursor-pointer border-t border-slate-100 align-top hover:bg-violet-50" onClick={() => onJump(s.location)}>
                <td className="py-1 pr-2"><Crop location={s.location} page={pageOf(s.location.project_id)} width={132} height={76} /></td>
                <td className="py-1 pr-2">
                  <div className="font-bold text-slate-700">{label(s.node.signal)}{state(s.node.signal)}</div>
                  <div className="text-slate-500">{s.location.page} · {s.location.device} の{ROLE[s.location.role]}{s.location.contact ? `（${s.location.contact}）` : ''}</div>
                </td>
                <td className="py-1 text-slate-600">{s.node.side === 'self' ? '注目点' : `${s.node.depth} 段${s.node.side === 'up' ? '上流' : '下流'}`}{s.node.negated ? '・反転' : ''}</td>
              </tr>
            ))}
            {steps.length === 0 && <tr><td colSpan={3} className="py-2 text-slate-500">選んだ図面の中に、関係する箇所がありません。</td></tr>}
          </tbody>
        </table>
      )}
    </div>
  )
}
