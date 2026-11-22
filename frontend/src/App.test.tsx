// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'

const api = vi.hoisted(() => ({ health: vi.fn(), desktopHeartbeat: vi.fn(), shutdownDesktop: vi.fn() }))
vi.mock('./api/client', () => ({ api }))

let mediaListeners: ((event: MediaQueryListEvent) => void)[] = []
let wide = true

function stubMedia() {
  mediaListeners = []
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: wide, media: query,
    addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => mediaListeners.push(listener),
    removeEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => {
      mediaListeners = mediaListeners.filter((l) => l !== listener)
    },
  }))
}

function openApp(path = '/') {
  const router = createMemoryRouter([
    {
      path: '/', element: <App />,
      children: [
        { index: true, element: <div>一覧の中身</div> },
        { path: 'projects/:id', element: <div>エディタの中身</div> },
        { path: 'guide', element: <div>ヘルプの中身</div> },
      ],
    },
  ], { initialEntries: [path] })
  return { ...render(<RouterProvider router={router} />), router }
}

beforeEach(() => {
  vi.clearAllMocks()
  wide = true
  stubMedia()
  window.localStorage.setItem('seqanno:onboarding-complete', '1')
  api.health.mockResolvedValue({ status: 'ok', desktop: false })
  api.desktopHeartbeat.mockResolvedValue({ status: 'ok' })
  api.shutdownDesktop.mockResolvedValue({ status: 'shutting_down' })
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0)
    return 1
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  window.localStorage.clear()
})

describe('App', () => {
  it('shows the navigation and the page, and collapses the sidebar', async () => {
    const view = openApp()
    expect(view.getByText('一覧の中身')).toBeTruthy()
    expect(view.getByRole('navigation', { name: 'メインナビゲーション' })).toBeTruthy()
    expect(view.queryByRole('dialog')).toBeNull()
    fireEvent.click(view.getByRole('button', { name: 'サイドバーを閉じる' }))
    expect(view.getByRole('button', { name: 'サイドバーを開く' })).toBeTruthy()
    expect(view.getByRole('link', { name: /ヘルプ/ }).getAttribute('title')).toBe('ヘルプ')
    fireEvent.click(view.getByRole('button', { name: 'サイドバーを開く' }))
    fireEvent.click(view.getByRole('link', { name: /ヘルプ/ }))
    expect(await view.findByText('ヘルプの中身')).toBeTruthy()
    await waitFor(() => expect(api.health).toHaveBeenCalled())
    expect(view.queryByRole('button', { name: /終了/ })).toBeNull()
  })

  it('opens the tour on the first visit and remembers when it is closed', async () => {
    window.localStorage.clear()
    const view = openApp()
    expect(view.getByRole('dialog', { name: 'TodenYOLOへようこそ' })).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: '後で見る' }))
    expect(window.localStorage.getItem('seqanno:onboarding-complete')).toBe('1')
    expect(view.queryByRole('dialog')).toBeNull()
    fireEvent.click(view.getByRole('button', { name: /ツアー/ }))
    expect(view.getByRole('dialog', { name: 'TodenYOLOへようこそ' })).toBeTruthy()
  })

  it('still opens and closes the tour when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    const view = openApp()
    fireEvent.click(view.getByRole('button', { name: 'ツアーを閉じる' }))
    expect(view.queryByRole('dialog')).toBeNull()
  })

  it('uses the full-screen layout for the editor', () => {
    window.localStorage.clear()
    const view = openApp('/projects/3')
    expect(view.getByRole('dialog', { name: 'TodenYOLOへようこそ' })).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'ツアーを閉じる' }))
    expect(view.getByText('エディタの中身')).toBeTruthy()
    expect(view.queryByRole('navigation', { name: 'メインナビゲーション' })).toBeNull()
  })

  it('opens the mobile menu as a dialog on narrow screens and closes it on navigation', async () => {
    wide = false
    stubMedia()
    const view = openApp()
    fireEvent.click(view.getByRole('button', { name: 'メニューを開く' }))
    expect(view.getByRole('dialog', { name: 'メインメニュー' })).toBeTruthy()
    fireEvent.click(view.getAllByRole('button', { name: 'メニューを閉じる' })[0])
    expect(view.queryByRole('dialog', { name: 'メインメニュー' })).toBeNull()
    fireEvent.click(view.getByRole('button', { name: 'メニューを開く' }))
    fireEvent.click(view.getByRole('link', { name: /ヘルプ/ }))
    await view.findByText('ヘルプの中身')
    expect(view.queryByRole('dialog', { name: 'メインメニュー' })).toBeNull()
    act(() => mediaListeners.forEach((l) => l({ matches: true } as MediaQueryListEvent)))
    fireEvent.click(view.getByRole('button', { name: 'メニューを開く' }))
    expect(view.queryByRole('dialog', { name: 'メインメニュー' })).toBeNull()
  })

  it('ignores a server that is not ready yet', async () => {
    api.health.mockRejectedValue(new Error('down'))
    const view = openApp()
    await waitFor(() => expect(api.health).toHaveBeenCalled())
    expect(view.getByText('一覧の中身')).toBeTruthy()
  })

  describe('desktop (EXE) mode', () => {
    beforeEach(() => {
      api.health.mockResolvedValue({ status: 'ok', desktop: true })
    })

    it('sends heartbeats and stops when unmounted', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      const view = openApp()
      await waitFor(() => expect(api.desktopHeartbeat).toHaveBeenCalledTimes(1))
      await act(() => vi.advanceTimersByTimeAsync(15000))
      expect(api.desktopHeartbeat).toHaveBeenCalledTimes(2)
      view.unmount()
      await act(() => vi.advanceTimersByTimeAsync(30000))
      expect(api.desktopHeartbeat).toHaveBeenCalledTimes(2)
    })

    it('keeps going after a heartbeat failure while the server still answers', async () => {
      api.desktopHeartbeat.mockRejectedValueOnce(new Error('一時的'))
      const view = openApp()
      await waitFor(() => expect(api.health).toHaveBeenCalledTimes(2))
      expect(view.getByText('一覧の中身')).toBeTruthy()
    })

    it('shows the terminated screen when the server is gone, and reconnects', async () => {
      api.desktopHeartbeat.mockRejectedValue(new Error('refused'))
      api.health.mockResolvedValueOnce({ status: 'ok', desktop: true }).mockRejectedValue(new Error('refused'))
      const reload = vi.fn()
      vi.stubGlobal('location', { ...window.location, reload })
      const view = openApp()
      expect(await view.findByText('TodenYOLO を終了しました')).toBeTruthy()
      fireEvent.click(view.getByRole('button', { name: '再接続' }))
      expect(reload).toHaveBeenCalled()
    })

    it('asks before shutting down, then closes the tab', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)
      const close = vi.spyOn(window, 'close').mockImplementation(() => undefined)
      let finish: (value: { status: string }) => void = () => undefined
      api.shutdownDesktop.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
      const view = openApp()
      fireEvent.click(view.getByRole('button', { name: 'サイドバーを閉じる' }))
      const button = await view.findByRole('button', { name: /終了/ })
      fireEvent.click(button)
      expect(confirm).toHaveBeenCalledTimes(1)
      expect(api.shutdownDesktop).not.toHaveBeenCalled()
      fireEvent.click(button)
      expect(await view.findByText('終了中…')).toBeTruthy()
      // 終了中にもう一度押しても二重に送らない
      fireEvent.click(view.getByRole('button', { name: /終了中/ }))
      expect(api.shutdownDesktop).toHaveBeenCalledTimes(1)
      await act(async () => finish({ status: 'shutting_down' }))
      expect(await view.findByText('TodenYOLO を終了しました')).toBeTruthy()
      await act(() => vi.advanceTimersByTimeAsync(300))
      expect(close).toHaveBeenCalled()
    })

    it('reports a failed shutdown and lets the user retry', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const alert = vi.spyOn(window, 'alert').mockImplementation(() => undefined)
      api.shutdownDesktop.mockRejectedValueOnce(new Error('終了できません')).mockRejectedValueOnce('文字列')
      const view = openApp()
      fireEvent.click(await view.findByRole('button', { name: /終了/ }))
      await waitFor(() => expect(alert).toHaveBeenCalledWith('終了できません'))
      fireEvent.click(view.getByRole('button', { name: /^終了$/ }))
      await waitFor(() => expect(alert).toHaveBeenCalledWith('文字列'))
    })
  })
})
