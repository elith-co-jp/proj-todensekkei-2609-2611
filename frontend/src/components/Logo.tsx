/**
 * Elith ロゴ。
 * マーク実体は public/elith-mark.svg（差し替え可能）。文字色は親の text-* を継承する。
 */

export function LogoMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <img
      src="/elith-mark.svg"
      alt="Elith"
      width={size}
      height={size}
      className={`flex-none select-none ${className}`}
      style={{ height: size, width: 'auto' }}
      draggable={false}
    />
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
          style={{ fontSize: Math.round(size * 0.82) }}
        >
          Elith
        </span>
      )}
    </span>
  )
}
