import { describe, expect, it } from 'vitest'

import { DESKTOP_SESSION_TRANSITIONS, desktopSessionReducer, type DesktopSessionEvent } from './desktopSession'

const run = (events: DesktopSessionEvent[]) => events.reduce(desktopSessionReducer, 'web' as const)

describe('desktopSessionReducer', () => {
  it('つながってから終了まで', () => {
    expect(run(['CONNECT'])).toBe('connected')
    expect(run(['CONNECT', 'REQUEST_SHUTDOWN'])).toBe('shutting_down')
    expect(run(['CONNECT', 'REQUEST_SHUTDOWN', 'STOPPED'])).toBe('terminated')
  })

  it('終了に失敗したらつながった状態に戻り、もう一度押せる', () => {
    expect(run(['CONNECT', 'REQUEST_SHUTDOWN', 'SHUTDOWN_FAILED'])).toBe('connected')
    expect(run(['CONNECT', 'REQUEST_SHUTDOWN', 'SHUTDOWN_FAILED', 'REQUEST_SHUTDOWN'])).toBe('shutting_down')
  })

  it('サーバが応答しなくなれば終了の画面にする', () => {
    expect(run(['CONNECT', 'STOPPED'])).toBe('terminated')
  })

  it('表に無い移り方は無視する', () => {
    expect(run(['REQUEST_SHUTDOWN'])).toBe('web') // デスクトップ版でなければ終了できない
    expect(run(['STOPPED'])).toBe('web')
    expect(run(['CONNECT', 'STOPPED', 'CONNECT', 'REQUEST_SHUTDOWN'])).toBe('terminated') // 終わったら戻らない
    expect(Object.keys(DESKTOP_SESSION_TRANSITIONS.terminated)).toHaveLength(0)
  })
})
