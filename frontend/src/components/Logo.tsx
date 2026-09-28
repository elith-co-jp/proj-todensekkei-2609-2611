import { Scan } from 'lucide-react'

/**
 * TodenYOLO ロゴ。グラデーションの角丸に検出枠（YOLO のバウンディングボックス）を重ねたマーク。
 * 文字色は親の text-* を継承する。
 */
export function LogoMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`brand-gradient flex flex-none select-none items-center justify-center rounded-xl text-white ${className}`}
      style={{ height: size, width: size }}
    >
      <Scan size={Math.round(size * 0.6)} strokeWidth={2.4} />
    </span>
  )
}

export function Logo({
  size = 28,
  showWordmark = true,
  className = '',
}: {
  size?: number
  showWordmark?: boolean
  className?: string
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <LogoMark size={size} />
      {showWordmark && (
        <span
          className="font-extrabold leading-none tracking-tight"
          style={{ fontSize: Math.round(size * 0.68) }}
        >
          TodenYOLO
        </span>
      )}
    </span>
  )
}
