import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  CircuitBoard,
  Download,
  Files,
  ListChecks,
  Loader2,
  Maximize,
  Minimize,
  Network,
  PanelRightClose,
  PanelRightOpen,
  PenLine,
  PowerOff,
  Presentation,
  RefreshCw,
  Route,
  Search,
  ShieldCheck,
  Tags,
  Timer,
  ToggleRight,
  Workflow,
  X,
} from 'lucide-react'

import { api } from '../api/client'
import type { CircuitResult, CircuitSummary, ProjectRow } from '../types'
import ConsistencyPanel from './circuit/ConsistencyPanel'
import DevicePanel from './circuit/DevicePanel'
import type { LocateTarget } from './circuit/DevicePanel'
import EditPanel from './circuit/EditPanel'
import LogicPanel from './circuit/LogicPanel'
import TimelinePanel from './circuit/TimelinePanel'
import TracePanel from './circuit/TracePanel'
import WhatIfPanel from './circuit/WhatIfPanel'
import DrawingViewer from './circuit/DrawingViewer'
import type { ViewerFocus } from './circuit/DrawingViewer'
import DrawingSetBar from './circuit/DrawingSetBar'

const ENGINE_LABEL = { ngspice: 'ngspice で計算', builtin: '内蔵ソルバで計算（ngspice 未導入）' }

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
  icon: typeof CircuitBoard
  badge?: number
  tone?: 'warn'
  render: () => ReactNode
}

const STEPS = ['図面選択', '回路作成', '動作確認'] as const

/** 道具のパネルを図面の上に重ねる幅（xl 以上）かどうか */
function useWideScreen() {
  const query = '(min-width: 1280px)'
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia(query)
    const update = () => setWide(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  return wide
}

/** 重ねた道具のパネルの幅（px）。全体表示はこの分を除いて合わせる */
const PANEL_WIDTH = 384

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
  // 機器索引などから選んだ記号（そのページを開き、記号を中心に拡大して枠を点滅させる）
  const [focus, setFocus] = useState<(ViewerFocus & { projectId: number }) | null>(null)
  const drawingRef = useRef<HTMLElement | null>(null)
  const [tab, setTab] = useState('operate')
  const [panelOpen, setPanelOpen] = useState(true)
  const [fullscreen, setFullscreen] = useState(false)
  const wide = useWideScreen()
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
  const locate = (target: LocateTarget) => {
    setPage(target.project_id)
    setFocus({ projectId: target.project_id, cx: target.cx, cy: target.cy, w: target.w, h: target.h, label: target.label, key: Date.now() })
    // パネルが図面の下にある幅では、図面が見える位置まで動かす
    if (!wide) drawingRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }
  const step = summary ? 2 : selected.length ? 1 : 0
  const energizedCount = summary ? summary.coils.filter((c) => result?.coil_states[c.device]?.energized).length : 0
  const onCount = Object.values(inputs).filter(Boolean).length
  const issueCount = summary?.diagnostics.length ?? 0
  const rebuild = () => void build(projectIds)

  const tabs: ToolTab[] = summary
    ? [
        {
          key: 'operate',
          label: '操作',
          caption: '入力を ON / OFF し、リレーの動作を見る。図面上の入力（水色）もクリックで切り替え。',
          icon: ToggleRight,
          render: () => (
            <div className="space-y-4">
              <section className="card p-4" aria-label="外部入力">
                <div className="flex items-baseline gap-2">
                  <div className="text-sm font-black">入力</div>
                  <span className="text-[11px] text-slate-500">{summary.inputs.length} 個 · ON {onCount}</span>
                </div>
                <p className="mb-2 mt-1 text-[11px] leading-5 text-slate-500" title="選んだ図面にコイルが無い接点を入力として扱います">
                  ON で a 接点は閉、b 接点は開。
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
                  <button type="button" className="btn btn-sm" onClick={reset} disabled={busy} title="すべての入力を OFF にして解き直す">
                    <PowerOff size={12} /> 全 OFF
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => void api.downloadNetlist(projectIds, inputs)} title="SPICE ネットリスト（.cir）。LTspice・ngspice で開けます">
                    <Download size={12} /> ネットリスト出力
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
                    {ENGINE_LABEL[result.engine]}・{result.steps} 回で収束
                  </p>
                )}
              </section>
            </div>
          ),
        },
        {
          key: 'devices',
          label: '機器',
          caption: '器具番号・電線番号で探し、コイルと接点がどのページのどこにあるかを見る。',
          icon: Tags,
          render: () => <DevicePanel projectIds={projectIds} onLocate={locate} />,
        },
        {
          key: 'trace',
          label: '条件',
          caption: '成立条件の逆引きと、影響の順引き。',
          icon: Route,
          render: () => <TracePanel projectIds={projectIds} />,
        },
        {
          key: 'logic',
          label: '論理図',
          caption: '出力のコイルから辿った論理図（AND・OR・NOT・タイマー・キープリレー）を作り、入力や時間の流れで動かす。',
          icon: Workflow,
          render: () => <LogicPanel projectIds={projectIds} />,
        },
        {
          key: 'whatif',
          label: '波及',
          caption: '機器を選び、影響範囲と、強制 ON / OFF したときの変化を見る。',
          icon: Network,
          render: () => <WhatIfPanel projectIds={projectIds} summary={summary} inputs={inputs} energized={energizedNow()} />,
        },
        {
          key: 'timeline',
          label: 'タイマー',
          caption: '入力の ON / OFF を時刻順に並べ、タイマーを含めて実行する。',
          icon: Timer,
          render: () => <TimelinePanel projectIds={projectIds} summary={summary} />,
        },
        {
          key: 'check',
          label: '検査',
          caption: '回路にできなかった箇所、シート間接続、解析結果の整合性を見る。',
          icon: ShieldCheck,
          badge: issueCount,
          tone: 'warn',
          render: () => (
            <div className="space-y-4">
              <section className="card p-4" aria-label="回路にできなかった箇所">
                <div className="mb-1 text-sm font-black">回路にできなかった箇所（{issueCount}）</div>
                {issueCount === 0 ? (
                  <p className="text-[11px] text-emerald-700">なし</p>
                ) : (
                  <>
                    <p className="mb-2 text-[11px] leading-5 text-slate-500">
                      「編集」で記号を開いて直し、「再作成」で反映。
                    </p>
                    <ul className="thin-scroll max-h-72 space-y-1 overflow-auto text-[11px] text-slate-600">
                      {summary.diagnostics.map((d, i) => (
                        <li key={i} className="rounded-lg bg-amber-50 px-2 py-1.5">
                          {d.page && <b>{d.page} </b>}
                          {d.symbol_ref && <span className="font-mono text-slate-400">{d.symbol_ref} </span>}
                          {d.message}
                          {d.project_id != null && d.symbol_ref && (
                            <Link className="ml-1 font-bold text-cyan-700 underline" to={editorLink(d.project_id, d.symbol_ref)}>
                              編集
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
                  <div className="mb-1 text-sm font-black">シート間接続（{summary.sheet_links?.length}）</div>
                  <p className="mb-2 text-[11px] leading-5 text-slate-500" title="行先札・来先札を組にして、図面をまたいで配線をつなぎました">
                    行先札・来先札の組。「推定」は候補が 1 組だけのもの。
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
          label: '変更',
          caption: '部品の追加・削除・つなぎ替えを試行し、差分を見てから適用する。',
          icon: PenLine,
          render: () => <EditPanel projectIds={projectIds} onApplied={rebuild} />,
        },
      ]
    : []
  const activeTab = tabs.find((t) => t.key === tab) ?? tabs[0]
  const overlayPanel = wide || fullscreen
  const inputDevices = useMemo(() => new Set(summary?.inputs.map((i) => i.device) ?? []), [summary])

  // 全画面は Esc で戻す。P でパネルを開閉
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target
      if (event.key === 'Escape' && fullscreen) setFullscreen(false)
      if ((event.key === 'p' || event.key === 'P') && !event.ctrlKey && !event.metaKey && !event.altKey
        && !(target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]'))) setPanelOpen((v) => !v)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullscreen])

  const openTab = (key: string) => {
    setTab(key)
    setPanelOpen(true)
  }

  const tabBar = (vertical: boolean) => (
    <div
      className={vertical ? 'flex flex-col gap-1' : 'grid gap-1'}
      style={vertical ? undefined : { gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
      role="tablist"
      aria-label="ツール"
    >
      {tabs.map((t) => {
        const Icon = t.icon
        const active = t.key === activeTab?.key && (panelOpen || !overlayPanel)
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`circuit-tab-${t.key}`}
            aria-selected={active}
            aria-controls="circuit-tool-panel"
            title={vertical ? t.label : undefined}
            className={`relative flex flex-col items-center justify-center gap-1 rounded-xl px-0 text-[11px] font-bold leading-tight transition ${
              vertical ? 'h-11 w-11' : 'min-h-14'
            } ${active ? 'bg-blue-600 text-white shadow-sm' : 'text-slate-600 hover:bg-slate-100'}`}
            onClick={() => openTab(t.key)}
          >
            <Icon size={16} />
            {/* 道具が 8 つ並んでも「タイマー」が折り返さないよう、横並びのときは詰めて 1 行にする */}
            <span className={vertical ? 'sr-only' : 'whitespace-nowrap text-center text-[10px] tracking-tighter'}>{t.label}</span>
            {t.badge ? (
              <span className={`absolute right-0.5 top-0.5 min-w-4 rounded-full px-1 text-[9px] leading-4 ${
                active ? 'bg-white text-blue-700' : t.tone === 'warn' ? 'bg-amber-500 text-white' : 'bg-slate-500 text-white'
              }`}>
                {t.badge}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )

  const toolContent = activeTab && (
    <div id="circuit-tool-panel" role="tabpanel" aria-labelledby={`circuit-tab-${activeTab.key}`} className="[&_section.card]:mt-0">
      <p className="mb-3 px-1 text-[11px] leading-5 text-slate-500">{activeTab.caption}</p>
      {activeTab.render()}
    </div>
  )

  const selector = (
    <section className="card mb-4 p-4 sm:p-5" aria-label="図面を選ぶ">
      <div className="flex flex-wrap items-center gap-2">
        <div className="mr-auto min-w-0">
          <div className="text-sm font-black text-slate-900">図面選択</div>
          <p className="mt-0.5 text-[11px] text-slate-500">
            複数選ぶと、母線・行先札・器具番号で図面をつなぎます。
          </p>
        </div>
        <label className="relative">
          <span className="sr-only">図面を絞り込む</span>
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            className="field h-9 min-h-9 w-48 py-0 pl-7 text-xs"
            placeholder="図面を検索"
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
          <ListChecks size={12} /> {filter ? '表示中を全選択' : '全選択'}
        </button>
        <button type="button" className="btn btn-sm" onClick={() => setIds(new Set())} disabled={ids.size === 0}>
          <X size={12} /> 選択解除
        </button>
      </div>
      <div className="mt-3">
        <DrawingSetBar selected={selected} onSelect={(next) => setIds(new Set(next))} />
      </div>
      <div className="thin-scroll mb-3 flex max-h-44 flex-wrap gap-2 overflow-auto">
        {rows.length === 0 && (
          <p className="text-xs text-slate-500">
            まだ図面がありません。<Link className="font-bold text-blue-700 underline" to="/">図面一覧</Link>で登録してください。
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
          {busy ? <Loader2 size={14} className="animate-spin" /> : <CircuitBoard size={14} />}
          回路を作成
        </button>
        <span className="text-[11px] text-slate-500">
          {selected.length ? `${selected.length} 枚選択` : '図面を選んでください'}
        </span>
        {summary && (
          <button type="button" className="btn btn-sm ml-auto" onClick={() => setChoosing(false)}>
            {selectionChanged ? 'キャンセル' : '閉じる'}
          </button>
        )}
      </div>
    </section>
  )

  if (!summary) {
    return (
      <div className="page-shell enter-up">
        <header className="page-header">
          <div>
            <div className="eyebrow">Circuit simulation</div>
            <h1 className="page-title">回路 <span className="title-accent">検証</span></h1>
            <p className="page-description">
              アノテーションから回路を作り、入力の ON / OFF でリレーの動作と影響範囲を図面上で確かめます。
            </p>
          </div>
          <ol className="flex flex-wrap items-center gap-1.5 text-xs" aria-label="進め方">
            {STEPS.map((label, i) => (
              <li
                key={label}
                aria-current={i === step ? 'step' : undefined}
                className={`flex items-center gap-1.5 rounded-full border py-1 pl-1 pr-2.5 font-semibold ${
                  i === step ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-400'
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
        {selector}
      </div>
    )
  }

  // 右上のボタン列（約 56px）とパネルの間・外側の余白を足した幅を、全体表示から除く
  const insets = { top: 120, right: overlayPanel ? (panelOpen ? PANEL_WIDTH + 80 : 72) : 0 }

  return (
    <div className={`flex flex-col gap-3 p-3 sm:p-4 ${wide ? 'h-dvh' : ''}`}>
      <section className="card flex flex-wrap items-center gap-2 px-3 py-2" aria-label="対象の図面">
        <h1 className="mr-1 flex items-center gap-1.5 text-sm font-black text-slate-900">
          <CircuitBoard size={15} className="text-rose-600" /> 回路検証
        </h1>
        <span className="hidden h-5 w-px bg-slate-200 sm:block" />
        <span className="text-[11px] font-bold text-slate-500">図面</span>
        <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
          {summary.pages.map((p) => (
            <span key={p.id} className="rounded-lg bg-slate-100 px-2 py-0.5 text-xs font-bold text-slate-700">{p.name}</span>
          ))}
        </div>
        <span className="text-[11px] text-slate-500">記号 {summary.element_count} · 節点 {summary.node_count}</span>
        {issueCount > 0 && (
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-800 hover:bg-amber-100"
            onClick={() => openTab('check')}
            title={`回路にできなかった箇所が ${issueCount} 件あります。結果に影響することがあります`}
          >
            <AlertTriangle size={12} /> 警告 {issueCount}
          </button>
        )}
        <Link className="btn btn-sm" to={`/demo?ids=${projectIds.join(',')}`} title="図面と論理図を並べて動かす">
          <Presentation size={12} /> デモ表示
        </Link>
        <button type="button" className="btn btn-sm" onClick={() => setChoosing((v) => !v)} title="対象の図面を選び直す">
          <Files size={12} /> 図面選択
        </button>
        <button type="button" className="btn btn-sm" onClick={rebuild} disabled={busy} title="エディタで直した内容を読み込み、回路を作り直す">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} 再作成
        </button>
      </section>

      {error && <div className="notice border-rose-200 bg-rose-50 text-rose-700" role="alert">{error}</div>}
      {choosing && selector}

      <section
        ref={drawingRef}
        aria-label="図面上の結果"
        className={
          fullscreen
            ? 'fixed inset-0 z-[60] bg-slate-100'
            : `relative overflow-hidden rounded-2xl border border-slate-200 bg-slate-100 ${wide ? 'min-h-[520px] flex-1' : 'h-[72dvh] min-h-[420px]'}`
        }
      >
        {row ? (
          <DrawingViewer
            imageUrl={api.imageUrl(row.id)}
            alt={sheetName(row)}
            width={row.image_width}
            height={row.image_height}
            overlay={overlay}
            inputs={inputs}
            inputDevices={inputDevices}
            onToggleInput={toggle}
            disabled={busy}
            insets={insets}
            focus={focus && focus.projectId === row.id ? focus : null}
          >
            <div data-overlay-ui className="absolute left-3 top-3 flex flex-col gap-2" style={{ maxWidth: `calc(100% - ${Math.max(insets.right, 64) + 24}px)` }}>
              <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-white/70 bg-white/90 p-1.5 shadow-lg shadow-slate-900/10 backdrop-blur">
                <div className="flex flex-wrap gap-1" role="tablist" aria-label="図面のページ">
                  {summary.pages.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      role="tab"
                      aria-selected={page === p.id}
                      className={`rounded-lg px-2.5 py-1.5 text-xs font-bold transition ${page === p.id ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
                      onClick={() => setPage(p.id)}
                    >
                      {p.name}
                    </button>
                  ))}
                </div>
                <span className="mx-1 h-5 w-px bg-slate-200" />
                <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-bold text-emerald-700">入力 ON {onCount}</span>
                <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-bold text-rose-700">励磁 {energizedCount} / {summary.coils.length}</span>
                {busy && <Loader2 size={13} className="animate-spin text-slate-400" aria-label="計算中" />}
              </div>
              {result?.warnings.map((w) => (
                <p key={w} className="flex gap-1.5 rounded-xl border border-rose-200 bg-rose-50/95 px-3 py-1.5 text-[11px] font-bold text-rose-700 shadow-sm">
                  <AlertTriangle size={13} className="flex-none" /> {w}
                </p>
              ))}
              <div className="hidden flex-wrap items-center gap-x-3 gap-y-1 self-start rounded-xl sm:flex border border-white/70 bg-white/85 px-2.5 py-1 text-[10px] text-slate-500 shadow-sm backdrop-blur">
                <span><span className="mr-1 inline-block h-0.5 w-4 bg-rose-500 align-middle" />通電</span>
                <span><span className="mr-1 inline-block h-2.5 w-2.5 border-2 border-emerald-500 align-middle" />接点 閉</span>
                <span><span className="mr-1 inline-block h-2.5 w-2.5 border-2 border-dashed border-slate-400 align-middle" />接点 開</span>
                <span><span className="mr-1 inline-block h-2.5 w-2.5 bg-rose-400/60 align-middle" />励磁</span>
                {inputDevices.size > 0 && <span title="図面上の入力をクリックすると ON / OFF が切り替わります"><span className="mr-1 inline-block h-2.5 w-2.5 border-2 border-sky-500 bg-sky-500/20 align-middle" />入力（クリックで切替）</span>}
              </div>
            </div>

            <div data-overlay-ui className="pointer-events-none absolute bottom-3 right-3 top-3 flex items-start gap-2 [&>*]:pointer-events-auto">
              {overlayPanel && panelOpen && activeTab && (
                <div
                  className="flex max-h-full flex-col overflow-hidden rounded-2xl border border-white/70 bg-white/95 shadow-2xl shadow-slate-900/20 backdrop-blur"
                  style={{ width: PANEL_WIDTH }}
                >
                  <div className="flex items-center gap-2 border-b border-slate-100 p-1.5">
                    <div className="min-w-0 flex-1">{tabBar(false)}</div>
                  </div>
                  <div className="thin-scroll min-h-0 flex-1 overflow-auto p-3">{toolContent}</div>
                </div>
              )}
              <div className="flex flex-col gap-1 rounded-2xl border border-white/70 bg-white/90 p-1 shadow-lg shadow-slate-900/10 backdrop-blur">
                <button
                  type="button"
                  className="icon-button h-11 w-11 text-slate-600 hover:bg-slate-100"
                  onClick={() => setFullscreen((v) => !v)}
                  aria-label={fullscreen ? '全画面を終了' : '全画面'}
                  title={fullscreen ? '全画面を終了（Esc）' : '全画面（メニューを隠す）'}
                >
                  {fullscreen ? <Minimize size={17} /> : <Maximize size={17} />}
                </button>
                {overlayPanel && (
                  <>
                    <button
                      type="button"
                      className="icon-button h-11 w-11 text-slate-600 hover:bg-slate-100"
                      onClick={() => setPanelOpen((v) => !v)}
                      aria-label={panelOpen ? 'パネルを閉じる' : 'パネルを開く'}
                      title={panelOpen ? 'パネルを閉じる（P）' : 'パネルを開く（P）'}
                    >
                      {panelOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
                    </button>
                    {!panelOpen && (
                      <>
                        <span className="mx-2 h-px bg-slate-200" />
                        {tabBar(true)}
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
          </DrawingViewer>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-slate-500">図面を読み込んでいます…</div>
        )}
      </section>

      {!overlayPanel && (
        <div className="min-w-0">
          <div className="card mb-3 p-1">{tabBar(false)}</div>
          {toolContent}
        </div>
      )}
    </div>
  )
}
