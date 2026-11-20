// 画面に出す時刻は日本時間（DD-0036）。PC のタイムゾーン設定に関係なく Asia/Tokyo で表示する。
// サーバーは `+09:00` 付きで返す。オフセットのない古い形式は UTC とみなす（DB は UTC で保存）。

const HAS_OFFSET = /(Z|[+-]\d{2}:?\d{2})$/

function parts(date: Date): Record<string, string> {
  const fmt = new Intl.DateTimeFormat('ja-JP', {
    timeZone: 'Asia/Tokyo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  })
  const out: Record<string, string> = {}
  for (const p of fmt.formatToParts(date)) out[p.type] = p.value
  return out
}

export function parseServerTime(iso: string): Date | null {
  const d = new Date(HAS_OFFSET.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

/** `11-20 17:10:00` の形（月-日 時:分:秒、日本時間）。null や読めない値は `—`。 */
export function formatJstShort(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = parseServerTime(iso)
  if (!d) return '—'
  const p = parts(d)
  return `${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`
}

/** `17:10:00` の形（日本時間）。ログの行頭など。 */
export function formatJstClock(date: Date = new Date()): string {
  const p = parts(date)
  return `${p.hour}:${p.minute}:${p.second}`
}
