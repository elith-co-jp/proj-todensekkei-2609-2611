import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react'

import type { CircuitResult } from '../../types'

type Overlay = CircuitResult['overlay'][number]
type View = { scale: number; x: number; y: number }

const MIN_SCALE = 0.05
const MAX_SCALE = 8
const DRAG_THRESHOLD = 4

/**
 * 回路の結果を重ねた図面を、画面いっぱいに拡大・縮小・移動して見る。
 * ホイールでカーソル位置を中心に拡大、ドラッグで移動、ダブルクリックで全体表示。
 * 外部入力の接点はクリックで ON / OFF を切り替えられる。
 */
export default function DrawingViewer({
  imageUrl,
  alt,
  width,
  height,
  overlay,
  inputs,
  inputDevices,
  onToggleInput,
  disabled,
  insets,
  children,
}: {
  imageUrl: string
  alt: string
  width: number
  height: number
  overlay: Overlay | undefined
  inputs: Record<string, boolean>
  inputDevices: Set<string>
  onToggleInput: (device: string) => void
  disabled?: boolean
  /** 図面の上に重ねたパネルで隠れる幅。全体表示はこれを除いた範囲に合わせる */
  insets?: { top?: number; right?: number; bottom?: number; left?: number }
  children?: ReactNode
}) {
  const stageRef = useRef<HTMLDivElement | null>(null)
  const [view, setView] = useState<View>({ scale: 1, x: 0, y: 0 })
  const viewRef = useRef(view)
  viewRef.current = view
  const fitted = useRef(true)
  const drag = useRef<{ id: number; sx: number; sy: number; vx: number; vy: number; moved: boolean } | null>(null)
  const top = insets?.top ?? 0
  const right = insets?.right ?? 0
  const bottom = insets?.bottom ?? 0
  const left = insets?.left ?? 0

  const fit = useCallback(() => {
    const stage = stageRef.current
    if (!stage || !width || !height) return
    const w = Math.max(stage.clientWidth - left - right - 32, 80)
    const h = Math.max(stage.clientHeight - top - bottom - 32, 80)
    const scale = Math.min(w / width, h / height)
    fitted.current = true
    setView({
      scale,
      x: left + 16 + (w - width * scale) / 2,
      y: top + 16 + (h - height * scale) / 2,
    })
  }, [width, height, top, right, bottom, left])

  // 図面を変えたとき・重ねたパネルを開閉したときは全体表示に戻す
  useLayoutEffect(() => {
    fit()
  }, [fit, imageUrl])

  // 画面の大きさが変わったら、全体表示のままなら合わせ直す
  useEffect(() => {
    const stage = stageRef.current
    if (!stage || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (fitted.current) fit()
    })
    observer.observe(stage)
    return () => observer.disconnect()
  }, [fit])

  const zoomAt = useCallback((factor: number, cx: number, cy: number) => {
    const v = viewRef.current
    const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor))
    const k = scale / v.scale
    fitted.current = false
    setView({ scale, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k })
  }, [])

  const zoomCenter = (factor: number) => {
    const stage = stageRef.current
    if (!stage) return
    zoomAt(factor, left + (stage.clientWidth - left - right) / 2, top + (stage.clientHeight - top - bottom) / 2)
  }

  // ページのスクロールを止めるため、ホイールは passive でない登録にする
  useEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    const onWheel = (event: WheelEvent) => {
      if ((event.target as Element | null)?.closest('[data-overlay-ui]')) return
      event.preventDefault()
      const rect = stage.getBoundingClientRect()
      zoomAt(Math.exp(-event.deltaY * 0.0015), event.clientX - rect.left, event.clientY - rect.top)
    }
    stage.addEventListener('wheel', onWheel, { passive: false })
    return () => stage.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as Element).closest('[data-overlay-ui]')) return
    drag.current = { id: event.pointerId, sx: event.clientX, sy: event.clientY, vx: view.x, vy: view.y, moved: false }
  }
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d || d.id !== event.pointerId) return
    const dx = event.clientX - d.sx
    const dy = event.clientY - d.sy
    if (!d.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return
    if (!d.moved) {
      d.moved = true
      event.currentTarget.setPointerCapture?.(event.pointerId)
    }
    fitted.current = false
    setView((v) => ({ ...v, x: d.vx + dx, y: d.vy + dy }))
  }
  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id === event.pointerId) {
      // 動かしたあとのクリックで接点を切り替えないよう、少し遅らせて消す
      const moved = drag.current.moved
      window.setTimeout(() => {
        if (drag.current?.id === event.pointerId) drag.current = null
      }, 0)
      if (!moved) drag.current = null
    }
  }
  const clickContact = (device: string) => {
    if (drag.current?.moved || disabled) return
    onToggleInput(device)
  }

  return (
    <div
      ref={stageRef}
      className="surface-grid relative h-full w-full cursor-grab touch-none select-none overflow-hidden bg-slate-100 active:cursor-grabbing"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={(event) => {
        if (!(event.target as Element).closest('[data-overlay-ui]')) fit()
      }}
    >
      <div
        className="absolute left-0 top-0 origin-top-left bg-white shadow-xl shadow-slate-900/15 ring-1 ring-slate-300"
        style={{ width, height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}
      >
        <img src={imageUrl} alt={alt} className="pointer-events-none absolute inset-0 h-full w-full" draggable={false} />
        {overlay && (
          <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none" aria-label="シミュレーション結果">
            {overlay.wires.map((w, i) => (
              <line
                key={i}
                x1={w.x1} y1={w.y1} x2={w.x2} y2={w.y2}
                stroke={w.live ? '#e11d48' : '#94a3b8'}
                strokeOpacity={w.live ? 0.9 : 0.5}
                strokeWidth={w.live ? 3 : 1.5}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {overlay.coils.map((c) => (
              <rect
                key={c.symbol_ref}
                x={c.cx - c.w / 2} y={c.cy - c.h / 2} width={c.w} height={c.h}
                fill={c.energized ? '#fb7185' : 'none'} fillOpacity={0.45}
                stroke={c.energized ? '#e11d48' : '#94a3b8'} strokeWidth={2} vectorEffect="non-scaling-stroke"
              />
            ))}
            {overlay.contacts.map((c) => {
              const isInput = inputDevices.has(c.device)
              const on = Boolean(inputs[c.device])
              return (
                <rect
                  key={c.symbol_ref}
                  x={c.cx - c.w / 2} y={c.cy - c.h / 2} width={c.w} height={c.h}
                  fill={isInput ? (on ? '#10b981' : '#0ea5e9') : 'none'}
                  fillOpacity={isInput ? 0.12 : 0}
                  stroke={c.closed ? '#10b981' : '#94a3b8'}
                  strokeDasharray={c.closed ? undefined : '4 3'}
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                  className={isInput ? 'cursor-pointer hover:fill-opacity-30' : undefined}
                  role={isInput ? 'button' : undefined}
                  aria-label={isInput ? `${c.device} を ${on ? 'OFF' : 'ON'} にする` : undefined}
                  onClick={isInput ? () => clickContact(c.device) : undefined}
                >
                  <title>{`${c.device}（${c.kind} 接点）: ${c.closed ? '閉' : '開'}${isInput ? `　クリックで ${on ? 'OFF' : 'ON'}` : ''}`}</title>
                </rect>
              )
            })}
          </svg>
        )}
      </div>

      {children}

      <div
        data-overlay-ui
        className="absolute bottom-4 flex items-center gap-1 rounded-xl border border-white/70 bg-white/90 p-1 text-slate-700 shadow-lg shadow-slate-900/10 backdrop-blur"
        style={{ left: left + 16 }}
      >
        <button type="button" className="icon-button h-9 w-9 hover:bg-slate-100" onClick={() => zoomCenter(1 / 1.25)} aria-label="縮小" title="縮小（ホイール）">
          <ZoomOut size={16} />
        </button>
        <button type="button" className="inline-flex h-9 items-center gap-1 rounded-lg px-2 text-xs font-bold hover:bg-slate-100" onClick={fit} title="全体を表示（ダブルクリック）">
          <Maximize2 size={14} /> 全体
        </button>
        <button type="button" className="icon-button h-9 w-9 hover:bg-slate-100" onClick={() => zoomCenter(1.25)} aria-label="拡大" title="拡大（ホイール）">
          <ZoomIn size={16} />
        </button>
        <span className="w-12 text-center font-mono text-[11px] text-slate-500" aria-label="表示倍率">
          {Math.round(view.scale * 100)}%
        </span>
      </div>
    </div>
  )
}
