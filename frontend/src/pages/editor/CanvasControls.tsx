import { Eye, EyeOff, Maximize2, PanelRightOpen, ZoomIn, ZoomOut } from 'lucide-react'

import { MODES } from './model'
import type { Mode } from './model'

export function ModeBadge({ mode }: { mode: Mode }) {
  const activeMode = MODES.find((item) => item.id === mode) ?? MODES[0]
  const ActiveIcon = activeMode.icon
  return (
    <div className="pointer-events-none absolute left-3 top-3 z-10 flex items-center gap-2 rounded-xl border border-white/70 bg-white/85 px-3 py-2 text-xs font-bold text-slate-700 shadow-lg shadow-slate-900/10 backdrop-blur sm:left-4 sm:top-4">
      <ActiveIcon size={15} className="text-cyan-700" />
      <span>{activeMode.label}</span>
      <span className="kbd">{activeMode.key}</span>
    </div>
  )
}

export function CanvasControls({
  mode,
  inspectorOpen,
  onOpenInspector,
  zoom,
  zoomAt,
  fit,
  shortcutsVisible,
  onToggleShortcuts,
}: {
  mode: Mode
  inspectorOpen: boolean
  onOpenInspector: () => void
  zoom: number
  zoomAt: (factor: number, cx: number, cy: number) => void
  fit: (preferReadableScale?: boolean) => void
  shortcutsVisible: boolean
  onToggleShortcuts: () => void
}) {
  return (
    <>
      {!inspectorOpen && (
        <button
          type="button"
          className="absolute right-3 top-3 z-10 flex min-h-10 items-center gap-2 rounded-xl border border-white/70 bg-white/90 px-3 text-xs font-bold text-slate-700 shadow-lg shadow-slate-900/10 backdrop-blur lg:hidden"
          onClick={onOpenInspector}
          aria-controls="annotation-inspector"
        >
          <PanelRightOpen size={16} /> 詳細
        </button>
      )}
      <ModeBadge mode={mode} />
      <div className="absolute bottom-3 left-3 z-20 flex items-center gap-1 rounded-2xl border border-white/70 bg-white/90 p-1.5 text-slate-700 shadow-xl shadow-slate-900/15 backdrop-blur sm:bottom-4 sm:left-4">
        <button
          type="button"
          className="icon-button h-9 w-9 hover:bg-slate-100"
          onClick={() => zoomAt(1 / 1.3, window.innerWidth / 2, window.innerHeight / 2)}
          aria-label="図面を縮小"
          title="図面を縮小"
        >
          <ZoomOut size={17} />
        </button>
        <button
          type="button"
          className="flex min-h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs font-bold hover:bg-slate-100"
          onClick={() => fit(false)}
          title="図面全体を表示"
        >
          <Maximize2 size={15} />
          <span className="hidden sm:inline">全体</span>
        </button>
        <button
          type="button"
          className="icon-button h-9 w-9 hover:bg-slate-100"
          onClick={() => zoomAt(1.3, window.innerWidth / 2, window.innerHeight / 2)}
          aria-label="図面を拡大"
          title="図面を拡大"
        >
          <ZoomIn size={17} />
        </button>
        <span className="min-w-12 border-l border-slate-200 pl-2 text-center font-mono text-[11px] font-bold text-slate-500">
          {Math.round(zoom * 100)}%
        </span>
      </div>

      <div className="absolute bottom-3 right-3 z-20 sm:bottom-4 sm:right-4">
        {shortcutsVisible && (
          <div className="mb-2 max-w-[calc(100vw-1.5rem)] rounded-2xl border border-white/30 bg-slate-950/95 p-3 text-[11px] font-semibold leading-5 text-white shadow-2xl backdrop-blur sm:max-w-sm">
            <div className="mb-2 font-black text-white">図面操作</div>
            ホイールで拡大縮小 ／ Alt+ドラッグまたは空白ドラッグで移動
            <div className="mt-2 flex flex-wrap gap-1.5">
              <span className="kbd">B 描画</span>
              <span className="kbd">T 端子</span>
              <span className="kbd">C 配線</span>
              <span className="kbd">V 選択</span>
              <span className="kbd">Del 削除</span>
              <span className="kbd">Ctrl+S 即時保存</span>
            </div>
          </div>
        )}
        <button
          type="button"
          className="flex min-h-10 items-center gap-2 rounded-xl border border-slate-300 bg-white px-3 text-xs font-black text-slate-950 shadow-lg shadow-slate-900/15 backdrop-blur hover:bg-slate-50"
          onClick={onToggleShortcuts}
          aria-expanded={shortcutsVisible}
        >
          {shortcutsVisible ? <EyeOff size={15} /> : <Eye size={15} />}
          操作ヒント
        </button>
      </div>
    </>
  )
}
