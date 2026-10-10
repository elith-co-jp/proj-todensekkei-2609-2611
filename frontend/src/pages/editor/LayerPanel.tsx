import { Cable, CircleDot, Eye, EyeOff, Sparkles, Square } from 'lucide-react'

import { layerStateOf } from './model'
import type { LayerState } from './model'

export type ClassLayerRow = {
  key: string
  label: string
  color: string
  total: number
  hidden: number
  refs: string[]
}

export function LayerPanel({
  connectionCount,
  terminalCount,
  inferenceRefs,
  hiddenInferenceCount,
  classLayerRows,
  showConnections,
  showTerminals,
  allLayersVisible,
  onToggleConnections,
  onToggleTerminals,
  onSetSymbolGroupHidden,
  onReset,
}: {
  connectionCount: number
  terminalCount: number
  inferenceRefs: string[]
  hiddenInferenceCount: number
  classLayerRows: ClassLayerRow[]
  showConnections: boolean
  showTerminals: boolean
  allLayersVisible: boolean
  onToggleConnections: () => void
  onToggleTerminals: () => void
  onSetSymbolGroupHidden: (refs: string[], hidden: boolean) => void
  onReset: () => void
}) {
  return (
    <div
      id="annotation-layers"
      className="absolute right-3 top-14 z-10 flex max-h-[calc(100%-8rem)] w-64 flex-col overflow-hidden rounded-2xl border border-white/70 bg-white/95 text-slate-800 shadow-xl shadow-slate-900/15 backdrop-blur sm:right-4 lg:top-4"
      onPointerDown={(event) => event.stopPropagation()}
      onWheel={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        // キャンバスの編集処理（矩形/端子/配線の確定・選択枠の移動/削除）へ
        // 流すとパネル操作中に図面が変わるため、それらのキーは止める。
        // それ以外のキー（Undo・保存・モード切替等）は編集ショートカットとして
        // 通し、パネルにフォーカスがあっても使えるようにする
        const blocked =
          event.key === 'Enter' ||
          event.key === 'Delete' ||
          event.key === 'Backspace' ||
          event.key.startsWith('Arrow')
        if (blocked) event.stopPropagation()
      }}
    >
      <div className="flex flex-none items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
        <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500">
          Layers
        </span>
        <button
          type="button"
          className="min-h-8 rounded-lg px-2 text-[11px] font-bold text-cyan-700 hover:bg-cyan-50 disabled:text-slate-300"
          onClick={onReset}
          disabled={allLayersVisible}
        >
          すべて表示
        </button>
      </div>
      <div
        className="thin-scroll flex-1 overflow-auto p-1.5"
        role="group"
        aria-label="アノテーションのレイヤー"
      >
        <LayerRow
          icon={Cable}
          color="#7c3aed"
          label="配線"
          count={connectionCount}
          state={showConnections ? 'on' : 'off'}
          onToggle={onToggleConnections}
        />
        <LayerRow
          icon={CircleDot}
          color="#265f44"
          label="端子"
          count={terminalCount}
          state={showTerminals ? 'on' : 'off'}
          onToggle={onToggleTerminals}
        />
        {inferenceRefs.length > 0 && (
          <LayerRow
            icon={Sparkles}
            color="#0891b2"
            label="AI 検出"
            count={inferenceRefs.length}
            state={layerStateOf(hiddenInferenceCount, inferenceRefs.length)}
            onToggle={() =>
              onSetSymbolGroupHidden(inferenceRefs, hiddenInferenceCount === 0)
            }
          />
        )}
        {classLayerRows.length > 0 && (
          <div className="px-2 pb-1 pt-2 text-[10px] font-bold text-slate-400">
            シンボル（種別）
          </div>
        )}
        {classLayerRows.map((layer) => (
          <LayerRow
            key={layer.key}
            icon={Square}
            color={layer.color}
            label={layer.label}
            count={layer.hidden > 0 ? `${layer.total - layer.hidden}/${layer.total}` : layer.total}
            state={layerStateOf(layer.hidden, layer.total)}
            onToggle={() => onSetSymbolGroupHidden(layer.refs, layer.hidden === 0)}
          />
        ))}
      </div>
    </div>
  )
}

function LayerRow({
  icon: Icon,
  color,
  label,
  count,
  state,
  onToggle,
}: {
  icon: typeof Square
  color: string
  label: string
  count: React.ReactNode
  state: LayerState
  onToggle: () => void
}) {
  const title =
    state === 'on' ? `${label}を非表示` : state === 'partial' ? `${label}をすべて表示` : `${label}を表示`
  return (
    <div className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-100">
      <Icon size={14} style={{ color }} className="flex-none" />
      <span className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-700">{label}</span>
      <span className="font-mono text-[10px] text-slate-400">{count}</span>
      <button
        type="button"
        className={`icon-button h-7 w-7 hover:bg-slate-200 ${
          state === 'on' ? 'text-slate-600' : state === 'partial' ? 'text-amber-500' : 'text-slate-300'
        }`}
        onClick={onToggle}
        aria-pressed={state !== 'on'}
        aria-label={title}
        title={title}
      >
        {state === 'on' ? <Eye size={15} /> : <EyeOff size={15} />}
      </button>
    </div>
  )
}
