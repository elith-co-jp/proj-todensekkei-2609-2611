/**
 * TodenYOLO ロゴ。検出枠（YOLO のバウンディングボックス）がシーケンス図の a 接点をとらえたマーク。
 * 絵は favicon・EXE のアイコンと同じ public/todenyolo-mark.svg。ワードマークの文字色は親の text-* を継承する。
 */
export function LogoMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <img
      src="/todenyolo-mark.svg"
      alt=""
      aria-hidden="true"
      draggable={false}
      width={size}
      height={size}
      className={`block flex-none select-none ${className}`}
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
          style={{ fontSize: Math.round(size * 0.68) }}
        >
          TodenYOLO
        </span>
      )}
    </span>
  )
}
