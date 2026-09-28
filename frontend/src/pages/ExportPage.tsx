import { useEffect, useRef, useState } from 'react'
import { Activity, CheckCircle2, Download, Upload, XCircle } from 'lucide-react'

import { api } from '../api/client'
import { LoadingOverlay } from '../components/LoadingOverlay'
import type { ProjectRow } from '../types'

type ImportNotice = {
  kind: 'success' | 'error'
  title: string
  body: string
}

export default function ExportPage() {
  const [rows, setRows] = useState<ProjectRow[]>([])
  const [ids, setIds] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [busyLabel, setBusyLabel] = useState<{ message: string; hint: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [dropActive, setDropActive] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [importNotice, setImportNotice] = useState<ImportNotice | null>(null)
  const [log, setLog] = useState<string[]>([])
  const fileRef = useRef<HTMLInputElement | null>(null)

  const push = (s: string) => setLog((prev) => [`${new Date().toTimeString().slice(0, 8)}  ${s}`, ...prev])
  const notifyImport = (notice: ImportNotice) => setImportNotice(notice)

  const reload = async () => {
    try {
      const nextRows = await api.listProjects()
      setRows(nextRows)
      setError(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    void reload()
  }, [])

  const doExport = async () => {
    setBusy(true)
    setBusyLabel({
      message: 'ZIP を作成しています',
      hint: '図面とアノテーションデータをまとめています。',
    })
    try {
      const r = await api.exportBulk({ ids: Array.from(ids) })
      push(`✓ ${r.name} を出力（${(r.size / 1024).toFixed(1)} KB／${ids.size || rows.length} 枚）`)
    } catch (e) {
      push(`× ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
      setBusyLabel(null)
    }
  }

  const doImport = async (f: File | undefined) => {
    if (!f) return
    if (!isZipFile(f)) {
      push('× ZIP ファイルを選択してください')
      notifyImport({
        kind: 'error',
        title: '読み込みできませんでした',
        body: 'ZIP ファイルを選択してください。',
      })
      return
    }
    if (!(await containsBundleJson(f))) {
      push('× bundle.json を含む ZIP ではありません（このツールで書き出した ZIP を読み込んでください）')
      notifyImport({
        kind: 'error',
        title: '読み込みできませんでした',
        body: 'bundle.json を含む ZIP ではありません。このツールで書き出した ZIP を読み込んでください。',
      })
      return
    }
    setImportNotice(null)
    setBusy(true)
    setBusyLabel({
      message: 'ZIP を読み込んで復元しています',
      hint: '図面とアノテーションデータを復元しています。',
    })
    try {
      const r = await api.importZip(f)
      push(`✓ ${f.name} を復元（${r.count} 枚：シンボル・端子・配線をすべて復元）`)
      notifyImport({
        kind: 'success',
        title: '読み込みが完了しました',
        body: `${f.name} を読み込みました（${r.count} 枚）。図面ワークスペースで内容を確認してください。`,
      })
      await reload()
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      push(`× ${message}`)
      notifyImport({
        kind: 'error',
        title: '読み込みに失敗しました',
        body: message,
      })
    } finally {
      setBusy(false)
      setBusyLabel(null)
    }
  }

  const toggle = (id: number) =>
    setIds((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const selectAll = () => setIds(new Set(rows.map((row) => row.id)))
  const clearSelection = () => setIds(new Set())

  const handleDrop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDropActive(false)
    if (busy) return
    const zip = await findZipFile(event.dataTransfer)
    if (!zip) {
      push('× ZIP ファイルが見つかりません')
      notifyImport({
        kind: 'error',
        title: '読み込みできませんでした',
        body: 'ZIP ファイルが見つかりません。',
      })
      return
    }
    await doImport(zip)
  }

  return (
    <div className="page-shell enter-up">
      {loading && !busyLabel && (
        <LoadingOverlay message="データを読み込んでいます" hint="図面一覧を取得しています。" />
      )}
      {busyLabel && <LoadingOverlay message={busyLabel.message} hint={busyLabel.hint} />}
      <div aria-hidden={loading || busyLabel !== null} inert={loading || busyLabel !== null ? true : undefined}>
      <header className="page-header">
        <div>
          <div className="eyebrow">Data handoff</div>
          <h1 className="page-title">データ受け渡し</h1>
          <p className="page-description">
            入力したシンボル・端子・配線情報の書き出しと、このツールで書き出したZIPの読み込みを行います
          </p>
        </div>
      </header>

      <section className="notice mb-5 border-cyan-200 bg-cyan-50 text-cyan-900">
        <Activity size={18} className="mt-0.5 flex-none text-cyan-700" />
        <div className="grid min-w-0 gap-3 text-[12px] leading-6 sm:grid-cols-2">
          <div>
            <div className="font-black text-slate-900">書き出し</div>
            <p>選択した図面、または全図面のアノテーションデータをZIPでダウンロードします。</p>
          </div>
          <div>
            <div className="font-black text-slate-900">読み込み</div>
            <p>このツールで書き出したZIPから、含まれているアノテーションデータを復元します。</p>
          </div>
          <p className="sm:col-span-2">
            PDFや任意のZIPから自動でアノテーションを作成する機能ではありません。同じZIPを複数回取り込むと図面が重複します。インポート前に一覧を確認してください。
          </p>
        </div>
      </section>

      {error && <div className="notice mb-5 border-rose-200 bg-rose-50 text-rose-700" role="alert">{error}</div>}

      {importNotice && (
        <div
          className={`notice mb-5 ${
            importNotice.kind === 'success'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
              : 'border-rose-200 bg-rose-50 text-rose-700'
          }`}
          role={importNotice.kind === 'success' ? 'status' : 'alert'}
        >
          {importNotice.kind === 'success' ? (
            <CheckCircle2 size={18} className="mt-0.5 flex-none" />
          ) : (
            <XCircle size={18} className="mt-0.5 flex-none" />
          )}
          <div className="min-w-0 flex-1">
            <div className="text-sm font-black">{importNotice.title}</div>
            <div className="mt-0.5 text-[12px] leading-5">{importNotice.body}</div>
          </div>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setImportNotice(null)}
          >
            閉じる
          </button>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <div className="space-y-5">
          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan-50 text-cyan-700"><Download size={17} /></span>
              <div><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Export</div><h2 className="text-sm font-black">作業データを書き出す</h2></div>
            </div>
            <div className="space-y-4 p-4 sm:p-5">
              <label className="block">
                <span className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                  <span>対象（未選択なら全件）— 選択 {ids.size} / {rows.length} 枚</span>
                  <span className="flex-1" />
                  <button type="button" className="btn btn-sm" onClick={selectAll} disabled={busy || rows.length === 0 || ids.size === rows.length}>
                    全選択
                  </button>
                  <button type="button" className="btn btn-sm" onClick={clearSelection} disabled={busy || ids.size === 0}>
                    選択解除
                  </button>
                </span>
                <div className="thin-scroll max-h-[min(56vh,36rem)] overflow-auto rounded-2xl border border-slate-200 bg-slate-50/60">
                  {rows.map((r) => (
                    <label
                      key={r.id}
                      className="flex min-h-10 items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs last:border-0 hover:bg-white"
                    >
                      <input className="h-6 w-6 flex-none" type="checkbox" checked={ids.has(r.id)} onChange={() => toggle(r.id)} />
                      <span className="font-mono text-slate-400">{r.id}</span>
                      <span className="truncate">{r.name}</span>
                      <span className="flex-1" />
                      <span className="font-mono text-slate-400">
                        {r.symbol_count} / {r.connection_count}
                      </span>
                    </label>
                  ))}
                  {rows.length === 0 && (
                    <div className="px-3 py-4 text-center text-xs text-slate-400">図面がありません</div>
                  )}
                </div>
              </label>
              <button
                className="btn btn-accent w-full justify-center"
                onClick={() => void doExport()}
                disabled={busy}
              >
                <Download size={14} /> ZIP をダウンロード
              </button>
            </div>
          </div>

        </div>

        <div className="space-y-5">
          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-700"><Upload size={17} /></span>
              <div><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Import</div><h2 className="text-sm font-black">作業データを復元する</h2></div>
            </div>
            <div
              className="space-y-4 p-4 sm:p-5"
              onDragEnter={(event) => {
                event.preventDefault()
                if (!busy) setDropActive(true)
              }}
              onDragOver={(event) => {
                event.preventDefault()
                if (!busy) {
                  event.dataTransfer.dropEffect = 'copy'
                  setDropActive(true)
                }
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropActive(false)
              }}
              onDrop={(event) => {
                void handleDrop(event)
              }}
            >
              <p className="text-xs leading-relaxed text-slate-600">
                このツールで書き出した ZIP を読み込みます。
                <b>bundle.json</b> を含む ZIP から、含まれているアノテーションデータを復元します。
              </p>
              <div
                className={`rounded-2xl border border-dashed px-4 py-6 text-center transition ${
                  dropActive
                    ? 'border-violet-400 bg-violet-50 text-violet-800'
                    : 'border-slate-300 bg-slate-50 text-slate-500'
                }`}
                aria-label="ZIPファイルのドラッグアンドドロップ"
              >
                <Upload className="mx-auto mb-2" size={22} />
                <div className="text-xs font-black text-slate-800">ZIPファイルをここにドラッグ&ドロップ</div>
                <div className="mt-1 text-[11px] leading-5">
                  フォルダをドロップした場合は、中にあるZIPファイルを探して読み込みます。
                </div>
              </div>
              <button
                className="btn w-full justify-center"
                onClick={() => fileRef.current?.click()}
                disabled={busy}
              >
                <Upload size={14} /> ZIP を選択して読み込む
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".zip,application/zip"
                hidden
                onChange={(e) => {
                  void doImport(e.target.files?.[0])
                  e.target.value = ''
                }}
              />
            </div>
          </div>

          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-700"><Activity size={17} /></span>
              <div><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Activity</div><h2 className="text-sm font-black">実行ログ</h2></div>
            </div>
            <div
              className="thin-scroll max-h-64 overflow-auto p-4"
              role="log"
              aria-live="polite"
              aria-relevant="additions text"
            >
              {log.length === 0 && <div className="text-xs text-slate-400">まだ操作していません</div>}
              {log.map((l, i) => (
                <div key={i} className="border-b border-slate-100 py-1 font-mono text-[11px] last:border-0">
                  {l}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      </div>
    </div>
  )
}

type WebkitFileEntry = {
  isFile: boolean
  isDirectory: boolean
  file?: (success: (file: File) => void, failure?: (error: DOMException) => void) => void
  createReader?: () => {
    readEntries: (success: (entries: WebkitFileEntry[]) => void, failure?: (error: DOMException) => void) => void
  }
}

type DataTransferItemWithEntry = DataTransferItem & {
  webkitGetAsEntry?: () => WebkitFileEntry | null
}

function isZipFile(file: File) {
  return file.name.toLowerCase().endsWith('.zip') || file.type === 'application/zip' || file.type === 'application/x-zip-compressed'
}

async function findZipFile(dataTransfer: DataTransfer): Promise<File | undefined> {
  const directZip = Array.from(dataTransfer.files).find(isZipFile)
  if (directZip) return directZip

  const entries: WebkitFileEntry[] = []
  for (const item of Array.from(dataTransfer.items ?? [])) {
    const entry = (item as DataTransferItemWithEntry).webkitGetAsEntry?.()
    if (entry) entries.push(entry)
  }

  for (const entry of entries) {
    const files = await filesFromEntry(entry)
    const zip = files.find(isZipFile)
    if (zip) return zip
  }
  return undefined
}

async function containsBundleJson(file: File) {
  try {
    const bytes = new Uint8Array(await file.arrayBuffer())
    return containsAscii(bytes, 'bundle.json')
  } catch {
    return false
  }
}

function containsAscii(bytes: Uint8Array, text: string) {
  const needle = new TextEncoder().encode(text)
  outer: for (let i = 0; i <= bytes.length - needle.length; i += 1) {
    for (let j = 0; j < needle.length; j += 1) {
      if (bytes[i + j] !== needle[j]) continue outer
    }
    return true
  }
  return false
}

async function filesFromEntry(entry: WebkitFileEntry): Promise<File[]> {
  if (entry.isFile && entry.file) {
    return new Promise((resolve) => entry.file?.((file) => resolve([file]), () => resolve([])))
  }
  if (!entry.isDirectory || !entry.createReader) return []

  const reader = entry.createReader()
  const entries = await readDirectoryEntries(reader)
  const nested = await Promise.all(entries.map(filesFromEntry))
  return nested.flat()
}

async function readDirectoryEntries(reader: ReturnType<NonNullable<WebkitFileEntry['createReader']>>) {
  const entries: WebkitFileEntry[] = []
  for (;;) {
    const batch = await new Promise<WebkitFileEntry[]>((resolve) => reader.readEntries(resolve, () => resolve([])))
    if (batch.length === 0) break
    entries.push(...batch)
  }
  return entries
}
