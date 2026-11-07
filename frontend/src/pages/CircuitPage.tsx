import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Download, Loader2, RotateCcw, Zap } from 'lucide-react'

import { api } from '../api/client'
import type { CircuitResult, CircuitSummary, ProjectRow } from '../types'

const ENGINE_LABEL = { ngspice: 'SPICE（ngspice）で解いています', builtin: '内蔵の回路計算で解いています（ngspice 未導入）' }

function sheetName(row: ProjectRow) {
  return row.sheet_no || row.name
}

/** 該当の記号を選んだ状態でエディタを開くリンク（直したら回路を組み直す） */
function editorLink(projectId: number, symbolRef: string) {
  return `/projects/${projectId}?symbol=${encodeURIComponent(symbolRef)}`
}

export default function CircuitPage() {
  const [rows, setRows] = useState<ProjectRow[]>([])
  const [ids, setIds] = useState<Set<number>>(new Set())
  const [summary, setSummary] = useState<CircuitSummary | null>(null)
  const [result, setResult] = useState<CircuitResult | null>(null)
  const [inputs, setInputs] = useState<Record<string, boolean>>({})
  const [page, setPage] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listProjects().then(setRows).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  const selected = useMemo(() => rows.filter((r) => ids.has(r.id)).map((r) => r.id), [rows, ids])

  const run = async (next: Record<string, boolean>, energized: Record<string, boolean>, projectIds = selected) => {
    setBusy(true)
    try {
      const r = await api.simulateCircuit(projectIds, next, energized)
      setResult(r)
      setInputs(next)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const build = async () => {
    setBusy(true)
    try {
      const s = await api.buildCircuit(selected)
      setSummary(s)
      setPage(s.pages[0]?.id ?? null)
      setError(null)
      await run({}, {}, selected)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  // 前の励磁状態から解くので、押しボタンを離しても自己保持の回路は保持される。
  const energizedNow = () =>
    Object.fromEntries(Object.entries(result?.coil_states ?? {}).map(([dev, s]) => [dev, s.energized]))
  const toggle = (device: string) => void run({ ...inputs, [device]: !inputs[device] }, energizedNow())
  const reset = () => void run({}, {})

  const overlay = result?.overlay.find((o) => o.project_id === page)
  const row = rows.find((r) => r.id === page)

  return (
    <div className="page-shell enter-up">
      <header className="page-header">
        <div>
          <div className="eyebrow">Circuit simulation</div>
          <h1 className="page-title">回路 <span className="title-accent">シミュレーション</span></h1>
          <p className="page-description">
            図面のアノテーション（記号・端子・配線）から回路を組み、接点の On/Off で電気がどこまで通り、どのリレーが動くかを確かめます。
          </p>
        </div>
      </header>

      {error && <div className="notice mb-5 border-rose-200 bg-rose-50 text-rose-700" role="alert">{error}</div>}

      <section className="card mb-5 p-4 sm:p-5" aria-label="図面を選ぶ">
        <div className="mb-2 text-[11px] font-bold text-slate-500">
          図面を選ぶ（複数選ぶと、母線と同じ器具番号のリレーを図面をまたいでつなぎます）
        </div>
        <div className="thin-scroll mb-3 flex max-h-40 flex-wrap gap-2 overflow-auto">
          {rows.map((r) => (
            <label key={r.id} className="flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-1.5 text-xs">
              <input
                type="checkbox"
                className="h-4 w-4"
                checked={ids.has(r.id)}
                onChange={() =>
                  setIds((prev) => {
                    const n = new Set(prev)
                    if (n.has(r.id)) n.delete(r.id)
                    else n.add(r.id)
                    return n
                  })
                }
              />
              <span className="font-bold text-slate-700">{sheetName(r)}</span>
              <span className="font-mono text-slate-400">{r.connection_count} 配線</span>
            </label>
          ))}
        </div>
        <button type="button" className="btn btn-accent" onClick={() => void build()} disabled={busy || selected.length === 0}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
          回路を組んでシミュレーションする
        </button>
      </section>

      {summary && (
        <div className="grid gap-5 xl:grid-cols-[22rem_minmax(0,1fr)]">
          <div className="space-y-5">
            <section className="card p-4" aria-label="外部入力">
              <div className="mb-1 text-sm font-black">外部入力</div>
              <p className="mb-2 text-[11px] leading-5 text-slate-500">
                選んだ図面にコイルが無い接点です。On にすると a 接点は閉じ、b 接点は開きます。
              </p>
              {summary.inputs.length === 0 && <p className="text-[11px] text-slate-400">ありません</p>}
              <div className="space-y-1.5">
                {summary.inputs.map((input) => (
                  <button
                    key={input.device}
                    type="button"
                    aria-pressed={Boolean(inputs[input.device])}
                    className={`flex w-full items-center gap-2 rounded-xl border px-3 py-1.5 text-left text-xs ${
                      inputs[input.device] ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'
                    }`}
                    onClick={() => toggle(input.device)}
                    disabled={busy}
                  >
                    <span className={`h-2.5 w-2.5 rounded-full ${inputs[input.device] ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                    <span className="flex-1 font-bold text-slate-700">{input.device}</span>
                    <span className="text-[10px] text-slate-400">接点 {input.contacts.length}</span>
                    <span className="font-mono text-[10px] font-bold">{inputs[input.device] ? 'ON' : 'OFF'}</span>
                  </button>
                ))}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" className="btn btn-sm" onClick={reset} disabled={busy}>
                  <RotateCcw size={12} /> すべて OFF に戻す
                </button>
                <button type="button" className="btn btn-sm" onClick={() => void api.downloadNetlist(selected, inputs)}>
                  <Download size={12} /> ネットリスト（.cir）
                </button>
              </div>
            </section>

            <section className="card p-4" aria-label="リレー">
              <div className="mb-2 text-sm font-black">リレー（{summary.coils.length}）</div>
              <div className="space-y-1">
                {summary.coils.map((coil) => {
                  const state = result?.coil_states[coil.device]
                  return (
                    <div key={coil.device} className="flex items-center gap-2 text-xs">
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        state?.energized ? 'bg-rose-100 text-rose-700' : 'bg-slate-100 text-slate-500'
                      }`}>
                        {state?.energized ? '励磁' : '無励磁'}
                      </span>
                      <span className="flex-1 font-bold text-slate-700">{coil.device}</span>
                      <span className="font-mono text-[10px] text-slate-400">{state ? `${state.voltage} V` : ''}</span>
                    </div>
                  )
                })}
              </div>
              {result && (
                <p className="mt-2 text-[10px] text-slate-400">
                  {ENGINE_LABEL[result.engine]}。{result.steps} 回で状態が落ち着きました。
                </p>
              )}
              {result?.warnings.map((w) => (
                <p key={w} className="mt-2 flex gap-1.5 text-[11px] font-bold text-rose-700"><AlertTriangle size={13} /> {w}</p>
              ))}
            </section>

            {summary.diagnostics.length > 0 && (
              <section className="card p-4" aria-label="回路にできなかった箇所">
                <div className="mb-1 text-sm font-black">確認が必要な箇所（{summary.diagnostics.length}）</div>
                <p className="mb-2 text-[11px] leading-5 text-slate-500">
                  アノテーションを直して「回路を組んでシミュレーションする」を押し直すと反映されます。
                </p>
                <ul className="thin-scroll max-h-64 space-y-1 overflow-auto text-[11px] text-slate-600">
                  {summary.diagnostics.map((d, i) => (
                    <li key={i} className="rounded-lg bg-amber-50 px-2 py-1">
                      {d.page && <b>{d.page} </b>}
                      {d.symbol_ref && <span className="font-mono text-slate-400">{d.symbol_ref} </span>}
                      {d.message}
                      {d.project_id != null && d.symbol_ref && (
                        <Link className="ml-1 font-bold text-cyan-700 underline" to={editorLink(d.project_id, d.symbol_ref)}>
                          エディタで直す
                        </Link>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>

          <section className="card overflow-hidden" aria-label="図面上の結果">
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 p-3">
              {summary.pages.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`btn btn-sm ${page === p.id ? 'btn-accent' : ''}`}
                  onClick={() => setPage(p.id)}
                >
                  {p.name}
                </button>
              ))}
              <span className="flex-1" />
              <span className="flex items-center gap-3 text-[10px] text-slate-500">
                <span><span className="mr-1 inline-block h-0.5 w-4 bg-rose-500 align-middle" />電圧あり</span>
                <span><span className="mr-1 inline-block h-2.5 w-2.5 border-2 border-emerald-500 align-middle" />閉じた接点</span>
                <span><span className="mr-1 inline-block h-2.5 w-2.5 bg-rose-400/60 align-middle" />励磁したコイル</span>
              </span>
            </div>
            {row && overlay && (
              <div className="relative bg-white" style={{ aspectRatio: `${row.image_width} / ${row.image_height}` }}>
                <img src={api.imageUrl(row.id)} alt={sheetName(row)} className="absolute inset-0 h-full w-full" />
                <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none" aria-label="シミュレーション結果">
                  {overlay.wires.map((w, i) => (
                    <line
                      key={i}
                      x1={w.x1} y1={w.y1} x2={w.x2} y2={w.y2}
                      stroke={w.live ? '#e11d48' : '#94a3b8'}
                      strokeOpacity={w.live ? 0.9 : 0.5}
                      strokeWidth={w.live ? 3 : 1.5}
                      vectorEffect="non-scaling-stroke"
                    />
                  ))}
                  {overlay.coils.map((c) => (
                    <rect
                      key={c.symbol_ref}
                      x={c.cx - c.w / 2} y={c.cy - c.h / 2} width={c.w} height={c.h}
                      fill={c.energized ? '#fb7185' : 'none'} fillOpacity={0.45}
                      stroke={c.energized ? '#e11d48' : '#94a3b8'} strokeWidth={2} vectorEffect="non-scaling-stroke"
                    />
                  ))}
                  {overlay.contacts.map((c) => (
                    <rect
                      key={c.symbol_ref}
                      x={c.cx - c.w / 2} y={c.cy - c.h / 2} width={c.w} height={c.h}
                      fill="none"
                      stroke={c.closed ? '#10b981' : '#94a3b8'}
                      strokeDasharray={c.closed ? undefined : '4 3'}
                      strokeWidth={2}
                      vectorEffect="non-scaling-stroke"
                    >
                      <title>{`${c.device}（${c.kind} 接点）: ${c.closed ? '閉' : '開'}`}</title>
                    </rect>
                  ))}
                </svg>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
