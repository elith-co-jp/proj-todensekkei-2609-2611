import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  ClipboardCheck,
  Clock,
  Download,
  GitBranch,
  ListChecks,
  Loader2,
  PencilRuler,
  Power,
  RotateCcw,
  Search,
  Zap,
} from 'lucide-react'

import { api } from '../api/client'
import type { CircuitResult, CircuitSummary, ProjectRow } from '../types'
import ConsistencyPanel from './circuit/ConsistencyPanel'
import EditPanel from './circuit/EditPanel'
import TimelinePanel from './circuit/TimelinePanel'
import WhatIfPanel from './circuit/WhatIfPanel'

const ENGINE_LABEL = { ngspice: 'SPICE（ngspice）で解いています', builtin: '内蔵の回路計算で解いています（ngspice 未導入）' }

function sheetName(row: ProjectRow) {
  return row.sheet_no || row.name
}

/** 該当の記号を選んだ状態でエディタを開くリンク（直したら回路を組み直す） */
function editorLink(projectId: number, symbolRef: string) {
  return `/projects/${projectId}?symbol=${encodeURIComponent(symbolRef)}`
}

/** 一覧などから `#/circuit?ids=1,2` で図面を選んだ状態で開く */
function idsFromQuery(value: string | null): number[] {
  if (!value) return []
  return value
    .split(',')
    .map((v) => Number(v))
    .filter((v) => Number.isInteger(v) && v > 0)
}

type ToolTab = {
  key: string
  label: string
  caption: string
  icon: typeof Zap
  badge?: number
  tone?: 'warn'
  render: () => ReactNode
}

const STEPS = ['図面を選ぶ', '回路を組む', '動かして確かめる'] as const

export default function CircuitPage() {
  const [params] = useSearchParams()
  const idsParam = params.get('ids')
  const [rows, setRows] = useState<ProjectRow[]>([])
  const [ids, setIds] = useState<Set<number>>(() => new Set(idsFromQuery(idsParam)))
  const [filter, setFilter] = useState('')
  const [choosing, setChoosing] = useState(true)
  const [summary, setSummary] = useState<CircuitSummary | null>(null)
  const [builtIds, setBuiltIds] = useState<number[]>([])
  const [result, setResult] = useState<CircuitResult | null>(null)
  const [inputs, setInputs] = useState<Record<string, boolean>>({})
  const [page, setPage] = useState<number | null>(null)
  const [tab, setTab] = useState('operate')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const handledParam = useRef<string | null>(null)

  useEffect(() => {
    api.listProjects().then(setRows).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  const selected = useMemo(() => rows.filter((r) => ids.has(r.id)).map((r) => r.id), [rows, ids])
  const visibleRows = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return rows
    return rows.filter((r) => `${r.id} ${r.name} ${r.sheet_no ?? ''}`.toLowerCase().includes(q))
  }, [rows, filter])
  // 組んだあとに選択を変えても、結果を見ている回路の図面は組んだときのまま
  const projectIds = summary ? builtIds : selected
  const selectionChanged = summary !== null && (builtIds.length !== selected.length || builtIds.some((id) => !ids.has(id)))

  const run = async (next: Record<string, boolean>, energized: Record<string, boolean>, target = projectIds) => {
    setBusy(true)
    try {
      const r = await api.simulateCircuit(target, next, energized)
      setResult(r)
      setInputs(next)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const build = async (target = selected) => {
    if (target.length === 0) return
    setBusy(true)
    try {
      const s = await api.buildCircuit(target)
      setSummary(s)
      setBuiltIds(target)
      setPage((current) => (s.pages.some((p) => p.id === current) ? current : s.pages[0]?.id ?? null))
      setChoosing(false)
      setError(null)
      await run({}, {}, target)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  // 一覧から図面を選んで来たときは、そのまま回路を組む
  useEffect(() => {
    if (!idsParam || rows.length === 0 || handledParam.current === idsParam) return
    handledParam.current = idsParam
    const wanted = idsFromQuery(idsParam)
    const target = rows.filter((r) => wanted.includes(r.id)).map((r) => r.id)
    if (target.length === 0) return
    setIds(new Set(target))
    void build(target)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, idsParam])

  // 前の励磁状態から解くので、押しボタンを離しても自己保持の回路は保持される。
  const energizedNow = () =>
    Object.fromEntries(Object.entries(result?.coil_states ?? {}).map(([dev, s]) => [dev, s.energized]))
  const toggle = (device: string) => void run({ ...inputs, [device]: !inputs[device] }, energizedNow())
  const reset = () => void run({}, {})

  const toggleId = (id: number) =>
    setIds((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const overlay = result?.overlay.find((o) => o.project_id === page)
  const row = rows.find((r) => r.id === page)
  const step = summary ? 2 : selected.length ? 1 : 0
  const energizedCount = summary ? summary.coils.filter((c) => result?.coil_states[c.device]?.energized).length : 0
  const onCount = Object.values(inputs).filter(Boolean).length
  const issueCount = summary?.diagnostics.length ?? 0
  const rebuild = () => void build(projectIds)

  const tabs: ToolTab[] = summary
    ? [
        {
          key: 'operate',
          label: '動かす',
          caption: '入力を ON / OFF して、どのリレーが動くかを図面で確かめます。',
          icon: Power,
          render: () => (
            <div className="space-y-4">
              <section className="card p-4" aria-label="外部入力">
                <div className="flex items-baseline gap-2">
                  <div className="text-sm font-black">外部入力</div>
                  <span className="text-[11px] text-slate-500">{summary.inputs.length} 個 · ON {onCount}</span>
                </div>
                <p className="mb-2 mt-1 text-[11px] leading-5 text-slate-500">
                  選んだ図面にコイルが無い接点です。押すと ON / OFF が切り替わり、a 接点は閉じ、b 接点は開きます。
                </p>
                {summary.inputs.length === 0 && <p className="text-[11px] text-slate-400">ありません</p>}
                <div className="grid gap-1.5 sm:grid-cols-2">
                  {summary.inputs.map((input) => {
                    const on = Boolean(inputs[input.device])
                    return (
                      <button
                        key={input.device}
                        type="button"
                        aria-pressed={on}
                        className={`flex min-h-10 w-full items-center gap-2 rounded-xl border px-3 py-1.5 text-left text-xs transition ${
                          on ? 'border-emerald-400 bg-emerald-50 ring-1 ring-emerald-200' : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                        }`}
                        onClick={() => toggle(input.device)}
                        disabled={busy}
                        title={`接点 ${input.contacts.length} 個`}
                      >
                        <span className={`h-2.5 w-2.5 flex-none rounded-full ${on ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                        <span className="min-w-0 flex-1 truncate font-bold text-slate-700">{input.device}</span>
                        <span className={`rounded px-1.5 font-mono text-[10px] font-bold ${on ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-500'}`}>
                          {on ? 'ON' : 'OFF'}
                        </span>
                      </button>
                    )
                  })}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className="btn btn-sm" onClick={reset} disabled={busy}>
                    <RotateCcw size={12} /> すべて OFF に戻す
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => void api.downloadNetlist(projectIds, inputs)}>
                    <Download size={12} /> ネットリスト（.cir）
                  </button>
                </div>
              </section>

              <section className="card p-4" aria-label="リレー">
                <div className="flex items-baseline gap-2">
                  <div className="text-sm font-black">リレー</div>
                  <span className="text-[11px] text-slate-500">{summary.coils.length} 個 · 励磁 {energizedCount}</span>
                </div>
                <div className="mt-2 grid gap-1 sm:grid-cols-2">
                  {summary.coils.map((coil) => {
                    const state = result?.coil_states[coil.device]
                    return (
                      <div key={coil.device} className={`flex items-center gap-2 rounded-lg px-2 py-1 text-xs ${state?.energized ? 'bg-rose-50' : ''}`}>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                          state?.energized ? 'bg-rose-500 text-white' : 'bg-slate-100 text-slate-500'
                        }`}>
                          {state?.energized ? '励磁' : '無励磁'}
                        </span>
                        <span className="min-w-0 flex-1 truncate font-bold text-slate-700">{coil.device}</span>
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
              </section>
            </div>
          ),
        },
        {
          key: 'whatif',
          label: 'もしも・波及',
          caption: '機器を動かした・止めたときに何が変わるか、影響がどこまで及ぶかを調べます。',
          icon: GitBranch,
          render: () => <WhatIfPanel projectIds={projectIds} summary={summary} inputs={inputs} energized={energizedNow()} />,
        },
        {
          key: 'timeline',
          label: '時間の流れ',
          caption: '入力を何秒に ON / OFF するかを並べ、タイマーを含めて時間の順に解きます。',
          icon: Clock,
          render: () => <TimelinePanel projectIds={projectIds} summary={summary} />,
        },
        {
          key: 'check',
          label: '点検',
          caption: '回路にできなかった箇所、図面間のつながり、解析結果どうしの矛盾を確かめます。',
          icon: ClipboardCheck,
          badge: issueCount,
          tone: 'warn',
          render: () => (
            <div className="space-y-4">
              <section className="card p-4" aria-label="回路にできなかった箇所">
                <div className="mb-1 text-sm font-black">確認が必要な箇所（{issueCount}）</div>
                {issueCount === 0 ? (
                  <p className="text-[11px] text-emerald-700">すべての記号を回路に含められました。</p>
                ) : (
                  <>
                    <p className="mb-2 text-[11px] leading-5 text-slate-500">
                      「エディタで直す」でその記号を開けます。直したら上の「組み直す」を押すと反映されます。
                    </p>
                    <ul className="thin-scroll max-h-72 space-y-1 overflow-auto text-[11px] text-slate-600">
                      {summary.diagnostics.map((d, i) => (
                        <li key={i} className="rounded-lg bg-amber-50 px-2 py-1.5">
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
                  </>
                )}
              </section>

              {(summary.sheet_links?.length ?? 0) > 0 && (
                <section className="card p-4" aria-label="図面間のつながり">
                  <div className="mb-1 text-sm font-black">図面間のつながり（{summary.sheet_links?.length}）</div>
                  <p className="mb-2 text-[11px] leading-5 text-slate-500">
                    行先札・来先札を組にして、図面をまたいで配線をつなぎました。「推定」は向かい合う札が 1 組だけだったものです。
                  </p>
                  <ul className="thin-scroll max-h-48 space-y-1 overflow-auto text-[11px] text-slate-600">
                    {summary.sheet_links?.map((link, i) => (
                      <li key={i} className="flex flex-wrap items-center gap-1 rounded-lg bg-slate-50 px-2 py-1">
                        <Link className="font-bold text-cyan-700 underline" to={editorLink(link.from.project_id, link.from.symbol_ref)}>
                          {link.from.page} {link.from.label || link.from.symbol_ref}
                        </Link>
                        <span>⇄</span>
                        <Link className="font-bold text-cyan-700 underline" to={editorLink(link.to.project_id, link.to.symbol_ref)}>
                          {link.to.page} {link.to.label || link.to.symbol_ref}
                        </Link>
                        {link.how === 'guessed' && <span className="rounded bg-amber-100 px-1 text-[10px] font-bold text-amber-700">推定</span>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <ConsistencyPanel projectIds={projectIds} onApplied={rebuild} />
            </div>
          ),
        },
        {
          key: 'edit',
          label: '図面の変更',
          caption: '部品の追加・削除やつなぎ替えを試し、差分を確かめてから適用します。',
          icon: PencilRuler,
          render: () => <EditPanel projectIds={projectIds} onApplied={rebuild} />,
        },
      ]
    : []
  const activeTab = tabs.find((t) => t.key === tab) ?? tabs[0]

  return (
    <div className="page-shell enter-up">
      <header className="page-header">
        <div>
          <div className="eyebrow">Circuit simulation</div>
          <h1 className="page-title">回路の <span className="title-accent">確認・もしも</span></h1>
          <p className="page-description">
            図面のアノテーション（記号・端子・配線）から回路を組み、入力の On/Off でどのリレーが動くか、
            機器を変えたらどこまで影響するかを図面の上で確かめます。
          </p>
        </div>
        <ol className="flex flex-wrap items-center gap-1.5 text-xs" aria-label="進め方">
          {STEPS.map((label, i) => (
            <li
              key={label}
              aria-current={i === step ? 'step' : undefined}
              className={`flex items-center gap-1.5 rounded-full border py-1 pl-1 pr-2.5 font-semibold ${
                i === step
                  ? 'border-blue-300 bg-blue-50 text-blue-800'
                  : i < step
                    ? 'border-slate-200 bg-white text-slate-600'
                    : 'border-slate-200 bg-white text-slate-400'
              }`}
            >
              <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                i <= step ? 'bg-blue-600 text-white' : 'bg-slate-200 text-slate-500'
              }`}>
                {i + 1}
              </span>
              {label}
            </li>
          ))}
        </ol>
      </header>

      {error && <div className="notice mb-5 border-rose-200 bg-rose-50 text-rose-700" role="alert">{error}</div>}

      {summary && !choosing ? (
        <section className="card mb-4 flex flex-wrap items-center gap-2 p-3 sm:px-4" aria-label="対象の図面">
          <span className="text-[11px] font-bold text-slate-500">対象の図面</span>
          <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {summary.pages.map((p) => (
              <span key={p.id} className="rounded-lg bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-700">{p.name}</span>
            ))}
          </div>
          <span className="text-[11px] text-slate-500">
            記号 {summary.element_count} · 節点 {summary.node_count}
          </span>
          <button type="button" className="btn btn-sm" onClick={() => setChoosing(true)}>
            図面を選び直す
          </button>
          <button type="button" className="btn btn-sm" onClick={rebuild} disabled={busy} title="エディタで直した内容を読み込み直します">
            {busy ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />} 組み直す
          </button>
        </section>
      ) : (
        <section className="card mb-5 p-4 sm:p-5" aria-label="図面を選ぶ">
          <div className="flex flex-wrap items-center gap-2">
            <div className="mr-auto min-w-0">
              <div className="text-sm font-black text-slate-900">図面を選ぶ</div>
              <p className="mt-0.5 text-[11px] text-slate-500">
                複数選ぶと、母線・行先札と同じ器具番号のリレーを図面をまたいでつなぎます。
              </p>
            </div>
            <label className="relative">
              <span className="sr-only">図面を絞り込む</span>
              <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                className="field h-9 min-h-9 w-48 py-0 pl-7 text-xs"
                placeholder="シート番号・名前"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setIds((prev) => new Set([...prev, ...visibleRows.map((r) => r.id)]))}
              disabled={visibleRows.length === 0}
            >
              <ListChecks size={12} /> {filter ? '表示中を選ぶ' : 'すべて選ぶ'}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setIds(new Set())} disabled={ids.size === 0}>
              選択を外す
            </button>
          </div>
          <div className="thin-scroll my-3 flex max-h-44 flex-wrap gap-2 overflow-auto">
            {rows.length === 0 && (
              <p className="text-xs text-slate-500">
                まだ図面がありません。<Link className="font-bold text-blue-700 underline" to="/">図面ワークスペース</Link>で登録してください。
              </p>
            )}
            {visibleRows.map((r) => {
              const on = ids.has(r.id)
              return (
                <label
                  key={r.id}
                  className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-1.5 text-xs transition ${
                    on ? 'border-blue-400 bg-blue-50' : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <input type="checkbox" className="h-4 w-4" checked={on} onChange={() => toggleId(r.id)} />
                  <span className="font-bold text-slate-700">{sheetName(r)}</span>
                  <span className="font-mono text-slate-400">#{r.id} · {r.connection_count} 配線</span>
                </label>
              )
            })}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn btn-accent" onClick={() => void build()} disabled={busy || selected.length === 0}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
              回路を組んでシミュレーションする
            </button>
            <span className="text-[11px] text-slate-500">
              {selected.length ? `${selected.length} 枚を選択中` : '図面を 1 枚以上選んでください'}
            </span>
            {summary && (
              <button type="button" className="btn btn-sm ml-auto" onClick={() => setChoosing(false)}>
                {selectionChanged ? '選び直さずに戻る' : '閉じる'}
              </button>
            )}
          </div>
        </section>
      )}

      {summary && issueCount > 0 && tab !== 'check' && (
        <div className="notice mb-4 items-center border-amber-200 bg-amber-50 text-amber-800" role="status">
          <AlertTriangle size={16} className="flex-none" />
          <span className="flex-1 text-xs">
            回路にできなかった箇所が <b>{issueCount} 件</b>あります。結果に影響することがあります。
          </span>
          <button type="button" className="btn btn-sm border-amber-300 bg-white text-amber-800 hover:bg-amber-100" onClick={() => setTab('check')}>
            点検で見る
          </button>
        </div>
      )}

      {summary && (
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_30rem]">
          <section className="card overflow-hidden xl:sticky xl:top-4" aria-label="図面上の結果">
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 p-3">
              <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="図面のページ">
                {summary.pages.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    role="tab"
                    aria-selected={page === p.id}
                    className={`btn btn-sm ${page === p.id ? 'btn-accent' : ''}`}
                    onClick={() => setPage(p.id)}
                  >
                    {p.name}
                  </button>
                ))}
              </div>
              <span className="flex-1" />
              <span className="flex items-center gap-2 text-[11px] font-bold">
                <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700">入力 ON {onCount}</span>
                <span className="rounded-full bg-rose-50 px-2 py-0.5 text-rose-700">励磁 {energizedCount} / {summary.coils.length}</span>
                {busy && <Loader2 size={13} className="animate-spin text-slate-400" aria-label="計算中" />}
              </span>
            </div>
            {result?.warnings.map((w) => (
              <p key={w} className="flex gap-1.5 border-b border-rose-100 bg-rose-50 px-3 py-2 text-[11px] font-bold text-rose-700">
                <AlertTriangle size={13} className="flex-none" /> {w}
              </p>
            ))}
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
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-slate-100 px-3 py-2 text-[11px] text-slate-500">
              <span><span className="mr-1 inline-block h-0.5 w-4 bg-rose-500 align-middle" />電圧あり</span>
              <span><span className="mr-1 inline-block h-0.5 w-4 bg-slate-400/60 align-middle" />電圧なし</span>
              <span><span className="mr-1 inline-block h-2.5 w-2.5 border-2 border-emerald-500 align-middle" />閉じた接点</span>
              <span><span className="mr-1 inline-block h-2.5 w-2.5 border-2 border-dashed border-slate-400 align-middle" />開いた接点</span>
              <span><span className="mr-1 inline-block h-2.5 w-2.5 bg-rose-400/60 align-middle" />励磁したコイル</span>
            </div>
          </section>

          <div className="min-w-0">
            <div className="card mb-3 grid gap-1 p-1" style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }} role="tablist" aria-label="確かめる道具">
              {tabs.map((t) => {
                const Icon = t.icon
                const active = t.key === activeTab?.key
                return (
                  <button
                    key={t.key}
                    type="button"
                    role="tab"
                    id={`circuit-tab-${t.key}`}
                    aria-selected={active}
                    aria-controls="circuit-tool-panel"
                    className={`relative flex min-h-14 flex-col items-center justify-center gap-1 rounded-xl px-1 text-[11px] font-bold leading-tight transition ${
                      active ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-100'
                    }`}
                    onClick={() => setTab(t.key)}
                  >
                    <Icon size={16} />
                    <span className="text-center">{t.label}</span>
                    {t.badge ? (
                      <span className={`absolute right-1 top-1 min-w-4 rounded-full px-1 text-[9px] leading-4 ${
                        active ? 'bg-white text-blue-700' : t.tone === 'warn' ? 'bg-amber-500 text-white' : 'bg-slate-500 text-white'
                      }`}>
                        {t.badge}
                      </span>
                    ) : null}
                  </button>
                )
              })}
            </div>
            {activeTab && (
              <div
                id="circuit-tool-panel"
                role="tabpanel"
                aria-labelledby={`circuit-tab-${activeTab.key}`}
                className="[&_section.card]:mt-0"
              >
                <p className="mb-3 px-1 text-[11px] leading-5 text-slate-500">{activeTab.caption}</p>
                {activeTab.render()}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
