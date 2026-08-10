import { useEffect, useRef, useState } from 'react'
import { Activity, Braces, Download, ListTree, TerminalSquare, Upload } from 'lucide-react'

import { api } from '../api/client'
import { LoadingOverlay } from '../components/LoadingOverlay'
import type { ProjectRow, SymbolClass } from '../types'

export default function ExportPage() {
  const [rows, setRows] = useState<ProjectRow[]>([])
  const [classes, setClasses] = useState<SymbolClass[]>([])
  const [ids, setIds] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [log, setLog] = useState<string[]>([])
  const fileRef = useRef<HTMLInputElement | null>(null)

  const push = (s: string) => setLog((prev) => [`${new Date().toTimeString().slice(0, 8)}  ${s}`, ...prev])

  const reload = async () => {
    try {
      const [nextRows, nextClasses] = await Promise.all([api.listProjects(), api.listClasses()])
      setRows(nextRows)
      setClasses(nextClasses)
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
    try {
      const r = await api.exportBulk({ ids: Array.from(ids) })
      push(`✓ ${r.name} を出力（${(r.size / 1024).toFixed(1)} KB／${ids.size || rows.length} 枚）`)
    } catch (e) {
      push(`× ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const doImport = async (f: File | undefined) => {
    if (!f) return
    setBusy(true)
    try {
      const r = await api.importZip(f)
      push(`✓ ${f.name} を復元（${r.count} 枚：シンボル・端子・配線をすべて復元）`)
      await reload()
    } catch (e) {
      push(`× ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const toggle = (id: number) =>
    setIds((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  return (
    <div className="page-shell enter-up">
      {loading && <LoadingOverlay message="データを読み込んでいます" hint="図面とクラス定義を取得しています。" />}
      <div aria-hidden={loading} inert={loading ? true : undefined}>
      <header className="page-header">
        <div>
          <div className="eyebrow">Data handoff</div>
          <h1 className="page-title">データ受け渡し</h1>
          <p className="page-description">
          YOLO 学習データの出力と、他の担当者が入力したデータの取り込み・復元を行います
          </p>
        </div>
      </header>

      {error && <div className="notice mb-5 border-rose-200 bg-rose-50 text-rose-700" role="alert">{error}</div>}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
        <div className="space-y-5">
          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan-50 text-cyan-700"><Download size={17} /></span>
              <div><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Export</div><h2 className="text-sm font-black">学習データを書き出す</h2></div>
            </div>
            <div className="space-y-4 p-4 sm:p-5">
              <label className="block">
                <span className="mb-1 block text-[11px] text-slate-500">
                  対象（未選択なら全件）— 選択 {ids.size} / {rows.length} 枚
                </span>
                <div className="thin-scroll max-h-52 overflow-auto rounded-2xl border border-slate-200 bg-slate-50/60">
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

          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-700"><Upload size={17} /></span>
              <div><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Import</div><h2 className="text-sm font-black">作業データを復元する</h2></div>
            </div>
            <div className="space-y-4 p-4 sm:p-5">
              <p className="text-xs leading-relaxed text-slate-600">
                他の担当者がこのツールで出力した ZIP を読み込みます。
                <b>bundle.json</b> を含む ZIP から、シンボル・端子・配線・図面情報まで完全に復元します。
              </p>
              <button
                className="btn w-full justify-center"
                onClick={() => fileRef.current?.click()}
                disabled={busy}
              >
                <Upload size={14} /> ZIP を選択して取り込む
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
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-50 text-amber-700"><Braces size={17} /></span>
              <div><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Schema</div><h2 className="text-sm font-black">クラス定義</h2></div>
            </div>
            <div className="p-4">
              <div className="flex flex-wrap gap-1.5">
                {classes.map((c) => (
                  <span
                    key={c.key}
                    className="inline-flex min-h-8 items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px]"
                  >
                    <span className="h-2.5 w-2.5 rounded-sm" style={{ background: c.color }} />
                    <span className="font-mono text-slate-400">{c.yolo_index}</span>
                    <span className="font-mono">{c.key}</span>
                    <span className="text-slate-500">{c.label}</span>
                  </span>
                ))}
              </div>
              <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
                class id は <span className="font-mono">classes.txt</span> の行番号（0 始まり）と一致します。
                インポート時に未知のクラスが現れた場合は末尾に自動採番して登録します。
              </p>
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-100 text-slate-700"><ListTree size={17} /></span>
              <div><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Bundle</div><h2 className="text-sm font-black">ZIPの構成</h2></div>
            </div>
            <pre className="thin-scroll overflow-auto bg-[#07111f] p-5 font-mono text-[11px] leading-6 text-slate-300">
              {`<root>/
├── bundle.json                     完全復元用（このツールの正本）
├── classes.txt                     クラス名（yolo_index 順）
├── data.yaml                       ultralytics 用の設定
├── dataset/
│   ├── images/train/*.png          全画像（val 分割なし）
│   └── labels/train/*.txt          "class_id cx cy w h"（小数6桁）
├── connections/
│   ├── connections.csv             配線 (from-to) の一覧
│   └── netlist.json                from-to を連結したネット
└── README.txt`}
            </pre>
          </div>

          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-50 text-emerald-700"><TerminalSquare size={17} /></span>
              <div><div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Training</div><h2 className="text-sm font-black">学習の実行例</h2></div>
            </div>
            <pre className="overflow-auto bg-slate-950 p-5 font-mono text-[11px] leading-6 text-emerald-200">
              {`unzip seqanno_export_*.zip -d dataset_root
cd dataset_root
yolo detect train data=data.yaml model=yolov8n.pt epochs=100 imgsz=1280`}
            </pre>
            <p className="px-4 pb-4 text-[11px] leading-relaxed text-slate-500">
              配線（from-to）は YOLO の物体検出フォーマットでは表現できないため、
              <span className="font-mono">connections/</span> に別ファイルとして出力します。
              関係抽出モデルの学習や、検出結果の後処理での接続解析に利用してください。
            </p>
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
