import type { Connection, SymbolBox } from '../../types'

export type DraftRect = { x: number; y: number; w: number; h: number }

export function EditorCanvasSvg({
  iw,
  ih,
  zoom,
  showConnections,
  showTerminals,
  connections,
  nodePos,
  selectedConn,
  visibleSymbols,
  selectedRef,
  colorOf,
  draftRect,
  draftClassKey,
}: {
  iw: number
  ih: number
  zoom: number
  showConnections: boolean
  showTerminals: boolean
  connections: Connection[]
  nodePos: (symbolRef: string, terminalRef: string | null) => { x: number; y: number } | null
  selectedConn: number | null
  visibleSymbols: SymbolBox[]
  selectedRef: string | null
  colorOf: (key: string) => string
  draftRect: DraftRect | null
  draftClassKey: string
}) {
  return (
    <svg
      className="pointer-events-none absolute left-0 top-0"
      width={iw}
      height={ih}
      viewBox={`0 0 ${iw} ${ih}`}
    >
      {/* 配線 */}
      {showConnections && connections.map((c, i) => {
        const a = nodePos(c.from_symbol_ref, c.from_terminal_ref)
        const b = nodePos(c.to_symbol_ref, c.to_terminal_ref)
        if (!a || !b) return null
        const active = selectedConn === i
        return (
          <g key={`c${i}`}>
            <line
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={active ? '#9ACA3B' : c.kind === 'sheet_ref' ? '#db2777' : '#7c3aed'}
              strokeWidth={(active ? 3.5 : 2) / zoom}
              strokeDasharray={c.kind === 'sheet_ref' ? `${6 / zoom} ${4 / zoom}` : undefined}
              opacity={0.85}
            />
            <circle cx={b.x} cy={b.y} r={4 / zoom} fill={active ? '#9ACA3B' : '#7c3aed'} />
          </g>
        )
      })}
      {/* シンボル */}
      {visibleSymbols.map((s) => {
        const x = (s.cx - s.w / 2) * iw
        const y = (s.cy - s.h / 2) * ih
        const w = s.w * iw
        const h = s.h * ih
        const active = selectedRef === s.ref
        const color = colorOf(s.class_key)
        return (
          <g key={s.ref}>
            <rect
              x={x}
              y={y}
              width={w}
              height={h}
              fill={color}
              fillOpacity={active ? 0.18 : 0.08}
              stroke={color}
              strokeWidth={(active ? 2.5 : 1.5) / zoom}
              strokeDasharray={s.origin === 'inference' ? `${6 / zoom} ${3 / zoom}` : undefined}
            />
            <text
              x={x}
              y={y - 3 / zoom}
              fill={color}
              fontSize={11 / zoom}
              fontWeight="600"
              style={{ paintOrder: 'stroke' }}
              stroke="#fff"
              strokeWidth={2.5 / zoom}
            >
              {s.origin === 'inference' ? 'AI ' : ''}
              {s.ref}
              {s.label ? ` ${s.label}` : ''}
              {s.confidence != null ? ` ${Math.round(s.confidence * 100)}%` : ''}
            </text>
            {active &&
              ([
                [x, y],
                [x + w, y],
                [x, y + h],
                [x + w, y + h],
              ] as [number, number][]).map(([hx, hy], i) => (
                <rect
                  key={i}
                  x={hx - 4 / zoom}
                  y={hy - 4 / zoom}
                  width={8 / zoom}
                  height={8 / zoom}
                  fill="#fff"
                  stroke={color}
                  strokeWidth={1.5 / zoom}
                />
              ))}
            {showTerminals && s.terminals.map((t) => (
              <g key={t.ref}>
                <circle
                  cx={t.tx * iw}
                  cy={t.ty * ih}
                  r={4.5 / zoom}
                  fill="#fff"
                  stroke="#3f9067"
                  strokeWidth={2 / zoom}
                />
                <text
                  x={t.tx * iw + 6 / zoom}
                  y={t.ty * ih - 4 / zoom}
                  fill="#265f44"
                  fontSize={10 / zoom}
                  style={{ paintOrder: 'stroke' }}
                  stroke="#fff"
                  strokeWidth={2 / zoom}
                >
                  {t.name}
                </text>
              </g>
            ))}
          </g>
        )
      })}
      {draftRect && (
        <rect
          x={draftRect.x}
          y={draftRect.y}
          width={draftRect.w}
          height={draftRect.h}
          fill={colorOf(draftClassKey)}
          fillOpacity={0.15}
          stroke={colorOf(draftClassKey)}
          strokeWidth={2 / zoom}
          strokeDasharray={`${5 / zoom} ${3 / zoom}`}
        />
      )}
    </svg>
  )
}
