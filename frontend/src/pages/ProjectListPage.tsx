import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUpRight,
  Cable,
  CheckCircle2,
  Download,
  FileStack,
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

export default function ProjectListPage() {
  const [rows, setRows] = useState<ProjectRow[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [q, setQ] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
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
        const ids = rows.map((row) => row.id)
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
      message: 'AI 推論を実行しています',
      hint: '1枚あたり数秒かかります。対象が多い場合はしばらくお待ちください。',
    })
    try {
      const r = await api.runInference(Array.from(checked))
      setMsg(
        `AI 推論が完了しました（${r.detection_count} 件検出）。図面を開くと「AI 推論」ボタンで結果を取り込めます`,
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
      message: 'AI 推論を実行しています',
      hint: '1枚あたり数秒かかります。対象が多い場合はしばらくお待ちください。',
    })
    try {
      const r = await api.runInference(justRegistered)
      setMsg(
        `AI 推論が完了しました（${r.detection_count} 件検出）。図面を開くと検出結果を確認・修正できます`,
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

  const statsCards = stats
    ? [
        { label: '登録図面', value: stats.project_count, unit: '枚', icon: FileStack, color: 'text-cyan-700 bg-cyan-50' },
        { label: 'シンボル', value: stats.symbol_count, unit: '件', icon: Shapes, color: 'text-violet-700 bg-violet-50' },
        { label: '配線', value: stats.connection_count, unit: '件', icon: Cable, color: 'text-amber-700 bg-amber-50' },
        { label: '完了', value: stats.by_status.done ?? 0, unit: '枚', icon: CheckCircle2, color: 'text-emerald-700 bg-emerald-50' },
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
          <h1 className="page-title">図面ワークスペース</h1>
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

      <section className="relative mb-6 overflow-hidden rounded-3xl bg-[#07111f] p-5 text-white shadow-2xl shadow-slate-900/15 sm:p-7">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_12%_20%,rgba(34,211,238,0.25),transparent_28rem),radial-gradient(circle_at_88%_20%,rgba(99,102,241,0.22),transparent_24rem)]" />
        <div className="relative grid gap-6 lg:grid-cols-[1fr_auto] lg:items-center">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.2em] text-cyan-200">
              <Sparkles size={14} /> Page-by-page workflow
            </div>
            <h2 className="mt-3 text-xl font-black tracking-tight sm:text-2xl">大きな図面も、1ページずつ迷わず処理</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">
              複数ページPDFは自動でPNGへ変換します。登録後は図面を開き、前後ページへ連続移動しながら作業できます。
            </p>
          </div>
          <button
            type="button"
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/10 px-5 text-sm font-bold text-white backdrop-blur transition hover:bg-white/15"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
          >
            <Upload size={18} /> ファイルを選択 <ArrowUpRight size={16} />
          </button>
        </div>
      </section>

      {statsCards.length > 0 && (
        <section className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="作業状況">
          {statsCards.map(({ label, value, unit, icon: Icon, color }) => (
            <div key={label} className="stat-card">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">{label}</div>
                  <div className="mt-2 text-2xl font-black tracking-tight text-slate-950 sm:text-3xl">
                    {value}
                    <span className="ml-1 text-xs font-bold text-slate-400">{unit}</span>
                  </div>
                </div>
                <span className={`flex h-10 w-10 items-center justify-center rounded-2xl ${color}`}>
                  <Icon size={19} />
                </span>
              </div>
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
              <Sparkles size={13} /> 登録した {justRegistered.length} 件に AI 推論を実行
            </button>
          )}
        </div>
      )}

      <section className="card overflow-hidden">
        <div className="card-head flex-wrap">
          <div className="min-w-0 flex-1 sm:flex-none">
            <h2 className="text-sm font-black text-slate-900">登録済みの図面</h2>
            <p className="mt-0.5 text-[10px] text-slate-400">チェックはShiftキーで範囲選択できます</p>
          </div>
          <div className="hidden flex-1 lg:block" />
          <label className="relative w-full sm:w-auto">
            <span className="sr-only">図面を検索</span>
            <Search size={15} className="pointer-events-none absolute left-3 top-3 text-slate-400" />
            <input
              className="field w-full pl-9 text-xs sm:w-72"
              placeholder="ID・名称・シート番号で検索"
              value={q}
              onChange={(event) => setQ(event.target.value)}
            />
          </label>
          <button type="button" className="btn btn-sm" onClick={() => void inferChecked()} disabled={busy}>
            <Sparkles size={14} /> {checked.size ? `選択 ${checked.size} 件` : '全件'}に AI 推論
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
        </div>

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
                <th className="th w-20"><span className="sr-only">編集</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td className="td py-14 text-center text-slate-400" colSpan={10}>
                    {debouncedQuery ? '条件に一致する図面がありません。' : '図面がありません。PDFまたは画像を登録してください。'}
                  </td>
                </tr>
              )}
              {rows.map((row) => (
                <tr key={row.id} className="group transition hover:bg-cyan-50/40">
                  <td className="td">
                    <input
                      type="checkbox"
                      className="h-6 w-6 rounded border-slate-300 accent-cyan-600"
                      aria-label={`${row.name}を選択`}
                      checked={checked.has(row.id)}
                      onChange={() => undefined}
                      onClick={(event) => toggle(row.id, event.shiftKey)}
                    />
                  </td>
                  <td className="td font-mono text-xs text-slate-400">{row.id}</td>
                  <td className="td">
                    <Link to={`/projects/${row.id}`} className="font-bold text-slate-800 transition hover:text-cyan-700">
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
                      <span className="ml-1 rounded-full bg-cyan-50 px-1.5 py-0.5 text-[9px] font-black text-cyan-700" title="AI 推論の検出件数">
                        +{row.prediction_count}
                      </span>
                    )}
                  </td>
                  <td className="td text-right font-mono text-xs text-slate-500">{row.terminal_count}</td>
                  <td className="td text-right font-mono text-xs">{row.connection_count}</td>
                  <td className="td text-right">
                    <Link
                      to={`/projects/${row.id}`}
                      className="icon-button h-9 w-9 border border-slate-200 text-slate-500 hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-700"
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
          {rows.length === 0 && (
            <div className="px-5 py-12 text-center text-sm text-slate-400">
              {debouncedQuery ? '条件に一致する図面がありません。' : '図面がありません。PDFまたは画像を登録してください。'}
            </div>
          )}
          {rows.map((row) => (
            <article key={row.id} className="p-4">
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-6 w-6 rounded border-slate-300 accent-cyan-600"
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
