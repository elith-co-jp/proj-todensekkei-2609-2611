/**
 * EXE（デスクトップ版）の画面とサーバのつながりの状態（DD-0047）。
 *
 * 以前は desktopMode・shutdownPending・desktopTerminated の 3 つの真偽値で持っていて、「終了中なのに未接続」のような
 * ありえない組み合わせを型で防げなかった。状態を 1 つにし、移り方を表で書く（表に無い移り方は無視する）。
 *
 *   web ──CONNECT──▶ connected ──REQUEST_SHUTDOWN──▶ shutting_down ──STOPPED──▶ terminated
 *                        │  ▲                               │
 *                        │  └────────SHUTDOWN_FAILED────────┘
 *                        └──────────────STOPPED（サーバが応答しない）──────────────▶ terminated
 *
 * - web: ブラウザで開いた通常の画面（デスクトップ版ではない、またはまだ分からない）
 * - connected: デスクトップ版。生存の合図（heartbeat）を送っている
 * - shutting_down: 終了を頼んで、応答を待っている（終了ボタンを押せない）
 * - terminated: サーバが止まった。終了の画面を出す
 */
export type DesktopSession = 'web' | 'connected' | 'shutting_down' | 'terminated'

export type DesktopSessionEvent = 'CONNECT' | 'REQUEST_SHUTDOWN' | 'SHUTDOWN_FAILED' | 'STOPPED'

export const DESKTOP_SESSION_TRANSITIONS: Record<DesktopSession, Partial<Record<DesktopSessionEvent, DesktopSession>>> = {
  web: { CONNECT: 'connected' },
  connected: { REQUEST_SHUTDOWN: 'shutting_down', STOPPED: 'terminated' },
  shutting_down: { SHUTDOWN_FAILED: 'connected', STOPPED: 'terminated' },
  terminated: {},
}

/** React の useReducer にそのまま渡す。表に無い移り方なら今の状態のまま。 */
export function desktopSessionReducer(state: DesktopSession, event: DesktopSessionEvent): DesktopSession {
  return DESKTOP_SESSION_TRANSITIONS[state][event] ?? state
}
