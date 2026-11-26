import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, FileSpreadsheet, Loader2, MapPin, Play, Presentation, RotateCcw, Sparkles, Upload } from 'lucide-react'

import { api } from '../api/client'
import { ConfirmDeleteDialog } from '../components/ConfirmDeleteDialog'
import type { AnalysisJob, DemoLocation, DemoView, IoTableSummary, ProjectRow } from '../types'
import DrawingViewer from './circuit/DrawingViewer'
import type { ViewerFocus, ViewerMark } from './circuit/DrawingViewer'
import FocusTrace from './demo/FocusTrace'
import type { TraceMode } from './demo/FocusTrace'
import type { TraceNode } from './demo/trace'

/** `#/demo?ids=1,2` で図面を選んだ状態で開く */
function idsFromQuery(value: string | null): number[] {
  if (!value) return []
  return value.split(',').map(Number).filter((v) => Number.isInteger(v) && v > 0)
}

type RowState = 'annotation' | 'ai' | 'none'

function rowState(row: ProjectRow): RowState {
  if (row.symbol_count > 0) return 'annotation'
  if (row.analysis_wire_count !== null) return 'ai'
  return 'none'
}

const STATE_LABEL: Record<RowState, { text: string; tone: string; title: string }> = {
  annotation: { text: '登録済み', tone: 'bg-emerald-50 text-emerald-700', title: '人が登録・確認した記号と配線で回路を組みます' },
  ai: { text: 'AI 解析のみ', tone: 'bg-violet-50 text-violet-700', title: 'AI が読み取った記号と配線で回路を組みます（人の確認前）' },
  none: { text: '未解析', tone: 'bg-slate-100 text-slate-600', title: 'AI 解析を実行すると、デモに使えます' },
}

const ROLE_LABEL = { coil: 'コイル', contact: '接点' } as const

// 論理図の SVG はサーバが作る。信号ごとのまとまり（g[data-sig]）を選べるようにする
const LOGIC_CSS = `
.demo-logic svg { width: 100%; height: auto; }
.demo-logic g[data-sig] { cursor: pointer; }
.demo-logic g[data-sig]:hover polygon, .demo-logic g[data-sig]:hover rect, .demo-logic g[data-sig]:hover circle { stroke: #7c3aed; }
.demo-logic g[data-selected] polygon, .demo-logic g[data-selected] rect, .demo-logic g[data-selected] circle { stroke: #7c3aed; stroke-width: 3; }
.demo-logic[data-focus] g[data-block]:not([data-path]) { opacity: 0.18; }
.demo-logic[data-focus] g[data-sig][data-path]:not([data-selected]) polygon, .demo-logic[data-focus] g[data-sig][data-path]:not([data-selected]) circle { stroke: #a78bfa; stroke-width: 2; }
`

/**
 * 図面と、その横に最終ページのような簡略化した論理図を並べて動かすデモ（DD-0046）。
 * 論理図の信号を選ぶと図面の上のコイル・接点を枠で示し、スイッチを入れると図面の線と論理図の線が同じように通じる。
 * 人が記号を登録していない図面は、AI 解析の結果だけで組む（PDF を登録して解析すれば動かせる）。
 */
export default function DemoPage() {
  const [params] = useSearchParams()
  const idsParam = params.get('ids')
  const [rows, setRows] = useState<ProjectRow[]>([])
  const [ids, setIds] = useState<Set<number>>(() => new Set(idsFromQuery(idsParam)))
  const [opened, setOpened] = useState<number[]>([])
  const [view, setView] = useState<DemoView | null>(null)
  const [inputs, setInputs] = useState<Record<string, boolean>>({})
  const [page, setPage] = useState<number | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [focus, setFocus] = useState<(ViewerFocus & { projectId: number }) | null>(null)
  // 注目点から辿った経路と見方（論理図を薄くする・図面に印を付けるのに使う）
  const [trace, setTrace] = useState<{ path: TraceNode[]; mode: TraceMode; step: DemoLocation | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState<AnalysisJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const logicRef = useRef<HTMLDivElement | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const ioRef = useRef<HTMLInputElement | null>(null)
  const [ioTable, setIoTable] = useState<IoTableSummary | null>(null)
  const [confirmIoClear, setConfirmIoClear] = useState(false)
  const handledParam = useRef<string | null>(null)

  const loadRows = () => api.listProjects().then(setRows).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  useEffect(() => {
    void loadRows()
    api.ioTable().then(setIoTable).catch(() => setIoTable(null))
  }, [])

  const uploadIo = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    try {
      setIoTable(await api.uploadIoTable(files[0]))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (ioRef.current) ioRef.current.value = ''
    }
  }
  const clearIo = async () => {
    try {
      setIoTable(await api.deleteIoTable())
      setConfirmIoClear(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const run = async (target: number[], next: Record<string, boolean>, energized: Record<string, boolean>) => {
    setBusy(true)
    try {
      const v = await api.demoView(target, next, energized)
      setView(v)
      setInputs(next)
      setPage((current) => (v.pages.some((p) => p.id === current) ? current : v.pages[0]?.id ?? null))
      setError(v.ready ? null : v.message ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const open = (target: number[]) => {
    if (target.length === 0) return
    setOpened(target)
    setSelected(null)
    setFocus(null)
    void run(target, {}, {})
  }

  useEffect(() => {
    if (!idsParam || rows.length === 0 || handledParam.current === idsParam) return
    handledParam.current = idsParam
    const target = rows.filter((r) => idsFromQuery(idsParam).includes(r.id)).map((r) => r.id)
    if (target.length) {
      setIds(new Set(target))
      open(target)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, idsParam])

  // AI 解析の進み具合を見て、終わったら一覧を読み直す
  useEffect(() => {
    if (!job || job.status !== 'running') return
    const timer = window.setTimeout(() => {
      api.getAnalysisJob(job.id).then((j) => {
        setJob(j)
        if (j.status !== 'running') void loadRows()
      }).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [job])

  const sim = view?.simulation
  const logic = view?.logic ?? null
  const energizedNow = () => Object.fromEntries(Object.entries(sim?.coil_states ?? {}).map(([d, s]) => [d, s.energized]))
  const toggle = (key: string) => void run(opened, { ...inputs, [key]: !(inputs[key] ?? defaultOf(key)) }, energizedNow())
  const reset = () => void run(opened, {}, {})
  const defaultOf = (key: string) => logic?.inputs.find((i) => i.signal === key)?.default ?? false
  const inputDevices = useMemo(() => new Set(sim?.inputs.map((i) => i.device) ?? []), [sim])
  const pathSignals = useMemo(() => new Set(selected && trace ? trace.path.map((n) => n.signal) : selected ? [selected] : []), [trace, selected])
  // 図面の印: 順に辿るときは今の 1 か所、ほかは経路に入る信号のコイル・接点すべて
  const locations: DemoLocation[] = trace?.mode === 'tour'
    ? (trace.step ? [trace.step] : [])
    : [...pathSignals].flatMap((sig) => logic?.locations[sig] ?? [])

  const choose = (sig: string) => {
    setSelected(sig)
    const locs = logic?.locations[sig] ?? []
    const here = locs.find((l) => l.project_id === page) ?? locs[0]
    if (here) {
      setPage(here.project_id)
      setFocus({ projectId: here.project_id, cx: here.cx, cy: here.cy, w: here.w, h: here.h, label: here.label ?? here.device, key: Date.now() })
    }
  }
  const jump = (loc: DemoLocation) => {
    setPage(loc.project_id)
    setFocus({ projectId: loc.project_id, cx: loc.cx, cy: loc.cy, w: loc.w, h: loc.h, label: loc.label ?? loc.device, key: Date.now() })
  }
  // 図面の上の入力を押したときは、切り替えたうえで論理図でもその信号を選ぶ
  const toggleOnDrawing = (device: string) => {
    toggle(device)
    const sig = logic && Object.entries(logic.locations).find(([, locs]) => locs.some((l) => l.device === device))?.[0]
    if (sig) setSelected(sig)
  }

  // 選んだ信号と経路に印を付け、経路の外のまとまりを薄くする（SVG は文字列なので、描いたあとに属性で示す）
  useEffect(() => {
    const root = logicRef.current
    if (!root) return
    const mark = (g: Element, name: string, on: boolean) => (on ? g.setAttribute(name, '') : g.removeAttribute(name))
    root.querySelectorAll('g[data-sig]').forEach((g) => {
      const sig = g.getAttribute('data-sig') ?? ''
      mark(g, 'data-selected', sig === selected)
      mark(g, 'data-path', pathSignals.has(sig))
    })
    root.querySelectorAll('g[data-block]').forEach((g) => mark(g, 'data-path', pathSignals.has(g.getAttribute('data-block') ?? '')))
    mark(root, 'data-focus', Boolean(selected && trace && trace.path.length > 1))
  }, [selected, logic?.svg, pathSignals, trace])

  const onLogicClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const g = (event.target as Element).closest('g[data-sig]')
    const sig = g?.getAttribute('data-sig')
    if (sig) choose(sig)
  }

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setBusy(true)
    try {
      const r = await api.createProjects(Array.from(files))
      await loadRows()
      setIds(new Set(r.project_ids))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }
  const analyze = async (target: number[]) => {
    try {
      setJob(await api.startAnalysisJob(target))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const selectedRows = rows.filter((r) => ids.has(r.id))
  const unanalyzed = selectedRows.filter((r) => rowState(r) === 'none').map((r) => r.id)
  const analyzing = job?.status === 'running'
  const row = rows.find((r) => r.id === page)
  const pageInfo = view?.pages.find((p) => p.id === page)
  const overlay = sim?.overlay.find((o) => o.project_id === page)
  const marks: ViewerMark[] = locations
    .filter((l) => l.project_id === page)
    .map((l) => ({ cx: l.cx, cy: l.cy, w: l.w, h: l.h, label: `${l.device}（${ROLE_LABEL[l.role]}）`, tone: l.role }))
  const aiPages = view?.pages.filter((p) => p.source === 'ai') ?? []

  const selector = (
    <section className="card p-4" aria-label="デモに使う図面">
      <div className="flex flex-wrap items-center gap-2">
        <input ref={fileRef} type="file" accept=".pdf,application/pdf,image/*" multiple className="hidden" aria-label="図面の PDF"
          onChange={(e) => void upload(e.target.files)} />
        <button type="button" className="btn btn-sm" onClick={() => fileRef.current?.click()} disabled={busy}>
          <Upload size={12} /> ① PDF を登録
        </button>
        <button type="button" className="btn btn-sm" onClick={() => void analyze(unanalyzed)} disabled={unanalyzed.length === 0 || analyzing}
          title="選んだ図面のうち未解析のものを AI で解析します">
          {analyzing ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />} ② AI 解析{unanalyzed.length > 0 ? `（${unanalyzed.length} 枚）` : ''}
        </button>
        <button type="button" className="btn btn-sm btn-primary" onClick={() => open(selectedRows.map((r) => r.id))} disabled={selectedRows.length === 0 || busy}>
          <Play size={12} /> ③ デモを開く（{selectedRows.length} 枚）
        </button>
        <input ref={ioRef} type="file" accept=".csv,.tsv,.txt,.xlsx" className="hidden" aria-label="I/O 表のファイル"
          onChange={(e) => void uploadIo(e.target.files)} />
        <button type="button" className="btn btn-sm" onClick={() => ioRef.current?.click()}
          title="他盤との信号の一覧（器具番号・信号名・入出力の列）。登録すると、一致する接点を外部入力にして信号名を付けます">
          <FileSpreadsheet size={12} /> I/O 表{ioTable?.loaded ? `（${ioTable.rows} 行）` : 'を登録'}
        </button>
        {ioTable?.loaded && (
          <button type="button" className="btn btn-sm" onClick={() => setConfirmIoClear(true)} title={`${ioTable.file_name ?? ''} を外します`}>
            I/O 表を外す
          </button>
        )}
        {confirmIoClear && ioTable?.loaded && (
          <ConfirmDeleteDialog
            title="I/O 表を外しますか？"
            removes={[`登録した I/O 表「${ioTable.file_name ?? ''}」（${ioTable.rows} 行）`, '接点に付けていた外部入力の印と信号名（次に回路を組んだときから）']}
            keeps={['図面とアノテーション（I/O 表は図面には書き込んでいません）', '元の CSV・Excel のファイル（もう一度登録できます）']}
            confirmLabel="外す"
            onConfirm={() => void clearIo()}
            onCancel={() => setConfirmIoClear(false)}
          />
        )}
        {analyzing && job && <span className="text-[11px] text-slate-500">解析中 {job.done} / {job.total}{job.current ? `（${job.current}）` : ''}</span>}
        {job && job.status !== 'running' && (
          <span className="text-[11px] text-slate-500">{job.status === 'done' ? '解析が終わりました' : `解析: ${job.error ?? job.status}`}</span>
        )}
      </div>
      <ul className="thin-scroll mt-3 max-h-64 divide-y divide-slate-100 overflow-auto rounded-lg border border-slate-200">
        {rows.map((r) => {
          const state = STATE_LABEL[rowState(r)]
          return (
            <li key={r.id}>
              <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs hover:bg-slate-50">
                <input type="checkbox" checked={ids.has(r.id)} onChange={() => setIds((prev) => {
                  const n = new Set(prev)
                  if (n.has(r.id)) n.delete(r.id)
                  else n.add(r.id)
                  return n
                })} />
                <span className="min-w-0 flex-1 truncate font-bold text-slate-700">
                  {r.sheet_no || r.name}
                  {r.sheet_no && <span className="ml-2 font-normal text-slate-400">{r.name}</span>}
                </span>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${state.tone}`} title={state.title}>{state.text}</span>
              </label>
            </li>
          )
        })}
        {rows.length === 0 && <li className="px-3 py-4 text-center text-xs text-slate-500">図面がありません。PDF を登録してください。</li>}
      </ul>
    </section>
  )

  return (
    <div className="flex min-h-dvh flex-col gap-3 p-3 sm:p-4 xl:h-dvh">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="flex items-center gap-1.5 text-sm font-black text-slate-900">
          <Presentation size={15} className="text-violet-600" /> 図面と論理図のデモ
        </h1>
        <p className="text-[11px] text-slate-500">論理図の部品を押すと図面の上の位置を示します。スイッチを入れると、図面と論理図の両方で線がつながります。</p>
        {opened.length > 0 && (
          <button type="button" className="btn btn-sm ml-auto" onClick={() => { setOpened([]); setView(null) }}>図面を選び直す</button>
        )}
      </header>

      {error && <div className="notice border-rose-200 bg-rose-50 text-rose-700" role="alert">{error}</div>}
      {opened.length === 0 && selector}

      {view && view.ready && sim && (
        <>
          {aiPages.length > 0 && (
            <div className="notice flex items-start gap-2 border-violet-200 bg-violet-50 text-[11px] text-violet-800">
              <AlertTriangle size={13} className="mt-0.5 flex-none" />
              <span>
                {aiPages.map((p) => p.name).join('、')} は人の確認前の AI 解析の結果で組んでいます。線がつながらない・位置が違うときは、
                <Link className="font-bold underline" to={`/projects/${aiPages[0].id}`}>エディタ</Link> で「AI 取込」して直すと正しくなります。
              </span>
            </div>
          )}
          <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-2">
            <section aria-label="図面" className="relative h-[60dvh] min-h-[380px] overflow-hidden rounded-2xl border border-slate-200 bg-slate-100 xl:h-auto">
              {row && pageInfo ? (
                <DrawingViewer
                  imageUrl={api.imageUrl(row.id)}
                  alt={pageInfo.name}
                  width={row.image_width}
                  height={row.image_height}
                  overlay={overlay}
                  inputs={inputs}
                  inputDevices={inputDevices}
                  onToggleInput={toggleOnDrawing}
                  disabled={busy}
                  insets={{ top: 56 }}
                  focus={focus && focus.projectId === row.id ? focus : null}
                  marks={marks}
                >
                  <div data-overlay-ui className="absolute left-3 right-3 top-3 flex flex-wrap items-center gap-1 rounded-xl border border-white/70 bg-white/90 p-1.5 shadow-lg backdrop-blur" role="tablist" aria-label="図面のページ">
                    {view.pages.map((p) => (
                      <button key={p.id} type="button" role="tab" aria-selected={page === p.id}
                        className={`rounded-lg px-2.5 py-1 text-xs font-bold ${page === p.id ? 'bg-blue-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
                        onClick={() => setPage(p.id)}>
                        {p.name}{p.source === 'ai' ? '（AI）' : ''}
                      </button>
                    ))}
                    {busy && <Loader2 size={13} className="ml-1 animate-spin text-slate-400" aria-label="計算中" />}
                  </div>
                </DrawingViewer>
              ) : (
                <div className="flex h-full items-center justify-center text-xs text-slate-500">図面を読み込んでいます…</div>
              )}
            </section>

            <section aria-label="論理図" className="card flex min-h-0 flex-col gap-3 overflow-hidden p-3">
              <style>{LOGIC_CSS}</style>
              {logic ? (
                <>
                  <div ref={logicRef} className="demo-logic thin-scroll min-h-[240px] flex-1 overflow-auto rounded-lg border border-slate-200 bg-white"
                    onClick={onLogicClick} data-testid="demo-logic"
                    // 図はサーバが作る SVG（器具番号・名前は HTML に逃がしてある）
                    dangerouslySetInnerHTML={{ __html: logic.svg }} />
                  <div className="grid gap-3 lg:grid-cols-[2fr_3fr]">
                    <div>
                      <div className="mb-1 flex items-center gap-2 text-xs font-black text-slate-800">
                        スイッチ
                        <button type="button" className="btn btn-sm ml-auto" onClick={reset} disabled={busy}><RotateCcw size={12} /> 初めに戻す</button>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {logic.inputs.map((i) => {
                          const on = Boolean(logic.values[i.signal])
                          return (
                            <button key={i.signal} type="button" aria-pressed={on} disabled={busy}
                              className={`rounded-lg border px-2 py-1 text-left text-[11px] font-bold ${on ? 'border-pink-300 bg-pink-100 text-pink-800' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}
                              onClick={() => { toggle(i.signal); setSelected(i.signal) }}
                              title={i.devices.join(' / ')}>
                              {i.name} <span className="font-mono">{on ? 'ON' : 'OFF'}</span>
                            </button>
                          )
                        })}
                      </div>
                    </div>
                    <div className="thin-scroll max-h-[24rem] overflow-auto pr-1">
                      {selected && logic.signals.some((x) => x.id === selected) ? (
                        <FocusTrace
                          focus={selected}
                          signals={logic.signals}
                          deps={logic.deps}
                          locations={logic.locations}
                          values={logic.values}
                          pages={view.pages}
                          onJump={jump}
                          onChoose={choose}
                          onPath={(path, mode, step) => setTrace({ path, mode, step })}
                        />
                      ) : (
                        <>
                          <div className="mb-1 flex items-center gap-1 text-xs font-black text-slate-800"><MapPin size={12} /> 注目点から辿る</div>
                          <p className="text-[11px] leading-5 text-slate-500">
                            論理図の部品（六角形・丸の英字・出力の箱）を押すと、そこを注目点にして原因（上流）・影響（下流）へ辿り、
                            図面の上でどう書かれているかを確かめられます。見方は「経路」「順に辿る」「一覧」から選べます。
                          </p>
                        </>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <p className="text-xs text-slate-500">{view.message ?? '論理図を作れませんでした。'}</p>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  )
}
