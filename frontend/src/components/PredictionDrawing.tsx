import { useCallback, useEffect, useRef, useState } from 'react'
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react'

import type { Detection, StructureResult } from '../types'

type View = { x: number; y: number; scale: number }
type Props = {
  imageUrl: string
  name: string
  width: number
  height: number
  detections: Detection[]
  structure: StructureResult | null
  showSymbols: boolean
  showWires: boolean
  wireOpacity: number
}

export default function PredictionDrawing({
  imageUrl, name, width, height, detections, structure, showSymbols, showWires, wireOpacity,
}: Props) {
  const viewport = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; start: View } | null>(null)
  const [view, setView] = useState<View>({ x: 0, y: 0, scale: 1 })

  const fit = useCallback(() => {
    const rect = viewport.current?.getBoundingClientRect()
    if (!rect || !rect.width || !rect.height) return
    const scale = Math.max(0.01, Math.min((rect.width - 32) / width, (rect.height - 32) / height))
    setView({ scale, x: (rect.width - width * scale) / 2, y: (rect.height - height * scale) / 2 })
  }, [width, height])

  useEffect(() => {
    fit()
    const element = viewport.current
    if (!element) return
    const observer = new ResizeObserver(fit)
    observer.observe(element)
    return () => observer.disconnect()
  }, [fit])

  const zoomAt = useCallback((factor: number, x: number, y: number) => {
    setView((old) => {
      const scale = Math.min(12, Math.max(0.01, old.scale * factor))
      const ratio = scale / old.scale
      return { scale, x: x - (x - old.x) * ratio, y: y - (y - old.y) * ratio }
    })
  }, [])

  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const wheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      zoomAt(Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * 0.002), event.clientX - rect.left, event.clientY - rect.top)
    }
    element.addEventListener('wheel', wheel, { passive: false })
    return () => element.removeEventListener('wheel', wheel)
  }, [zoomAt])

  const zoomCenter = (factor: number) => {
    const rect = viewport.current?.getBoundingClientRect()
    if (rect) zoomAt(factor, rect.width / 2, rect.height / 2)
  }
  const imageScaleX = width / (structure?.image_size.width || width)
  const imageScaleY = height / (structure?.image_size.height || height)

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={viewport}
        className="surface-grid relative min-h-64 flex-1 touch-none overflow-hidden bg-slate-100"
        style={{ cursor: 'grab' }}
        role="region"
        aria-label="解析結果の図面"
        onPointerDown={(event) => {
          if (event.button !== 0) return
          event.currentTarget.setPointerCapture(event.pointerId)
          drag.current = { x: event.clientX, y: event.clientY, start: view }
        }}
        onPointerMove={(event) => {
          const current = drag.current
          if (!current) return
          setView({ ...current.start, x: current.start.x + event.clientX - current.x, y: current.start.y + event.clientY - current.y })
        }}
        onPointerUp={() => { drag.current = null }}
        onPointerCancel={() => { drag.current = null }}
        onLostPointerCapture={() => { drag.current = null }}
      >
        <div className="absolute left-0 top-0 origin-top-left" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`, width, height }}>
          <img src={imageUrl} alt={name} width={width} height={height} draggable={false} className="block max-w-none select-none bg-white shadow-sm" />
          <svg className="pointer-events-none absolute inset-0" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-label="予測レイヤー">
            {showWires && structure && (
              <g data-testid="wire-layer" opacity={wireOpacity} fill="none" stroke="#e11d48" strokeWidth={1.5 / view.scale}>
                {structure.wires.map((wire) => (
                  <polyline key={wire.id} points={wire.polyline.map(([x, y]) => `${x * imageScaleX},${y * imageScaleY}`).join(' ')} />
                ))}
              </g>
            )}
            {showSymbols && (
              <g data-testid="symbol-layer" fill="none" stroke="#0891b2" strokeWidth={1.5 / view.scale} strokeDasharray={`${6 / view.scale} ${3 / view.scale}`}>
                {detections.map((d, i) => (
                  <rect key={i} x={(d.cx - d.w / 2) * width} y={(d.cy - d.h / 2) * height} width={d.w * width} height={d.h * height}>
                    <title>{d.class_label}</title>
                  </rect>
                ))}
              </g>
            )}
          </svg>
        </div>
      </div>
      <div className="absolute bottom-4 right-4 flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
        <button type="button" className="icon-button" aria-label="縮小" title="縮小" onClick={() => zoomCenter(1 / 1.25)}><ZoomOut size={17} /></button>
        <span className="w-14 text-center font-mono text-xs tabular-nums text-slate-600">{Math.round(view.scale * 100)}%</span>
        <button type="button" className="icon-button" aria-label="拡大" title="拡大" onClick={() => zoomCenter(1.25)}><ZoomIn size={17} /></button>
        <button type="button" className="icon-button" aria-label="図面全体を表示" title="図面全体を表示" onClick={fit}><Maximize2 size={17} /></button>
      </div>
    </div>
  )
}
