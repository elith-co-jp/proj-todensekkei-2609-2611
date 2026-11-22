// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { routes } from './routes'
import ErrorPage from './pages/ErrorPage'

// ページの中身はそれぞれのテストで確かめる。ここではルートの組み方とエラー画面だけを見る
vi.mock('./App', async () => {
  const { Outlet } = await import('react-router-dom')
  return { default: () => <div><span>サイドバー</span><Outlet /></div> }
})
vi.mock('./pages/ProjectListPage', () => ({ default: () => <div>図面一覧</div> }))
vi.mock('./pages/EditorPage', () => ({ default: () => <div>エディタ</div> }))
vi.mock('./pages/AnalysisPage', () => ({ default: () => <div>解析結果</div> }))
vi.mock('./pages/ExportPage', () => ({ default: () => <div>入出力</div> }))
vi.mock('./pages/MlOpsPage', () => ({ default: () => <div>AI 学習</div> }))
vi.mock('./pages/GuidePage', () => ({ default: () => <div>ヘルプ</div> }))
vi.mock('./pages/CircuitPage', () => ({
  default: () => {
    throw new Error('changes.coils が読めません')
  },
}))

function open(path: string) {
  return render(<RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />)
}

beforeEach(() => {
  // React Router と React が、捕まえた例外をコンソールに出すのを黙らせる
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('routes', () => {
  it.each([
    ['/', '図面一覧'],
    ['/projects/3', 'エディタ'],
    ['/projects/3/analysis', '解析結果'],
    ['/export', '入出力'],
    ['/ml', 'AI 学習'],
    ['/guide', 'ヘルプ'],
  ])('%s shows %s inside the app frame', (path, text) => {
    const view = open(path)
    expect(view.getByText(text)).toBeTruthy()
    expect(view.getByText('サイドバー')).toBeTruthy()
  })

  it('shows a Japanese not-found page for an unknown URL, keeping the sidebar', () => {
    const view = open('/nope')
    expect(view.getByRole('heading', { name: 'ページが見つかりません' })).toBeTruthy()
    expect(view.getByText('サイドバー')).toBeTruthy()
    expect(view.queryByRole('button', { name: /再読み込み/ })).toBeNull()
  })

  it('shows a Japanese error page instead of the developer screen when a page crashes', () => {
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    const view = open('/circuit')
    expect(view.getByRole('heading', { name: '画面を表示できませんでした' })).toBeTruthy()
    expect(view.getByText('changes.coils が読めません')).toBeTruthy()
    expect(view.getByText('サイドバー')).toBeTruthy()
    expect(view.queryByText(/Hey developer/)).toBeNull()
    fireEvent.click(view.getByRole('button', { name: /再読み込み/ }))
    expect(reload).toHaveBeenCalled()
    fireEvent.click(view.getByRole('link', { name: /図面一覧へ戻る/ }))
    expect(view.getByText('図面一覧')).toBeTruthy()
  })
})

describe('ErrorPage', () => {
  function throwing(error: unknown) {
    return render(<RouterProvider router={createMemoryRouter([
      { path: '/', element: <div>一覧</div> },
      { path: '/x', loader: () => { throw error }, element: <div />, errorElement: <ErrorPage /> },
    ], { initialEntries: ['/x'] })} />)
  }

  it('treats a 404 response as not found', async () => {
    const view = throwing(new Response('', { status: 404 }))
    expect(await view.findByRole('heading', { name: 'ページが見つかりません' })).toBeTruthy()
  })

  it('shows the status text of other responses, and nothing for unknown values', async () => {
    const view = throwing(new Response('', { status: 500, statusText: 'Server Error' }))
    expect(await view.findByText('Server Error')).toBeTruthy()
    cleanup()
    const other = throwing('文字列の例外')
    expect(await other.findByRole('heading', { name: '画面を表示できませんでした' })).toBeTruthy()
    expect(other.container.querySelector('pre')).toBeNull()
  })
})
