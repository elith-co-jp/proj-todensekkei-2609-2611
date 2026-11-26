import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, BadgeCheck, ChevronLeft, ChevronRight, Layers, RotateCcw, Rows3, Save, TriangleAlert } from 'lucide-react'

import { api } from '../api/client'
import { PageJump } from '../components/PageJump'
import type { ProjectRow, ProjectTables, ReviewTable } from '../types'
import { formatJstShort } from '../utils/jst'
import { getProjectNavigation } from '../utils/workspace'
import { LOW_SCORE, cellKey, defaultScale, rowHeight, sameValues, tableLayout, toValues, valueMap } from './tables/layout'
import type { CellRect } from './tables/layout'

const STATUS_LABEL: Record<ReviewTable['status'], string> = { unreviewed: '未確認', draft: '確認中', confirmed: '確定' }
const STATUS_TONE: Record<ReviewTable['status'], string> = {
  unreviewed: 'bg-slate-100 text-slate-600',
  draft: 'bg-amber-100 text-amber-800',
  confirmed: 'bg-emerald-100 text-emerald-800',
}

type ViewMode = 'stacked' | 'overlay'

const OVERLAY_TONE: Record<string, string> = {
  edited: 'border-sky-500 bg-sky-200/25 font-bold text-sky-700 focus:bg-white',
  low: 'border-amber-500 bg-amber-200/25 font-bold text-amber-700 focus:bg-white',
  ocr: 'border-slate-300/70 bg-transparent font-bold text-emerald-700 focus:bg-white',
  empty: 'border-transparent bg-transparent text-sky-700 hover:border-slate-300 focus:bg-white',
}

export default function TablesPage() {
  const projectId = Number(useParams().id)
  const [data, setData] = useState<ProjectTables | null>(null)
  const [project, setProject] = useState<{ id: number; name: string; width: number; height: number } | null>(null)
  const [projects, setProjects] = useState<ProjectRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [activeKey, setActiveKey] = useState<string | null>(null)
  // 並べ方と拡大は、保存して表を読み直しても変えない
  const [mode, setMode] = useState<ViewMode>('stacked')
  const [scales, setScales] = useState<Record<string, number>>({})
  const generation = useRef(0)

  useEffect(() => {
    const request = ++generation.current
    setData(null)
    setError(null)
    setNotice(null)
    void Promise.all([api.getTables(projectId), api.getProject(projectId), api.listProjects()]).then(([tables, detail, rows]) => {
      if (request !== generation.current) return
      setData(tables)
      setProject({ id: detail.id, name: detail.name, width: detail.image_width, height: detail.image_height })
      setProjects(rows)
      setActiveKey((key) => (tables.tables.some((t) => t.key === key) ? key : tables.tables[0]?.key ?? null))
    }).catch((caught) => {
      if (request === generation.current) setError(caught instanceof Error ? caught.message : String(caught))
    })
    return () => { generation.current++ }
  }, [projectId])

  const run = async (action: () => Promise<ProjectTables>, message: string) => {
    const request = generation.current
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const next = await action()
      if (request !== generation.current) return
      setData(next)
      setNotice(message)
    } catch (caught) {
      if (request === generation.current) setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (request === generation.current) setBusy(false)
    }
  }

  const navigation = getProjectNavigation(projects, projectId)
  const table = data?.tables.find((t) => t.key === activeKey) ?? null

  return (
    <div className="flex h-dvh min-h-[480px] flex-col bg-white text-slate-800">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-4 py-3">
        <Link to={`/projects/${projectId}/analysis`} className="icon-button flex-none" title="解析結果に戻る" aria-label="解析結果に戻る"><ArrowLeft size={19} /></Link>
        <div className="min-w-0 flex-1 basis-40">
          <h1 className="text-sm font-bold">表の読み取り確認</h1>
          <p className="mt-0.5 break-all text-xs text-slate-500">{project?.name ?? ''}</p>
        </div>
        <nav className="flex items-center gap-1" aria-label="図面の移動">
          {navigation.previousId ? <Link className="icon-button" to={`/projects/${navigation.previousId}/tables`} title="前の図面" aria-label="前の図面"><ChevronLeft size={18} /></Link> : <span className="inline-block w-9" />}
          {projects.length > 0 && <PageJump projects={projects} currentId={projectId} pathFor={(id) => `/projects/${id}/tables`} tone="light" />}
          {navigation.nextId ? <Link className="icon-button" to={`/projects/${navigation.nextId}/tables`} title="次の図面" aria-label="次の図面"><ChevronRight size={18} /></Link> : <span className="inline-block w-9" />}
        </nav>
      </header>
      {error && <p role="alert" className="border-b border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-800">{error}</p>}
      {notice && <p role="status" className="border-b border-emerald-200 bg-emerald-50 px-4 py-2 text-xs text-emerald-800">{notice}</p>}
      {!data ? (
        !error && <p role="status" className="p-6 text-sm text-slate-500">表の読み取りを読み込んでいます</p>
      ) : !data.analyzed ? (
        <div className="p-6 text-sm">
          <p>この図面はまだ解析していません。解析すると、リレー表・表題欄の読み取りをここで確かめられます。</p>
          <Link to={`/projects/${projectId}/analysis`} className="btn mt-3">解析結果へ</Link>
        </div>
      ) : data.tables.length === 0 ? (
        <p className="p-6 text-sm">この図面ではリレー表・表題欄が見つかりませんでした。</p>
      ) : (
        <>
          <div role="tablist" aria-label="表" className="flex flex-wrap gap-2 border-b border-slate-200 bg-slate-50 px-4 py-2">
            {data.tables.map((t) => (
              <button key={t.key} type="button" role="tab" aria-selected={t.key === activeKey} onClick={() => setActiveKey(t.key)}
                className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-semibold ${t.key === activeKey ? 'bg-white shadow-sm' : 'text-slate-600 hover:bg-white/70'}`}>
                {t.label}
                <span className={`rounded-full px-2 py-0.5 text-[11px] ${STATUS_TONE[t.status]}`}>{STATUS_LABEL[t.status]}</span>
              </button>
            ))}
          </div>
          {table && project && (
            <TableReviewer
              key={`${table.key}:${table.updated_at ?? ''}:${table.status}`}
              table={table} data={data} imageUrl={api.imageUrl(projectId)} image={project} busy={busy}
              mode={mode} onMode={setMode}
              scale={scales[table.key] ?? defaultScale(table.grid)} onScale={(value) => setScales((prev) => ({ ...prev, [table.key]: value }))}
              onSave={(values, confirm) => run(() => api.saveTable(projectId, table.key, values, confirm),
                confirm ? `${table.label}を確定しました` : '下書きを保存しました')}
              onReset={() => run(() => api.resetTable(projectId, table.key), '最新の AI の読みから始め直しました')}
            />
          )}
        </>
      )}
    </div>
  )
}

type ReviewerProps = {
  table: ReviewTable
  data: ProjectTables
  imageUrl: string
  image: { name: string; width: number; height: number }
  busy: boolean
  mode: ViewMode
  onMode: (mode: ViewMode) => void
  scale: number
  onScale: (scale: number) => void
  onSave: (values: ReturnType<typeof toValues>, confirm: boolean) => void
  onReset: () => void
}

function TableReviewer({ table, data, imageUrl, image, busy, mode, onMode, scale, onScale, onSave, onReset }: ReviewerProps) {
  const { grid } = table
  const layout = useMemo(() => tableLayout(grid), [grid])
  const ocr = useMemo(() => new Map(grid.cells.map((c) => [cellKey(c.row, c.col), c])), [grid])
  const saved = useMemo(() => valueMap(table.values), [table.values])
  const [values, setValues] = useState(() => new Map(saved))
  const [active, setActive] = useState<CellRect | null>(null)
  const inputs = useRef(new Map<string, HTMLInputElement>())

  const ocrText = useMemo(() => new Map([...ocr.entries()].map(([k, c]) => [k, c.text])), [ocr])
  const dirty = !sameValues(values, saved)
  const changed = layout.rects.filter((r) => (values.get(cellKey(r.row, r.col)) ?? '') !== (ocr.get(cellKey(r.row, r.col))?.text ?? ''))
  const lowScore = grid.cells.filter((c) => c.score < LOW_SCORE)

  const ownerOf = useCallback((row: number, col: number) =>
    layout.rects.find((r) => r.row === row && r.col <= col && col < r.col + r.colspan), [layout])

  const focusCell = (row: number, col: number) => {
    const rect = ownerOf(row, col)
    if (rect) inputs.current.get(cellKey(rect.row, rect.col))?.focus()
  }

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>, rect: CellRect) => {
    const down = event.key === 'ArrowDown' || (event.key === 'Enter' && !event.shiftKey)
    const up = event.key === 'ArrowUp' || (event.key === 'Enter' && event.shiftKey)
    if (!down && !up) return
    event.preventDefault()
    const row = rect.row + (down ? 1 : -1)
    if (row >= 0 && row < grid.rows.length) focusCell(row, rect.col)
  }

  const pickFromImage = (event: ReactMouseEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect()
    const x = layout.view.x + (event.clientX - bounds.left) / scale
    const y = layout.view.y + (event.clientY - bounds.top) / scale
    const rect = layout.rects.find((r) => x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h)
    if (rect) focusCell(rect.row, rect.col)
  }

  const setValue = (key: string, text: string) => setValues((prev) => new Map(prev).set(key, text))
  const width = layout.view.w * scale
  const height = layout.view.h * scale
  const fontSize = Math.max(10, Math.min(18, rowHeight(grid) * scale * 0.7))

  const background = {
    width, height,
    backgroundImage: `url(${imageUrl})`,
    backgroundSize: `${image.width * scale}px ${image.height * scale}px`,
    backgroundPosition: `${-layout.view.x * scale}px ${-layout.view.y * scale}px`,
    backgroundRepeat: 'no-repeat',
  }

  const highlight = active && (
    <div aria-hidden className="pointer-events-none absolute rounded-sm border-2 border-blue-500 bg-blue-400/15"
      style={{ left: (active.x - layout.view.x) * scale, top: (active.y - layout.view.y) * scale, width: active.w * scale, height: active.h * scale }} />
  )

  const cells = (overlay: boolean) => layout.rects.map((rect) => {
    const key = cellKey(rect.row, rect.col)
    const source = ocr.get(key)
    const value = values.get(key) ?? ''
    const edited = value !== (source?.text ?? '')
    const low = !edited && source !== undefined && source.score < LOW_SCORE
    const tone = edited ? 'border-sky-400 bg-sky-50 text-sky-900'
      : low ? 'border-amber-400 bg-amber-50 text-amber-950'
        : source ? 'border-slate-300 bg-white text-slate-900'
          : 'border-dashed border-slate-200 bg-transparent text-slate-900'
    const state = edited ? 'edited' : low ? 'low' : source ? 'ocr' : 'empty'
    const title = source
      ? `AI の読み: ${source.text}（確からしさ ${Math.round(source.score * 100)}%）`
      : 'AI は文字を読んでいません'
    return (
      <input
        key={key}
        ref={(el) => { if (el) inputs.current.set(key, el); else inputs.current.delete(key) }}
        aria-label={`${rect.row + 1} 行 ${rect.col + 1} 列`}
        title={title}
        value={value}
        onChange={(e) => setValue(key, e.target.value)}
        onFocus={() => setActive(rect)}
        onKeyDown={(e) => onKeyDown(e, rect)}
        data-state={state}
        className={`absolute rounded-[3px] border px-0.5 font-mono leading-none outline-none focus:z-10 focus:ring-2 focus:ring-blue-500 ${overlay ? OVERLAY_TONE[state] : tone}`}
        style={{ left: (rect.x - layout.view.x) * scale, top: (rect.y - layout.view.y) * scale, width: rect.w * scale, height: rect.h * scale,
          // 重ねるときは図面の文字が見えるよう、読みを小さく右下に寄せ、背景を透かす
          ...(overlay ? { fontSize: fontSize * 0.7, textAlign: 'right' as const, paddingTop: rect.h * scale * 0.35 } : { fontSize }) }}
      />
    )
  })

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 px-4 py-2 text-xs">
        <div className="segmented" role="group" aria-label="並べ方">
          <button type="button" className="segmented-item" aria-pressed={mode === 'stacked'} onClick={() => onMode('stacked')} title="図面の表と読み取り結果を上下に並べる"><Rows3 size={14} />上下</button>
          <button type="button" className="segmented-item" aria-pressed={mode === 'overlay'} onClick={() => onMode('overlay')} title="読み取り結果を図面の表に重ねる"><Layers size={14} />重ねる</button>
        </div>
        <label className="flex items-center gap-2 text-slate-600">
          拡大
          <input aria-label="拡大" type="range" min={50} max={400} step={25} value={Math.round(scale * 100)} onChange={(e) => onScale(Number(e.target.value) / 100)} className="w-28 accent-blue-600" />
          <span className="w-10 font-mono tabular-nums">{Math.round(scale * 100)}%</span>
        </label>
        <span className="text-slate-600">読み取り <b className="font-mono">{grid.cells.length}</b></span>
        <span className="flex items-center gap-1 text-amber-800" title={`確からしさ ${LOW_SCORE * 100}% 未満の読み`}><span className="h-3 w-3 rounded-sm border border-amber-400 bg-amber-50" />要確認 <b className="font-mono">{lowScore.length}</b></span>
        <span className="flex items-center gap-1 text-sky-800"><span className="h-3 w-3 rounded-sm border border-sky-400 bg-sky-50" />修正 <b className="font-mono">{changed.length}</b></span>
        <span className="ml-auto text-slate-500">
          {dirty ? '未保存の変更あり' : table.status === 'confirmed' ? `確定 ${formatJstShort(table.confirmed_at)}` : table.status === 'draft' ? `下書き ${formatJstShort(table.updated_at)}` : 'AI の読みのまま'}
        </span>
        <button type="button" className="btn btn-sm" disabled={busy || sameValues(values, ocrText)} onClick={() => setValues(new Map(ocrText))} title="直した欄をすべて AI の読みに戻す（保存するまで反映しない）"><RotateCcw size={14} />AI の読みに戻す</button>
        <button type="button" className="btn btn-sm" disabled={busy} onClick={() => onSave(toValues(values), false)} title="確定せずに保存する。確定済みの表は確認中に戻る"><Save size={14} />下書き保存</button>
        <button type="button" className="btn btn-sm btn-accent" disabled={busy || (!dirty && table.status === 'confirmed')} onClick={() => onSave(toValues(values), true)} title="この読みで確定する。リレー表は確定した読みを回路の照合・整合性チェックに使う"><BadgeCheck size={14} />確定</button>
      </div>
      {table.ocr_changed && (
        <div className="flex flex-wrap items-center gap-3 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">
          <TriangleAlert size={14} />確かめ始めた後に解析し直したため、AI の読みが変わっています。ここには確かめ始めたときの読みと、直した内容を出しています。
          <button type="button" className="btn btn-sm" disabled={busy} onClick={onReset}>最新の読みでやり直す</button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto bg-slate-100 p-4">
        {mode === 'stacked' ? (
          <div className="flex w-max flex-col gap-3">
            <section aria-label="図面の表">
              <h2 className="mb-1 text-xs font-bold text-slate-600">図面の表（クリックでその欄へ）</h2>
              <div className="relative cursor-crosshair border border-slate-300 bg-white" style={background} onClick={pickFromImage} role="img" aria-label={`${image.name} の${table.label}`}>
                {highlight}
              </div>
            </section>
            <section aria-label="読み取り結果">
              <h2 className="mb-1 text-xs font-bold text-slate-600">読み取り結果（欄を直す。Enter・↓ で下の欄へ）</h2>
              <div className="relative border border-slate-300 bg-white" style={{ width, height }}>{cells(false)}</div>
            </section>
          </div>
        ) : (
          <section aria-label="読み取り結果" className="w-max">
            <h2 className="mb-1 text-xs font-bold text-slate-600">図面の表に読み取り結果を重ねています</h2>
            <div className="relative border border-slate-300 bg-white" style={background} role="img" aria-label={`${image.name} の${table.label}`}>
              {cells(true)}
              {highlight}
            </div>
          </section>
        )}
        <div className="mt-4 grid max-w-5xl gap-4 lg:grid-cols-2">
          <section className="card p-3" aria-label="直した欄">
            <h2 className="mb-2 text-xs font-bold">直した欄（{changed.length}）</h2>
            {changed.length === 0 ? <p className="text-xs text-slate-500">まだ直した欄はありません。</p> : (
              <table className="w-full text-xs">
                <thead><tr className="text-left text-slate-500"><th className="py-1">位置</th><th>AI の読み</th><th>直した後</th></tr></thead>
                <tbody>
                  {changed.map((r) => {
                    const key = cellKey(r.row, r.col)
                    return (
                      <tr key={key} className="cursor-pointer border-t border-slate-100 hover:bg-slate-50" onClick={() => focusCell(r.row, r.col)}>
                        <td className="py-1">{r.row + 1} 行 {r.col + 1} 列</td>
                        <td className="font-mono text-slate-500">{ocr.get(key)?.text || '（空）'}</td>
                        <td className="font-mono text-sky-800">{values.get(key) || '（空）'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </section>
          {table.key === 'relay_table' && (
            <section className="card p-3" aria-label="リレー表の行">
              <h2 className="mb-2 text-xs font-bold">
                リレー表の行（{data.relay_source === 'confirmed' ? '確定した読み' : 'AI の読み'}から {data.relay_rows.length} 行）
              </h2>
              <p className="mb-2 text-[11px] text-slate-500">回路の照合・整合性チェックはこの行を使います。確定すると直した内容で読み直します。</p>
              <div className="max-h-64 overflow-auto">
                <table className="w-full text-xs">
                  <thead><tr className="text-left text-slate-500"><th className="py-1">器具番号</th><th>端子</th><th>接点</th><th>シート</th><th>行列</th></tr></thead>
                  <tbody className="font-mono">
                    {data.relay_rows.map((row, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="py-0.5">{row.device}</td><td>{row.terminal}</td><td>{row.contact}</td><td>{row.sheet}</td><td>{row.position}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
