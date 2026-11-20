import { describe, expect, it } from 'vitest'
import { formatJstClock, formatJstShort } from './jst'

describe('日本時間の表示', () => {
  it('+09:00 付きの時刻はそのまま日本時間で出す', () => {
    expect(formatJstShort('2026-11-20T17:10:05+09:00')).toBe('11-20 17:10:05')
  })
  it('UTC の時刻は 9 時間進めて出す（日付またぎも）', () => {
    expect(formatJstShort('2026-11-20T08:10:05+00:00')).toBe('11-20 17:10:05')
    expect(formatJstShort('2026-11-20T20:00:00Z')).toBe('11-21 05:00:00')
  })
  it('オフセットのない古い形式は UTC とみなす', () => {
    expect(formatJstShort('2026-11-20T08:10:05')).toBe('11-20 17:10:05')
  })
  it('空や読めない値は —', () => {
    expect(formatJstShort(null)).toBe('—')
    expect(formatJstShort('nope')).toBe('—')
  })
  it('時計の表示も日本時間', () => {
    expect(formatJstClock(new Date('2026-11-20T15:30:00Z'))).toBe('00:30:00')
  })
})
