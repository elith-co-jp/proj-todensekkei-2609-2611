import { useCallback, useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  BookOpen,
  CircleHelp,
  Download,
  LayoutDashboard,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Power,
  Sparkles,
  X,
} from 'lucide-react'

import { api } from './api/client'
import { Logo, LogoMark } from './components/Logo'
import { OnboardingTour } from './components/OnboardingTour'
import { useModalFocus } from './hooks/useModalFocus'
import { isEditorPath } from './utils/workspace'

const NAVIGATION = [
  { to: '/', label: '図面ワークスペース', caption: '登録・進捗・編集', icon: LayoutDashboard, end: true },
  { to: '/ml', label: 'AI 改善サイクル', caption: '推論・学習・モデル', icon: Sparkles, end: false },
  { to: '/export', label: 'データ受け渡し', caption: '出力・取り込み', icon: Download, end: false },
  { to: '/guide', label: '操作ガイド', caption: '手順・ショートカット', icon: BookOpen, end: false },
]

export default function App() {
  const location = useLocation()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [tourOpen, setTourOpen] = useState(false)
  const [desktopMode, setDesktopMode] = useState(false)
  const [shutdownPending, setShutdownPending] = useState(false)
  const [desktopTerminated, setDesktopTerminated] = useState(false)
  const [desktopNavigation, setDesktopNavigation] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 1024px)').matches,
  )
  const mobileNavigationRef = useRef<HTMLElement | null>(null)
  const heartbeatTimerRef = useRef<number | null>(null)

  const stopHeartbeat = useCallback(() => {
    if (heartbeatTimerRef.current !== null) {
      window.clearInterval(heartbeatTimerRef.current)
      heartbeatTimerRef.current = null
    }
  }, [])

  const sendHeartbeat = useCallback(() => {
    void api.desktopHeartbeat().catch(async () => {
      // 一時的な失敗なら継続。health も通らなければサーバ停止とみなし、
      // 打ち続けてコンソールが接続拒否エラーで埋まるのを防ぐため打ち切る
      try {
        await api.health()
        return
      } catch {
        /* サーバ応答なし → 終了扱い */
      }
      stopHeartbeat()
      setDesktopTerminated(true)
    })
  }, [stopHeartbeat])

  const closeTour = useCallback(() => {
    setTourOpen(false)
    try {
      window.localStorage.setItem('seqanno:onboarding-complete', '1')
    } catch {
      // ストレージを利用できない環境でもツアーは閉じられる。
    }
  }, [])
  const closeMobileNavigation = useCallback(() => setMobileNavOpen(false), [])
  const shutdownDesktop = useCallback(async () => {
    if (shutdownPending) return
    const confirmed = window.confirm('TodenYOLO を終了します。保存済みであることを確認してください。')
    if (!confirmed) return
    setShutdownPending(true)
    try {
      await api.shutdownDesktop()
      stopHeartbeat()
      setDesktopTerminated(true)
      window.setTimeout(() => window.close(), 300)
    } catch (caught) {
      setShutdownPending(false)
      window.alert(caught instanceof Error ? caught.message : String(caught))
    }
  }, [shutdownPending, stopHeartbeat])
  const mobileNavigationModalOpen = mobileNavOpen && !desktopNavigation

  useModalFocus({
    open: mobileNavigationModalOpen,
    containerRef: mobileNavigationRef,
    onClose: closeMobileNavigation,
  })

  useEffect(() => {
    try {
      if (window.localStorage.getItem('seqanno:onboarding-complete') !== '1') setTourOpen(true)
    } catch {
      setTourOpen(true)
    }
  }, [])

  useEffect(() => {
    let active = true

    const start = async () => {
      try {
        const status = await api.health()
        if (!active || !status.desktop) return
        setDesktopMode(true)
        sendHeartbeat()
        heartbeatTimerRef.current = window.setInterval(sendHeartbeat, 15000)
      } catch {
        // API 起動前の一時的な失敗では、通常画面の表示を優先する。
      }
    }

    void start()

    return () => {
      active = false
      stopHeartbeat()
    }
  }, [sendHeartbeat, stopHeartbeat])

  useEffect(() => {
    setMobileNavOpen(false)
  }, [location.pathname])

  useEffect(() => {
    const media = window.matchMedia('(min-width: 1024px)')
    const update = (event: MediaQueryListEvent) => setDesktopNavigation(event.matches)
    setDesktopNavigation(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  if (desktopTerminated) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-[#07111f] px-6 text-slate-100">
        <div className="max-w-sm text-center">
          <LogoMark size={44} className="mx-auto text-cyan-300" />
          <h1 className="mt-5 text-lg font-bold">TodenYOLO を終了しました</h1>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            このタブは閉じて構いません。アプリを再起動した場合は再接続してください。
          </p>
          <button
            type="button"
            className="mt-6 rounded-xl bg-cyan-300 px-5 py-2.5 text-sm font-bold text-slate-950 transition hover:bg-cyan-200"
            onClick={() => window.location.reload()}
          >
            再接続
          </button>
        </div>
      </main>
    )
  }

  if (isEditorPath(location.pathname)) {
    return (
      <>
        <main
          className="min-h-dvh bg-slate-950"
          aria-hidden={tourOpen}
          inert={tourOpen ? true : undefined}
        >
          <Outlet />
        </main>
        <OnboardingTour open={tourOpen} onClose={closeTour} />
      </>
    )
  }

  return (
    <>
    <div
      className="app-surface flex min-h-dvh text-slate-900"
      aria-hidden={tourOpen}
      inert={tourOpen ? true : undefined}
    >
      {mobileNavOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-slate-950/35 backdrop-blur-sm lg:hidden"
          onClick={closeMobileNavigation}
          aria-label="メニューを閉じる"
        />
      )}

      <aside
        ref={mobileNavigationRef}
        aria-hidden={!desktopNavigation && !mobileNavOpen}
        inert={!desktopNavigation && !mobileNavOpen ? true : undefined}
        role={mobileNavigationModalOpen ? 'dialog' : undefined}
        aria-modal={mobileNavigationModalOpen ? true : undefined}
        aria-label={mobileNavigationModalOpen ? 'メインメニュー' : undefined}
        tabIndex={mobileNavigationModalOpen ? -1 : undefined}
        className={`fixed inset-y-0 left-0 z-50 flex w-[264px] flex-none flex-col overflow-hidden border-r border-slate-200 bg-white text-slate-800 shadow-2xl shadow-slate-950/10 transition-transform duration-300 lg:sticky lg:top-0 lg:h-dvh lg:translate-x-0 lg:shadow-none lg:transition-[width] ${
          mobileNavOpen ? 'translate-x-0' : '-translate-x-full'
        } ${sidebarCollapsed ? 'lg:w-[76px]' : 'lg:w-[264px]'}`}
      >
        <div className={`flex min-h-24 items-center gap-3 px-5 py-5 ${sidebarCollapsed ? 'lg:justify-center lg:px-3' : ''}`}>
          <div className={`min-w-0 flex-1 ${sidebarCollapsed ? 'lg:hidden' : ''}`}>
            <Logo size={30} className="text-slate-950" />
            <div className="mt-3 text-[10px] font-semibold tracking-[0.16em] text-slate-600">
              ANNOTATION OPERATIONS
            </div>
          </div>
          {sidebarCollapsed && <LogoMark size={34} className="hidden lg:block" />}
          <button
            type="button"
            className="icon-button hidden text-slate-600 hover:bg-slate-100 hover:text-blue-700 lg:inline-flex"
            onClick={() => setSidebarCollapsed((value) => !value)}
            aria-label={sidebarCollapsed ? 'サイドバーを開く' : 'サイドバーを閉じる'}
            title={sidebarCollapsed ? 'サイドバーを開く' : 'サイドバーを閉じる'}
          >
            {sidebarCollapsed ? <PanelLeftOpen size={19} /> : <PanelLeftClose size={19} />}
          </button>
          <button
            type="button"
            data-autofocus
            className="icon-button text-slate-500 hover:bg-slate-100 hover:text-blue-700 lg:hidden"
            onClick={closeMobileNavigation}
            aria-label="メニューを閉じる"
          >
            <X size={20} />
          </button>
        </div>

        <div className="mx-5 h-px bg-slate-200" />

        <div className={`px-6 pt-7 text-[10px] font-semibold tracking-[0.12em] text-slate-600 ${sidebarCollapsed ? 'lg:hidden' : ''}`}>WORKSPACE</div>
        <nav className="flex-1 space-y-1 px-3 py-3" aria-label="メインナビゲーション">
          {NAVIGATION.map(({ to, label, caption, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={sidebarCollapsed ? label : undefined}
              className={({ isActive }) =>
                `group relative flex min-h-12 items-center gap-3 overflow-hidden rounded-xl px-3 py-2 transition ${
                  sidebarCollapsed ? 'lg:justify-center lg:px-2' : ''
                } ${
                  isActive
                    ? 'bg-blue-50 text-blue-700'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-blue-700'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={`flex h-8 w-8 flex-none items-center justify-center rounded-lg transition ${
                      isActive ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 group-hover:text-blue-600'
                    }`}
                  >
                    <Icon size={17} />
                  </span>
                  <span className={`min-w-0 ${sidebarCollapsed ? 'lg:hidden' : ''}`}>
                    <span className="block truncate text-[13px] font-semibold">{label}</span>
                    <span className="mt-0.5 block truncate text-[10px] text-slate-600">
                      {caption}
                    </span>
                  </span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-slate-200 p-3">
          <button
            type="button"
            className={`flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-slate-600 transition hover:bg-slate-50 hover:text-blue-700 ${
              sidebarCollapsed ? 'lg:justify-center lg:px-2' : ''
            }`}
            onClick={() => setTourOpen(true)}
            title="ツアーガイドを表示"
          >
            <CircleHelp size={17} className="flex-none text-slate-500" />
            <span className={`text-xs font-semibold ${sidebarCollapsed ? 'lg:hidden' : ''}`}>ツアーガイドを表示</span>
          </button>
          {desktopMode && (
            <button
              type="button"
              className={`mt-1 flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-slate-500 transition hover:bg-rose-50 hover:text-rose-700 disabled:cursor-wait disabled:opacity-70 ${
                sidebarCollapsed ? 'lg:justify-center lg:px-2' : ''
              }`}
              onClick={() => void shutdownDesktop()}
              disabled={shutdownPending}
              title="アプリを終了"
            >
              <Power size={17} className="flex-none" />
              <span className={`text-xs font-semibold ${sidebarCollapsed ? 'lg:hidden' : ''}`}>
                {shutdownPending ? '終了中...' : 'アプリを終了'}
              </span>
            </button>
          )}
          <div className={`px-3 pt-3 text-[10px] text-slate-600 ${sidebarCollapsed ? 'lg:hidden' : ''}`}>TodenYOLO · Schema v1.0</div>
        </div>
      </aside>

      <div
        className="relative flex min-w-0 flex-1 flex-col"
        aria-hidden={mobileNavigationModalOpen}
        inert={mobileNavigationModalOpen ? true : undefined}
      >
        <header className="sticky top-0 z-30 flex min-h-16 items-center gap-3 border-b border-slate-200/70 bg-white/80 px-4 backdrop-blur-xl lg:hidden">
          <button
            type="button"
            className="icon-button text-slate-600 hover:bg-slate-100"
            onClick={() => setMobileNavOpen(true)}
            aria-label="メニューを開く"
          >
            <Menu size={21} />
          </button>
          <LogoMark size={25} />
          <div className="min-w-0">
            <div className="truncate text-sm font-bold text-slate-900">TodenYOLO</div>
            <div className="text-[10px] font-semibold tracking-[0.12em] text-slate-600">ANNOTATION OPERATIONS</div>
          </div>
        </header>
        <main className="relative min-w-0 flex-1">
          <Outlet />
        </main>
      </div>

    </div>
      <OnboardingTour open={tourOpen} onClose={closeTour} />
    </>
  )
}
