import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Cable, ChevronLeft, ChevronRight, Download, Pencil, Sparkles, Square } from 'lucide-react'

import { api } from '../api/client'
import { LoadingOverlay } from '../components/LoadingOverlay'
import PredictionDrawing from '../components/PredictionDrawing'
import type { ProjectDetail, ProjectPredictions, ProjectRow, StructureResult } from '../types'
import { getProjectNavigation } from '../utils/workspace'

type PageData = {
  project: ProjectDetail
  predictions: ProjectPredictions
  structure: StructureResult | null
  projects: ProjectRow[]
}

export default function AnalysisPage() {
  const projectId = Number(useParams().id)
  const [data, setData] = useState<PageData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [showSymbols, setShowSymbols] = useState(true)
  const [showWires, setShowWires] = useState(true)
  const [wireOpacity, setWireOpacity] = useState(0.45)
  const [notice, setNotice] = useState<string | null>(null)
  const generation = useRef(0)

  async function fetchPage(id: number): Promise<PageData> {
    const [project, predictions, result, projects] = await Promise.all([
      api.getProject(id), api.getPredictions(id), api.getStructure(id), api.listProjects(),
    ])
    return { project, predictions, structure: result.result, projects }
  }

  useEffect(() => {
    const request = ++generation.current
    setData(null)
    setError(null)
    setNotice(null)
    setBusy(false)
    setDownloading(false)
    void fetchPage(projectId).then((result) => {
      if (request === generation.current) setData(result)
    }).catch((caught) => {
      if (request === generation.current) setError(String(caught instanceof Error ? caught.message : caught))
    })
    return () => { generation.current++ }
  }, [projectId])

  const run = async () => {
    const request = generation.current
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await api.runAnalysis([projectId])
      const result = await fetchPage(projectId)
      if (request !== generation.current) return
      setData(result)
      setNotice('解析結果を保存しました')
    } catch (caught) {
      if (request === generation.current) setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (request === generation.current) setBusy(false)
    }
  }

  const download = async () => {
    const request = generation.current
    setDownloading(true)
    try {
      await api.exportStructure([projectId])
    } catch (caught) {
      if (request === generation.current) setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (request === generation.current) setDownloading(false)
    }
  }

  const current = data?.project.id === projectId ? data : null
  if (!current) return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6">
      <p role={error ? 'alert' : 'status'}>{error || '解析結果を読み込んでいます'}</p>
      <Link to="/" className="btn"><ArrowLeft size={16} />図面一覧</Link>
    </div>
  )
  const { project, predictions, structure, projects } = current
  const navigation = getProjectNavigation(projects, projectId)

  return (
    <div className="flex h-dvh min-h-[480px] flex-col bg-white text-slate-800">
      {busy && <LoadingOverlay message="図面を解析しています" hint="シンボル・配線・接続関係を処理しています。" />}
      <div className="flex min-h-0 flex-1 flex-col" aria-hidden={busy} inert={busy ? true : undefined}>
        <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-4 py-3">
          <Link to="/" className="icon-button flex-none" title="図面一覧に戻る" aria-label="図面一覧に戻る"><ArrowLeft size={19} /></Link>
          <div className="min-w-0 flex-1 basis-40">
            <h1 className="text-sm font-bold">解析結果</h1>
            <p className="mt-0.5 break-all text-xs text-slate-500">{project.name}</p>
          </div>
          <nav className="flex items-center gap-1" aria-label="図面の移動">
            {navigation.previousId ? <Link className="icon-button" to={`/projects/${navigation.previousId}/analysis`} title="前の図面" aria-label="前の図面"><ChevronLeft size={18} /></Link> : <span className="inline-block w-9" />}
            <span className="min-w-12 text-center font-mono text-xs">{navigation.position}/{navigation.total}</span>
            {navigation.nextId ? <Link className="icon-button" to={`/projects/${navigation.nextId}/analysis`} title="次の図面" aria-label="次の図面"><ChevronRight size={18} /></Link> : <span className="inline-block w-9" />}
          </nav>
          <Link to={`/projects/${projectId}`} className="icon-button" title="アノテーション編集" aria-label="アノテーション編集"><Pencil size={17} /></Link>
          <button type="button" className="btn btn-accent" onClick={() => void run()} disabled={busy}><Sparkles size={16} />{structure ? '再解析' : '解析を実行'}</button>
          <button type="button" className="btn" onClick={() => void download()} disabled={!structure || downloading}><Download size={16} />構造JSON</button>
        </header>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs">
          <label className="flex items-center gap-2 font-semibold">
            <input type="checkbox" checked={showSymbols} onChange={(e) => setShowSymbols(e.target.checked)} className="h-4 w-4 accent-cyan-600" />
            <Square size={14} className="text-cyan-600" />シンボル
            <span className="font-mono font-normal text-slate-500">{predictions.count}</span>
          </label>
          <label className="flex items-center gap-2 font-semibold">
            <input type="checkbox" checked={showWires} onChange={(e) => setShowWires(e.target.checked)} className="h-4 w-4 accent-rose-500" disabled={!structure} />
            <Cable size={14} className="text-rose-500" />配線
            <span className="font-mono font-normal text-slate-500">{structure?.wires.length ?? '-'}</span>
          </label>
          <label className="flex items-center gap-2 text-slate-600">
            配線の濃さ
            <input aria-label="配線の濃さ" type="range" min={10} max={90} step={5} value={Math.round(wireOpacity * 100)} onChange={(e) => setWireOpacity(Number(e.target.value) / 100)} disabled={!showWires || !structure} className="w-24 accent-rose-500 sm:w-32" />
            <span className="w-8 font-mono tabular-nums">{Math.round(wireOpacity * 100)}%</span>
          </label>
          <span className="ml-auto text-slate-500">{structure ? `接続 ${structure.from_to.connection_count} 件` : predictions.count ? 'シンボルのみ解析済み' : '未解析'}</span>
        </div>
        {error && <p role="alert" className="border-b border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-800">{error}</p>}
        {notice && <p role="status" className="border-b border-emerald-200 bg-emerald-50 px-4 py-2 text-xs text-emerald-800">{notice}</p>}
        {structure?.warnings.map((warning) => <p key={warning} role="status" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">{warning}</p>)}
        <PredictionDrawing key={project.id} imageUrl={api.imageUrl(projectId)} name={project.name} width={project.image_width} height={project.image_height} detections={predictions.detections} structure={structure} showSymbols={showSymbols} showWires={showWires} wireOpacity={wireOpacity} />
      </div>
    </div>
  )
}
