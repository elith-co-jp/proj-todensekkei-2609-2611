import { Layers, PanelRightClose, PanelRightOpen, Redo2, Undo2 } from 'lucide-react'

import type { SymbolClass } from '../../types'
import { MODES } from './model'
import type { Mode } from './model'

export function EditorToolbar({
  mode,
  onModeChange,
  classKey,
  onClassKeyChange,
  classes,
  colorOf,
  layersOpen,
  onToggleLayers,
  hiddenAnnotationCount,
  histVersion,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  inspectorOpen,
  onToggleInspector,
}: {
  mode: Mode
  onModeChange: (mode: Mode) => void
  classKey: string
  onClassKeyChange: (key: string) => void
  classes: SymbolClass[]
  colorOf: (key: string) => string
  layersOpen: boolean
  onToggleLayers: () => void
  hiddenAnnotationCount: number
  histVersion: number
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  inspectorOpen: boolean
  onToggleInspector: () => void
}) {
  return (
    <div className="thin-scroll flex flex-none items-center gap-2 overflow-x-auto border-b border-white/10 bg-slate-900 px-3 py-2 sm:px-4">
      <div role="toolbar" aria-label="アノテーションモード" className="flex items-center gap-1.5">
        {MODES.map(({ id, label, key, icon: Icon }) => (
          <button
            type="button"
            key={id}
            id={`annotation-mode-${id}`}
            onClick={() => onModeChange(id)}
            onKeyDown={(event) => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
              event.preventDefault()
              const currentIndex = MODES.findIndex((item) => item.id === id)
              const nextIndex =
                event.key === 'Home'
                  ? 0
                  : event.key === 'End'
                    ? MODES.length - 1
                    : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + MODES.length) % MODES.length
              const nextMode = MODES[nextIndex].id
              onModeChange(nextMode)
              window.requestAnimationFrame(() => document.getElementById(`annotation-mode-${nextMode}`)?.focus())
            }}
            aria-pressed={mode === id}
            aria-label={`${label}（${key}キー）`}
            tabIndex={mode === id ? 0 : -1}
            className={`inline-flex min-h-10 items-center gap-2 whitespace-nowrap rounded-xl border px-3 text-xs font-bold transition ${
              mode === id
                ? 'border-cyan-300 bg-cyan-300 text-slate-950 shadow-lg shadow-cyan-400/20'
                : 'border-white/10 bg-white/[0.04] text-slate-300 hover:border-white/20 hover:bg-white/[0.08] hover:text-white'
            }`}
          >
            <Icon size={16} />
            <span className="hidden md:inline">{label}</span>
            <span className={`kbd border-0 ${mode === id ? 'bg-slate-950/10 text-slate-800' : 'bg-black/25 text-slate-400'}`}>
              {key}
            </span>
          </button>
        ))}
      </div>

      <span className="mx-1 h-7 w-px flex-none bg-white/10" />
      <label className="flex items-center gap-2 whitespace-nowrap text-[11px] font-bold text-slate-300">
        シンボル種別
        <span
          className="h-3.5 w-3.5 flex-none rounded-full ring-2 ring-white/15"
          style={{ background: colorOf(classKey) }}
        />
        <select
          className="min-h-10 w-48 rounded-xl border border-white/10 bg-white/[0.06] px-3 text-xs font-semibold text-white outline-none transition focus:border-cyan-400 md:w-60"
          value={classKey}
          onChange={(event) => onClassKeyChange(event.target.value)}
        >
          {classes.map((item, index) => (
            <option key={item.key} value={item.key} className="bg-slate-900 text-white">
              {index < 9 ? `${index + 1}. ` : ''}
              {item.label}（{item.key}）
            </option>
          ))}
        </select>
      </label>

      <span className="mx-1 h-7 w-px flex-none bg-white/10" />
      <button
        type="button"
        className={`inline-flex min-h-10 items-center gap-2 whitespace-nowrap rounded-xl border px-3 text-xs font-bold transition ${
          layersOpen
            ? 'border-cyan-400/40 bg-cyan-400/10 text-cyan-100 hover:bg-cyan-400/20'
            : 'border-white/10 bg-white/[0.04] text-slate-300 hover:border-white/20 hover:bg-white/[0.08] hover:text-white'
        }`}
        onClick={onToggleLayers}
        aria-expanded={layersOpen}
        aria-controls="annotation-layers"
        title="アノテーションの種類ごとに表示を切り替えます"
      >
        <Layers size={16} />
        <span className="hidden md:inline">レイヤー</span>
        {hiddenAnnotationCount > 0 && (
          <span
            className="rounded-full bg-amber-400/20 px-1.5 font-mono text-[10px] text-amber-200"
            title={`表示をオフにしたアノテーション ${hiddenAnnotationCount} 件`}
          >
            {hiddenAnnotationCount}
          </span>
        )}
      </button>

      <div className="flex-1" />
      <div className="flex items-center gap-1" role="group" aria-label={`編集履歴 ${histVersion}`}>
        <button
          type="button"
          className="icon-button text-slate-400 hover:bg-white/10 hover:text-white"
          onClick={onUndo}
          disabled={!canUndo}
          aria-label="元に戻す"
          title="元に戻す（Ctrl+Z）"
        >
          <Undo2 size={17} />
        </button>
        <button
          type="button"
          className="icon-button text-slate-400 hover:bg-white/10 hover:text-white"
          onClick={onRedo}
          disabled={!canRedo}
          aria-label="やり直す"
          title="やり直す（Ctrl+Shift+Z）"
        >
          <Redo2 size={17} />
        </button>
      </div>
      <button
        type="button"
        className={`icon-button hidden border lg:inline-flex ${
          inspectorOpen
            ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-200'
            : 'border-white/10 bg-white/[0.04] text-slate-400 hover:bg-white/10 hover:text-white'
        }`}
        onClick={onToggleInspector}
        aria-expanded={inspectorOpen}
        aria-controls="annotation-inspector"
        aria-label={inspectorOpen ? '詳細パネルを閉じる' : '詳細パネルを開く'}
        title={inspectorOpen ? '詳細パネルを閉じる' : '詳細パネルを開く'}
      >
        {inspectorOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
      </button>
    </div>
  )
}
