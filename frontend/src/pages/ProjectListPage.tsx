import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  ArrowUpRight,
  Cable,
  CheckCircle2,
  CircleDot,
  Download,
  FileStack,
  LayoutGrid,
  List,
  RefreshCw,
  Search,
  Shapes,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react'

import { api } from '../api/client'
import { LoadingOverlay } from '../components/LoadingOverlay'
import { useModalFocus } from '../hooks/useModalFocus'
import type { ProjectRow, Stats } from '../types'
import { STATUS_LABEL } from '../types'

const STATUS_CLASS: Record<string, string> = {
  draft: 'bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200',
  review: 'bg-amber-100 text-amber-800 ring-1 ring-inset ring-amber-200',
  done: 'bg-emerald-100 text-emerald-700 ring-1 ring-inset ring-emerald-200',
}

const STATUS_DOT: Record<string, string> = {
  draft: 'bg-slate-400',
  review: 'bg-amber-500',
  done: 'bg-emerald-500',
}

const STATUS_FILTERS = [
  { key: 'all', label: 'すべて' },
  { key: 'draft', label: STATUS_LABEL.draft },
  { key: 'review', label: STATUS_LABEL.review },
  { key: 'done', label: STATUS_LABEL.done },
] as const

type StatusFilter = (typeof STATUS_FILTERS)[number]['key']
type ViewMode = 'grid' | 'list'

const VIEW_STORAGE_KEY = 'todenyolo.workspace.view'

const WORKFLOW_STEPS = ['登録', 'AI 解析', '確認・修正', '出力']

function readViewMode(): ViewMode {
  try {
    return window.localStorage.getItem(VIEW_STORAGE_KEY) === 'list' ? 'list' : 'grid'
  } catch {
    return 'grid'
  }
}

function DrawingThumbnail({ id }: { id: number }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div className="flex h-full w-full items-center justify-center text-slate-400">
        <FileStack size={30} aria-hidden="true" />
      </div>
    )
  }
  return (
    <img
      src={api.thumbnailUrl(id)}
      alt=""
      loading="lazy"
      decoding="async"
      className="h-full w-full object-contain p-4 drop-shadow-[0_6px_14px_rgba(15,23,42,0.14)] transition duration-300 group-hover:scale-[1.03]"
      onError={() => setFailed(true)}
    />
  )
}

export default function ProjectListPage() {
  const [rows, setRows] = useState<ProjectRow[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [q, setQ] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [view, setView] = useState<ViewMode>(readViewMode)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [busyLabel, setBusyLabel] = useState<{ message: string; hint: string } | null>(null)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 直近の登録で作られた図面（登録直後に AI 推論へ進める導線用）
  const [justRegistered, setJustRegistered] = useState<number[] | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const anchorRef = useRef<number | null>(null)
  const deleteDialogRef = useRef<HTMLElement | null>(null)
  const loadGenerationRef = useRef(0)
  const closeDeleteDialog = useCallback(() => setDeleteDialogOpen(false), [])

  useModalFocus({
    open: deleteDialogOpen,
    containerRef: deleteDialogRef,
    onClose: closeDeleteDialog,
  })

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(q.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [q])

  const load = useCallback(async () => {
    const generation = loadGenerationRef.current + 1
    loadGenerationRef.current = generation
    try {
      const [list, nextStats] = await Promise.all([
        api.listProjects(debouncedQuery || undefined),
        api.stats(),
      ])
      if (generation !== loadGenerationRef.current) return
      setRows(list)
      setStats(nextStats)
      setError(null)
    } catch (caught) {
      if (generation !== loadGenerationRef.current) return
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (generation === loadGenerationRef.current) setLoading(false)
    }
  }, [debouncedQuery])

  useEffect(() => {
    void load()
  }, [load])

  const changeView = (next: ViewMode) => {
    setView(next)
    try {
      window.localStorage.setItem(VIEW_STORAGE_KEY, next)
    } catch {
      // 保存できない環境では、この画面を開いている間だけ切り替える
    }
  }

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = { all: rows.length }
    for (const row of rows) counts[row.status] = (counts[row.status] ?? 0) + 1
    return counts
  }, [rows])

  const visibleRows = useMemo(
    () => (statusFilter === 'all' ? rows : rows.filter((row) => row.status === statusFilter)),
    [rows, statusFilter],
  )

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setBusy(true)
    setUploading(true)
    setMsg(null)
    setError(null)
    try {
      const response = await api.createProjects(Array.from(files))
      setMsg(`${response.count} 件の図面を登録しました`)
      // 推論できる状態なら「登録した図面に AI 推論」ボタンを出す
      let inferable = false
      try {
        const s = await api.mlStatus()
        inferable = Boolean(s.ultralytics && s.active_model)
      } catch {
        inferable = false
      }
      setJustRegistered(inferable ? response.project_ids : null)
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setUploading(false)
      setBusy(false)
    }
  }

  /** ファイラ風の Shift 範囲選択 */
  const toggle = (id: number, shift: boolean) => {
    setChecked((previous) => {
      const next = new Set(previous)
      if (shift && anchorRef.current !== null) {
        const ids = visibleRows.map((row) => row.id)
        const start = ids.indexOf(anchorRef.current)
        const end = ids.indexOf(id)
        if (start >= 0 && end >= 0) {
          for (let index = Math.min(start, end); index <= Math.max(start, end); index += 1) {
            next.add(ids[index])
          }
          return next
        }
      }
      if (next.has(id)) next.delete(id)
      else next.add(id)
      anchorRef.current = id
      return next
    })
  }

  const removeChecked = async () => {
    if (checked.size === 0) return
    setBusy(true)
    setBusyLabel({
      message: '選択した図面を削除しています',
      hint: '図面とアノテーションデータを削除しています。',
    })
    try {
      await api.bulkDelete(Array.from(checked))
      setChecked(new Set())
      setJustRegistered(null)
      setMsg('選択した図面を削除しました')
      await load()
      setDeleteDialogOpen(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
      setBusyLabel(null)
    }
  }

  const exportChecked = async () => {
    setBusy(true)
    setBusyLabel({
      message: 'ZIP を作成しています',
      hint: '図面とアノテーションデータをまとめています。',
    })
    try {
      const response = await api.exportBulk({ ids: Array.from(checked) })
      setMsg(`${response.name} を出力しました（${(response.size / 1024).toFixed(1)} KB）`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
      setBusyLabel(null)
    }
  }

  const inferChecked = async () => {
    setBusy(true)
    setError(null)
    setBusyLabel({
      message: '図面を解析しています',
      hint: 'シンボル・配線・接続関係を処理しています。',
    })
    try {
      const r = await api.runAnalysis(Array.from(checked))
      setMsg(
        `解析完了: シンボル ${r.detection_count} 件、配線 ${r.results.reduce((n, page) => n + (page.wire_count ?? 0), 0)} 本。${r.results.flatMap((page) => page.warnings ?? []).length ? '一部のラベル読み取りに問題があります。解析結果を確認してください。' : ''}`,
      )
      setJustRegistered(null)
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
      setBusyLabel(null)
    }
  }

  /** 登録直後の図面にそのまま推論をかける */
  const inferRegistered = async () => {
    if (!justRegistered || justRegistered.length === 0) return
    setBusy(true)
    setError(null)
    setBusyLabel({
      message: '図面を解析しています',
      hint: 'シンボル・配線・接続関係を処理しています。',
    })
    try {
      const r = await api.runAnalysis(justRegistered)
      setMsg(
        `解析完了: シンボル ${r.detection_count} 件、配線 ${r.results.reduce((n, page) => n + (page.wire_count ?? 0), 0)} 本。${r.results.flatMap((page) => page.warnings ?? []).length ? '一部のラベル読み取りに問題があります。解析結果を確認してください。' : ''}`,
      )
      setJustRegistered(null)
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
      setBusyLabel(null)
    }
  }

  const completionRate = stats && stats.project_count > 0
    ? Math.round(((stats.by_status.done ?? 0) / stats.project_count) * 100)
    : 0

  const statsCards = stats
    ? [
        {
          label: '登録図面',
          value: stats.project_count,
          unit: '枚',
          icon: FileStack,
          color: 'text-blue-700 bg-blue-50',
          progress: null,
          caption: `作業中 ${stats.by_status.draft ?? 0} ・ レビュー待ち ${stats.by_status.review ?? 0}`,
        },
        { label: 'シンボル', value: stats.symbol_count, unit: '件', icon: Shapes, color: 'text-violet-700 bg-violet-50', progress: null, caption: '全図面の合計' },
        { label: '配線', value: stats.connection_count, unit: '件', icon: Cable, color: 'text-amber-700 bg-amber-50', progress: null, caption: '全図面の合計' },
        { label: '完了', value: stats.by_status.done ?? 0, unit: '枚', icon: CheckCircle2, color: 'text-emerald-700 bg-emerald-50', progress: completionRate, caption: null },
      ]
    : []

  return (
    <div className="page-shell enter-up">
      {uploading && (
        <LoadingOverlay
          message="図面をアップロードしています"
          hint="PDFをページ単位へ変換しています。画面を閉じずにお待ちください。"
        />
      )}
      {loading && !uploading && !busyLabel && (
        <LoadingOverlay message="図面を読み込んでいます" hint="作業状況と登録済み図面を取得しています。" />
      )}
      {busyLabel && <LoadingOverlay message={busyLabel.message} hint={busyLabel.hint} />}

      <div
        aria-hidden={deleteDialogOpen || loading || uploading || busyLabel !== null}
        inert={deleteDialogOpen || loading || uploading || busyLabel !== null ? true : undefined}
      >

      <header className="page-header">
        <div>
          <div className="eyebrow">Annotation operations</div>
          <h1 className="page-title">図面<span className="title-accent">ワークスペース</span></h1>
          <p className="page-description">
            PDFをページ単位で管理し、作業状況を確認しながらアノテーションを進めます。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn" onClick={() => void load()} disabled={busy}>
            <RefreshCw size={16} className={busy ? 'animate-spin' : ''} /> 更新
          </button>
          <button type="button" className="btn btn-accent" onClick={() => fileRef.current?.click()} disabled={busy}>
            <Upload size={16} /> PDF / 図面を登録
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf,.pdf,image/png,image/jpeg,image/bmp"
            multiple
            hidden
            onChange={(event) => {
              void upload(event.target.files)
              event.target.value = ''
            }}
          />
        </div>
      </header>

      <section className="info-banner mb-6" aria-label="作業の流れ">
        <div className="flex items-center gap-4 xl:contents">
          <span className="info-banner-icon">
            <FileStack size={24} />
          </span>
          <div className="min-w-0 xl:flex-none">
            <h2 className="text-lg font-semibold tracking-tight text-slate-950">1ページずつ迷わず処理</h2>
            <p className="mt-0.5 text-xs text-slate-600">PDFはページごとに自動で分割</p>
          </div>
        </div>
        <span className="info-banner-divider" />
        <ol className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-2 text-xs text-slate-700">
          {WORKFLOW_STEPS.map((step, index) => (
            <li key={step} className="flex items-center gap-1.5">
              <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-slate-200 bg-white py-1 pl-1 pr-2.5 font-semibold shadow-sm shadow-slate-900/[0.03]">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-blue-600 text-[10px] font-bold text-white">
                  {index + 1}
                </span>
                {step}
              </span>
              {index < WORKFLOW_STEPS.length - 1 && <ArrowRight size={13} className="text-slate-400" aria-hidden="true" />}
            </li>
          ))}
        </ol>
      </section>

      {statsCards.length > 0 && (
        <section className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="作業状況">
          {statsCards.map(({ label, value, unit, icon: Icon, color, progress, caption }) => (
            <div key={label} className="stat-card">
              <div className="flex items-start justify-between gap-3">
                <div>
              <div className="text-[11px] font-semibold text-slate-500">{label}</div>
              <div className="mt-2 text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">
                    {value}
                    <span className="ml-1 text-xs font-bold text-slate-400">{unit}</span>
                  </div>
                </div>
                <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${color}`}>
                  <Icon size={19} />
                </span>
              </div>
              {caption && <div className="mt-3 text-[11px] font-medium text-slate-500">{caption}</div>}
              {progress !== null && (
                <div className="mt-3">
                  <div
                    className="h-1.5 overflow-hidden rounded-full bg-slate-100"
                    role="progressbar"
                    aria-label="完了率"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={progress}
                  >
                    <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-500" style={{ width: `${progress}%` }} />
                  </div>
                  <div className="mt-1.5 text-[11px] font-semibold text-emerald-700">完了率 {progress}%</div>
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {(msg || error) && (
        <div
          className={`notice mb-5 ${
            error
              ? 'border-rose-200 bg-rose-50 text-rose-700'
              : 'border-emerald-200 bg-emerald-50 text-emerald-700'
          }`}
          role={error ? 'alert' : 'status'}
        >
          {error ? <X size={18} className="mt-0.5 flex-none" /> : <CheckCircle2 size={18} className="mt-0.5 flex-none" />}
          <span className="flex-1">{error ?? msg}</span>
          {!error && justRegistered && justRegistered.length > 0 && (
            <button
              type="button"
              className="btn btn-sm flex-none border-emerald-300 bg-white text-emerald-700 hover:bg-emerald-100"
              onClick={() => void inferRegistered()}
              disabled={busy}
            >
              <Sparkles size={13} /> 登録した {justRegistered.length} 件に AI 解析を実行
            </button>
          )}
        </div>
      )}

      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 pt-4 sm:px-5">
          <div className="mr-auto min-w-0">
            <h2 className="text-base font-semibold text-slate-900">登録済みの図面</h2>
            <p className="mt-0.5 text-[11px] text-slate-500">チェックはShiftキーで範囲選択できます</p>
          </div>
          <button type="button" className="btn btn-sm" onClick={() => void inferChecked()} disabled={busy}>
            <Sparkles size={14} /> {checked.size ? `選択 ${checked.size} 件` : '全件'}に AI 解析
          </button>
          <button type="button" className="btn btn-sm" onClick={() => void exportChecked()} disabled={busy}>
            <Download size={14} /> {checked.size ? `選択 ${checked.size} 件` : '全件'}を出力
          </button>
          <button
            type="button"
            className="btn btn-sm btn-danger"
            onClick={() => {
              setError(null)
              setDeleteDialogOpen(true)
            }}
            disabled={busy || checked.size === 0}
          >
            <Trash2 size={14} /> 削除
          </button>
          <div className="segmented" role="group" aria-label="表示形式">
            <button
              type="button"
              className="segmented-item !px-2.5"
              aria-pressed={view === 'grid'}
              aria-label="カード表示"
              title="カード表示"
              onClick={() => changeView('grid')}
            >
              <LayoutGrid size={15} />
            </button>
            <button
              type="button"
              className="segmented-item !px-2.5"
              aria-pressed={view === 'list'}
              aria-label="表で表示"
              title="表で表示"
              onClick={() => changeView('list')}
            >
              <List size={15} />
            </button>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 pb-4 pt-1">
            <div className="segmented" role="group" aria-label="状態で絞り込み">
              {STATUS_FILTERS.map(({ key, label }) => (
                <button
                  key={key}
                  type="button"
                  className="segmented-item"
                  aria-pressed={statusFilter === key}
                  onClick={() => setStatusFilter(key)}
                >
                  {key !== 'all' && <span className={`h-2 w-2 rounded-full ${STATUS_DOT[key]}`} aria-hidden="true" />}
                  {label}
                  <span className="segmented-count">{statusCounts[key] ?? 0}</span>
                </button>
              ))}
            </div>
          <label className="relative w-full sm:w-auto sm:min-w-56 sm:flex-1">
            <span className="sr-only">図面を検索</span>
            <Search size={15} className="pointer-events-none absolute left-3 top-3 text-slate-400" />
            <input
              className="field w-full !rounded-full pl-9 text-xs"
              placeholder="ID・名称・シート番号で検索"
              value={q}
              onChange={(event) => setQ(event.target.value)}
            />
          </label>
          </div>
        </div>

        {view === 'grid' && (
          <div className="bg-slate-50/60 p-4 sm:p-5">
            {visibleRows.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center">
                <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-600">
                  <FileStack size={26} aria-hidden="true" />
                </span>
                <p className="mt-4 text-sm font-semibold text-slate-800">
                  {debouncedQuery || statusFilter !== 'all' ? '条件に一致する図面がありません。' : '図面がありません。PDFまたは画像を登録してください。'}
                </p>
                {!debouncedQuery && statusFilter === 'all' && (
                  <button type="button" className="btn btn-primary mt-4" onClick={() => fileRef.current?.click()} disabled={busy}>
                    <Upload size={16} /> PDF / 図面を登録
                  </button>
                )}
              </div>
            ) : (
              <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {visibleRows.map((row) => {
                  const selected = checked.has(row.id)
                  return (
                    <li
                      key={row.id}
                      className={`group relative flex flex-col overflow-hidden rounded-2xl border bg-white transition duration-200 hover:-translate-y-0.5 hover:shadow-[0_18px_40px_-28px_rgba(15,23,42,0.45)] ${
                        selected ? 'border-blue-400 ring-2 ring-blue-200' : 'border-slate-200 hover:border-slate-300'
                      }`}
                    >
                      <Link
                        to={`/projects/${row.id}`}
                        tabIndex={-1}
                        aria-hidden="true"
                        className="relative block aspect-[4/3] overflow-hidden border-b border-slate-200 bg-slate-100"
                      >
                        <DrawingThumbnail id={row.id} />
                      </Link>
                      <label className="absolute left-3 top-3 flex h-9 w-9 cursor-pointer items-center justify-center rounded-xl bg-white/95 shadow-sm ring-1 ring-slate-200">
                        <input
                          type="checkbox"
                          className="h-5 w-5 rounded border-slate-300 accent-blue-600"
                          aria-label={`${row.name}を選択`}
                          checked={selected}
                          onChange={() => undefined}
                          onClick={(event) => toggle(row.id, event.shiftKey)}
                        />
                      </label>
                      <span className={`tag absolute right-3 top-3 shadow-sm ${STATUS_CLASS[row.status] ?? STATUS_CLASS.draft}`}>
                        {STATUS_LABEL[row.status] ?? row.status}
                      </span>
                      <div className="flex flex-1 flex-col p-4">
                        <div className="font-mono text-[11px] text-slate-500">
                          #{row.id}
                          {row.sheet_no && <> · シート {row.sheet_no}</>}
                          {row.page_no && <> · {row.page_no} 頁</>}
                        </div>
                        <Link
                          to={`/projects/${row.id}`}
                          className="mt-1 line-clamp-2 break-words text-sm font-semibold leading-6 text-slate-900 transition hover:text-blue-700"
                        >
                          {row.name}
                        </Link>
                        <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] font-semibold">
                          <span className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-1 text-violet-700" title="シンボル">
                            <Shapes size={12} aria-hidden="true" /> シンボル {row.symbol_count}
                            {row.prediction_count > 0 && <span className="text-blue-700" title="AI 推論の検出件数">+{row.prediction_count}</span>}
                          </span>
                          <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-1 text-sky-700" title="端子">
                            <CircleDot size={12} aria-hidden="true" /> 端子 {row.terminal_count}
                          </span>
                          <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-1 text-amber-700" title="配線">
                            <Cable size={12} aria-hidden="true" /> 配線 {row.connection_count}
                          </span>
                        </div>
                        <div className="mt-auto flex gap-2 pt-4">
                          <Link to={`/projects/${row.id}/analysis`} className="btn btn-sm flex-1" aria-label={`${row.name}の解析結果`}>
                            <Cable size={14} /> 解析結果
                          </Link>
                          <Link to={`/projects/${row.id}`} className="btn btn-sm btn-primary flex-1" aria-label={`${row.name}を編集`}>
                            編集 <ArrowUpRight size={14} />
                          </Link>
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        )}

        {view === 'list' && (
        <>
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full min-w-[840px]">
            <thead>
              <tr>
                <th className="th w-12"><span className="sr-only">選択</span></th>
                <th className="th w-16">ID</th>
                <th className="th">名称</th>
                <th className="th w-28">シート</th>
                <th className="th w-16">頁</th>
                <th className="th w-24">状態</th>
                <th className="th w-24 text-right">シンボル</th>
                <th className="th w-20 text-right">端子</th>
                <th className="th w-20 text-right">配線</th>
                <th className="th w-28"><span className="sr-only">解析結果・編集</span></th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.length === 0 && (
                <tr>
                  <td className="td py-12 text-center text-slate-500" colSpan={10}>
                    <FileStack size={28} className="mx-auto mb-3 text-blue-300" aria-hidden="true" />
                    {debouncedQuery || statusFilter !== 'all' ? '条件に一致する図面がありません。' : '図面がありません。PDFまたは画像を登録してください。'}
                  </td>
                </tr>
              )}
              {visibleRows.map((row) => (
                <tr key={row.id} className="group transition hover:bg-blue-50/50">
                  <td className="td">
                    <input
                      type="checkbox"
                      className="h-6 w-6 rounded border-slate-300 accent-blue-600"
                      aria-label={`${row.name}を選択`}
                      checked={checked.has(row.id)}
                      onChange={() => undefined}
                      onClick={(event) => toggle(row.id, event.shiftKey)}
                    />
                  </td>
                  <td className="td font-mono text-xs text-slate-400">{row.id}</td>
                  <td className="td">
                    <Link to={`/projects/${row.id}`} className="font-bold text-slate-800 transition hover:text-blue-700">
                      {row.name}
                    </Link>
                  </td>
                  <td className="td font-mono text-xs text-slate-500">{row.sheet_no ?? '—'}</td>
                  <td className="td text-xs">{row.page_no ?? '—'}</td>
                  <td className="td">
                    <span className={`tag ${STATUS_CLASS[row.status] ?? STATUS_CLASS.draft}`}>
                      {STATUS_LABEL[row.status] ?? row.status}
                    </span>
                  </td>
                  <td className="td text-right font-mono text-xs">
                    {row.symbol_count}
                    {row.prediction_count > 0 && (
                      <span className="ml-1 rounded-full bg-blue-50 px-1.5 py-0.5 text-[9px] font-black text-blue-700" title="AI 推論の検出件数">
                        +{row.prediction_count}
                      </span>
                    )}
                  </td>
                  <td className="td text-right font-mono text-xs text-slate-500">{row.terminal_count}</td>
                  <td className="td text-right font-mono text-xs">{row.connection_count}</td>
                  <td className="td text-right">
                    <Link to={`/projects/${row.id}/analysis`} className="icon-button mr-1 h-9 w-9 border border-slate-200 text-blue-700 hover:bg-blue-50" aria-label={`${row.name}の解析結果`} title="解析結果">
                      <Cable size={16} />
                    </Link>
                    <Link
                      to={`/projects/${row.id}`}
                      className="icon-button h-9 w-9 border border-slate-200 text-slate-500 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
                      aria-label={`${row.name}を編集`}
                    >
                      <ArrowUpRight size={16} />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="divide-y divide-slate-100 md:hidden">
          {visibleRows.length === 0 && (
            <div className="px-5 py-12 text-center text-sm text-slate-400">
              {debouncedQuery || statusFilter !== 'all' ? '条件に一致する図面がありません。' : '図面がありません。PDFまたは画像を登録してください。'}
            </div>
          )}
          {visibleRows.map((row) => (
            <article key={row.id} className="p-4">
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-6 w-6 rounded border-slate-300 accent-blue-600"
                  aria-label={`${row.name}を選択`}
                  checked={checked.has(row.id)}
                  onChange={() => toggle(row.id, false)}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[10px] text-slate-400">#{row.id}</span>
                    <span className={`tag ${STATUS_CLASS[row.status] ?? STATUS_CLASS.draft}`}>
                      {STATUS_LABEL[row.status] ?? row.status}
                    </span>
                  </div>
                  <Link to={`/projects/${row.id}`} className="mt-2 block break-words text-sm font-bold leading-6 text-slate-900">
                    {row.name}
                  </Link>
                  <div className="mt-3 grid grid-cols-4 gap-2 rounded-xl bg-slate-50 p-3 text-center">
                    <div><div className="text-[9px] text-slate-400">頁</div><div className="mt-1 text-xs font-bold">{row.page_no ?? '—'}</div></div>
                    <div><div className="text-[9px] text-slate-400">記号</div><div className="mt-1 text-xs font-bold">{row.symbol_count}</div></div>
                    <div><div className="text-[9px] text-slate-400">端子</div><div className="mt-1 text-xs font-bold">{row.terminal_count}</div></div>
                    <div><div className="text-[9px] text-slate-400">配線</div><div className="mt-1 text-xs font-bold">{row.connection_count}</div></div>
                  </div>
                </div>
                <Link to={`/projects/${row.id}/analysis`} className="icon-button border border-slate-200 text-blue-700" aria-label={`${row.name}の解析結果`} title="解析結果">
                  <Cable size={17} />
                </Link>
                <Link
                  to={`/projects/${row.id}`}
                  className="icon-button border border-slate-200 text-slate-500"
                  aria-label={`${row.name}を編集`}
                >
                  <ArrowUpRight size={17} />
                </Link>
              </div>
            </article>
          ))}
        </div>
        </>
        )}
      </section>

      </div>

      {deleteDialogOpen && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-sm">
          <section
            ref={deleteDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-dialog-title"
            tabIndex={-1}
            className="w-full max-w-md rounded-3xl border border-white/60 bg-white p-6 shadow-2xl"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-rose-50 text-rose-600">
              <Trash2 size={22} />
            </div>
            <h2 id="delete-dialog-title" className="mt-4 text-lg font-black text-slate-950">選択した図面を削除しますか？</h2>
            <p className="mt-2 text-sm leading-6 text-slate-500">
              {checked.size}件の図面と、そのアノテーションデータが削除されます。この操作は取り消せません。
            </p>
            {error && (
              <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700" role="alert">
                {error}
              </div>
            )}
            <div className="mt-6 flex justify-end gap-2">
              <button type="button" data-autofocus className="btn" onClick={closeDeleteDialog} disabled={busy}>キャンセル</button>
              <button type="button" className="btn btn-danger" onClick={() => void removeChecked()} disabled={busy}>
                <Trash2 size={15} /> {busy ? '削除中…' : '削除する'}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
