import { Link } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  Cable,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CloudUpload,
  Download,
  Save,
  Sparkles,
} from 'lucide-react'

import type { ProjectDetail } from '../../types'
import type { ProjectNavigation } from '../../utils/workspace'
import type { SaveStatus } from './model'

export function EditorHeader({
  project,
  projectId,
  navigation,
  symbolCount,
  connectionCount,
  aiApplying,
  onApplyPredictions,
  exporting,
  onExport,
  saveStatus,
  saveStatusLabel,
  onSave,
}: {
  project: ProjectDetail
  projectId: number
  navigation: ProjectNavigation
  symbolCount: number
  connectionCount: number
  aiApplying: boolean
  onApplyPredictions: () => void
  exporting: boolean
  onExport: () => void
  saveStatus: SaveStatus
  saveStatusLabel: { short: string; long: string }
  onSave: () => void
}) {
  return (
    <header className="flex flex-none flex-wrap items-center gap-2 border-b border-white/10 bg-[#07111f] px-3 py-2.5 shadow-xl shadow-slate-950/20 sm:gap-3 sm:px-4">
      <Link
        to="/"
        className="icon-button border border-white/10 bg-white/[0.06] text-slate-200 hover:bg-white/10 hover:text-white"
        aria-label="図面一覧に戻る"
        title="図面一覧に戻る"
      >
        <ArrowLeft size={19} />
      </Link>

      <div className="min-w-0 flex-1 basis-[180px]">
        <div className="flex items-center gap-2 text-[9px] font-bold uppercase tracking-[0.18em] text-cyan-300">
          Annotation workspace
          {navigation.position > 0 && (
            <span className="rounded-full bg-white/[0.07] px-2 py-0.5 text-slate-400">
              {navigation.position} / {navigation.total}
            </span>
          )}
        </div>
        <h1 className="mt-0.5 truncate text-sm font-bold text-white sm:text-base">{project.name}</h1>
        <div className="mt-0.5 hidden truncate text-[10px] text-slate-400 sm:block">
          {project.sheet_no ? `シート ${project.sheet_no}` : 'シート番号未設定'} · {project.image_width}×
          {project.image_height}px · シンボル {symbolCount} · 配線 {connectionCount}
        </div>
      </div>

      <nav className="flex items-center gap-1 rounded-xl border border-white/10 bg-black/20 p-1" aria-label="図面ページ移動">
        {navigation.previousId ? (
          <Link
            to={`/projects/${navigation.previousId}`}
            className="icon-button h-8 w-8 text-slate-300 hover:bg-white/10 hover:text-white"
            aria-label="前の図面"
            title="前の図面"
          >
            <ChevronLeft size={18} />
          </Link>
        ) : (
          <span className="icon-button h-8 w-8 text-slate-700" aria-hidden="true">
            <ChevronLeft size={18} />
          </span>
        )}
        <span className="min-w-12 text-center text-[10px] font-bold text-slate-400">
          {navigation.position || '—'} / {navigation.total || '—'}
        </span>
        {navigation.nextId ? (
          <Link
            to={`/projects/${navigation.nextId}`}
            className="icon-button h-8 w-8 text-slate-300 hover:bg-white/10 hover:text-white"
            aria-label="次の図面"
            title="次の図面"
          >
            <ChevronRight size={18} />
          </Link>
        ) : (
          <span className="icon-button h-8 w-8 text-slate-700" aria-hidden="true">
            <ChevronRight size={18} />
          </span>
        )}
      </nav>

      <Link to={`/projects/${projectId}/analysis`} className="icon-button border border-white/10 text-slate-300 hover:bg-white/10 hover:text-white" title="解析結果を表示" aria-label="解析結果を表示">
        <Cable size={17} />
      </Link>
      <button
        type="button"
        className="flex min-h-10 items-center gap-1.5 rounded-xl border border-cyan-400/30 bg-cyan-400/10 px-3 text-xs font-bold text-cyan-100 transition hover:bg-cyan-400/20 disabled:opacity-40"
        onClick={onApplyPredictions}
        disabled={aiApplying}
        aria-busy={aiApplying}
        title="保存済みの AI 推論結果を編集用シンボルとして取り込みます"
      >
        <Sparkles className={aiApplying ? 'animate-pulse' : undefined} size={15} />
        AI 推論
      </button>
      <button
        type="button"
        className="icon-button border border-white/10 bg-white/[0.06] text-slate-300 hover:bg-white/10 hover:text-white"
        onClick={onExport}
        disabled={exporting}
        aria-busy={exporting}
        aria-label={exporting ? 'この図面を出力中' : 'この図面を出力'}
        title={exporting ? 'この図面を出力中' : 'この図面を出力'}
      >
        <Download className={exporting ? 'animate-pulse' : undefined} size={17} />
      </button>
      <button
        type="button"
        className={`flex min-h-10 items-center gap-2 rounded-xl border px-3 text-xs font-bold transition ${
          saveStatus === 'error'
            ? 'border-rose-400/40 bg-rose-400/10 text-rose-200 hover:bg-rose-400/20'
            : saveStatus === 'saved'
              ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
              : 'border-cyan-400/30 bg-cyan-400/10 text-cyan-100'
        }`}
        onClick={onSave}
        aria-live="polite"
        aria-label={saveStatusLabel.long}
        title={saveStatusLabel.long}
      >
        {saveStatus === 'saving' ? (
          <CloudUpload className="animate-pulse" size={16} />
        ) : saveStatus === 'error' ? (
          <AlertCircle size={16} />
        ) : saveStatus === 'saved' ? (
          <CheckCircle2 size={16} />
        ) : (
          <Save size={16} />
        )}
        <span className="text-[10px] sm:hidden">{saveStatusLabel.short}</span>
        <span className="hidden sm:inline">{saveStatusLabel.long}</span>
      </button>
    </header>
  )
}
