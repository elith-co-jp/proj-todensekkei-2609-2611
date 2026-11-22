// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'

import type { ProjectDetail, ProjectRow, ReviewSummary, SymbolClass } from '../types'
import { clearPersistedProjectSave } from '../utils/workspace'
import EditorPage from './EditorPage'
import { buildAnnotationPayload, buildMetaForm } from './editor/model'

const apiMocks = vi.hoisted(() => ({
  aiImport: vi.fn(),
  attributeDefinitions: vi.fn(),
  exportProject: vi.fn(),
  getProject: vi.fn(),
  getPredictions: vi.fn(),
  getReviewSummary: vi.fn(),
  imageUrl: vi.fn((id: number) => `/api/projects/${id}/image`),
  listClasses: vi.fn(),
  listProjects: vi.fn(),
  saveAnnotations: vi.fn(),
  splitCoilWires: vi.fn(),
  suggestSheetNumber: vi.fn(),
  updateMeta: vi.fn(),
}))

vi.mock('../api/client', () => ({ api: apiMocks }))

const scrollIntoView = vi.fn()

const reviewSummary: ReviewSummary = {
  project_id: 1,
  status: 'draft',
  in_next_training: false,
  ai_detection_count: 12,
  region_excluded_count: 2,
  imported: true,
  kept: 7,
  relabeled: 2,
  deleted: 3,
  manual_added: 4,
}

const symbolClass: SymbolClass = {
  id: 1,
  key: 'relay',
  label: 'リレー',
  yolo_index: 0,
  color: '#0891b2',
  is_active: true,
  sort_order: 0,
}

function project(id: number): ProjectDetail {
  return {
    id,
    name: `匿名ページ${id}`,
    sheet_no: String(id),
    page_no: String(id),
    revision: null,
    source_file: null,
    image_width: 1000,
    image_height: 800,
    status: 'draft',
    assignee: null,
    note: null,
    symbols: [
      {
        ref: 'SYM-0001',
        class_key: 'relay',
        label: null,
        cx: 0.3,
        cy: 0.4,
        w: 0.12,
        h: 0.08,
        note: null,
        terminals: [],
      },
      {
        ref: 'SYM-0002',
        class_key: 'relay',
        label: null,
        cx: 0.7,
        cy: 0.4,
        w: 0.12,
        h: 0.08,
        note: null,
        terminals: [],
      },
    ],
    connections: [],
    images: [{ filename: 'anonymous.png', sha256: 'demo', width: 1000, height: 800 }],
  }
}

function row(id: number): ProjectRow {
  return {
    id,
    name: `匿名ページ${id}`,
    sheet_no: String(id),
    page_no: String(id),
    prediction_count: 0,
    analysis_wire_count: null,
    analysis_connection_count: null,
    revision: null,
    status: 'draft',
    assignee: null,
    image_width: 1000,
    image_height: 800,
    symbol_count: 2,
    connection_count: 0,
    terminal_count: 0,
    updated_at: null,
  }
}

function renderEditor(entry = '/projects/1') {
  const router = createMemoryRouter(
    [
      { path: '/', element: <div>図面一覧</div> },
      { path: '/projects/:id', element: <EditorPage /> },
    ],
    { initialEntries: [entry] },
  )
  return { ...render(<RouterProvider router={router} />), router }
}

beforeEach(() => {
  vi.clearAllMocks()
  window.sessionStorage.clear()
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView })
  for (const projectId of [1, 2]) {
    clearPersistedProjectSave('annotations', projectId)
    clearPersistedProjectSave('meta', projectId)
  }
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({
      matches: true,
      media: '',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    callback(0)
    return 1
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 800,
    height: 800,
    left: 0,
    right: 1000,
    top: 0,
    width: 1000,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  })
  apiMocks.listClasses.mockResolvedValue([symbolClass])
  apiMocks.attributeDefinitions.mockResolvedValue({
    symbol: { common: [{ key: 'maker', label: 'メーカー', type: 'text', unit: null }], by_class: {} },
    wire: [{ key: 'length_m', label: '長さ', type: 'number', unit: 'm' }],
  })
  apiMocks.listProjects.mockResolvedValue([row(1), row(2)])
  apiMocks.getProject.mockImplementation(async (id: number) => project(id))
  apiMocks.saveAnnotations.mockResolvedValue({ symbol_count: 2, connection_count: 0, skipped: [] })
  apiMocks.updateMeta.mockResolvedValue({ id: 1, status: 'draft' })
  apiMocks.exportProject.mockResolvedValue({ name: 'anonymous.zip', size: 10 })
  apiMocks.getReviewSummary.mockResolvedValue(reviewSummary)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})


/* 図面 1000×800 をキャンバス 1000×800 に合わせると、倍率 0.95・左上 (25, 20) に置かれる（1 px = 0.95 画面 px） */
const px = (nx: number) => 25 + nx * 950
const py = (ny: number) => 20 + ny * 760

function capturePointers() {
  const captured = new Set<number>()
  Object.defineProperties(HTMLElement.prototype, {
    setPointerCapture: { configurable: true, value: (id: number) => captured.add(id) },
    releasePointerCapture: { configurable: true, value: (id: number) => captured.delete(id) },
    hasPointerCapture: { configurable: true, value: (id: number) => captured.has(id) },
  })
}

async function openCanvas(entry?: string, mode = 'v') {
  capturePointers()
  const view = renderEditor(entry)
  const canvas = await view.findByRole('region', { name: '図面アノテーションキャンバス' })
  fireEvent.keyDown(window, { key: mode })
  const down = (nx: number, ny: number, extra: Record<string, unknown> = {}) =>
    fireEvent.pointerDown(canvas, { pointerId: 1, button: 0, clientX: px(nx), clientY: py(ny), ...extra })
  const move = (nx: number, ny: number, pointerId = 1) => fireEvent.pointerMove(window, { pointerId, clientX: px(nx), clientY: py(ny) })
  const up = (pointerId = 1) => fireEvent.pointerUp(window, { pointerId })
  return { ...view, canvas, down, move, up }
}

const rects = (container: HTMLElement) => [...container.querySelectorAll('svg rect[stroke="#0891b2"]')]
const box = (container: HTMLElement, index: number) => {
  const r = rects(container)[index]
  return ['x', 'y', 'width', 'height'].map((k) => Math.round(Number(r.getAttribute(k))))
}

describe('EditorPage 図面の上の操作', () => {
  it('選択モードで角をつまんで大きさを変え、中をつまんで動かす。小さすぎる大きさにはしない', async () => {
    const v = await openCanvas()
    expect(box(v.container, 0)).toEqual([240, 288, 120, 64])
    // 右下の角
    v.down(0.36, 0.44)
    v.move(0.40, 0.48)
    v.up()
    expect(box(v.container, 0)).toEqual([240, 288, 160, 96])
    // 左上の角
    v.down(0.24, 0.36)
    v.move(0.20, 0.34)
    v.up()
    expect(box(v.container, 0)).toEqual([200, 272, 200, 112])
    // 右上の角
    v.down(0.40, 0.34)
    v.move(0.42, 0.30)
    v.up()
    expect(box(v.container, 0)).toEqual([200, 240, 220, 144])
    // 左下の角
    v.down(0.20, 0.48)
    v.move(0.18, 0.50)
    // つぶれるほど小さくはしない
    v.move(0.42, 0.50)
    v.up()
    expect(box(v.container, 0)).toEqual([180, 240, 240, 160])
    // 中をつまんで動かす。ほかの指の動きは無視する
    v.down(0.30, 0.40)
    v.move(0.50, 0.40, 9)
    v.move(0.35, 0.45)
    fireEvent.pointerUp(window, { pointerId: 9 })
    fireEvent.pointerCancel(window, { pointerId: 9 })
    fireEvent.pointerCancel(window, { pointerId: 1 })
    expect(box(v.container, 0)).toEqual([230, 280, 240, 160])
    expect(v.getByRole('button', { name: /^SYM-0001リレー$/ }).getAttribute('aria-expanded')).toBe('true')
    // 取り消し・やり直しで戻せる
    fireEvent.click(v.getByRole('button', { name: '元に戻す' }))
    expect(box(v.container, 0)).toEqual([180, 240, 240, 160])
    fireEvent.click(v.getByRole('button', { name: 'やり直す' }))
    expect(box(v.container, 0)).toEqual([230, 280, 240, 160])
  })

  it('何も無いところ・中ボタン・Alt でつまむと図面を動かし、右クリックや部品の上では何もしない', async () => {
    const v = await openCanvas()
    const layer = () => (v.container.querySelector('[style*="translate"]') as HTMLElement).style.transform
    const start = layer()
    v.down(0.95, 0.9)
    expect(v.canvas.style.cursor).toBe('grabbing')
    // つまんでいる間のもう一本の指は無視する
    fireEvent.pointerDown(v.canvas, { pointerId: 2, button: 0, clientX: 10, clientY: 10 })
    fireEvent.pointerMove(window, { pointerId: 1, clientX: px(0.95) + 30, clientY: py(0.9) + 10 })
    v.up()
    expect(layer()).toBe('translate(55px, 30px) scale(0.95)')
    fireEvent.pointerDown(v.canvas, { pointerId: 3, button: 1, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(window, { pointerId: 3, clientX: -30, clientY: 0 })
    fireEvent.pointerUp(window, { pointerId: 3 })
    fireEvent.pointerDown(v.canvas, { pointerId: 4, button: 0, altKey: true, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(window, { pointerId: 4, clientX: 0, clientY: -10 })
    fireEvent.pointerUp(window, { pointerId: 4 })
    expect(layer()).toBe(start)
    fireEvent.pointerDown(v.canvas, { pointerId: 5, button: 2, clientX: 0, clientY: 0 })
    fireEvent.pointerDown(v.getByRole('button', { name: '拡大' }), { pointerId: 6, button: 0 })
    fireEvent.pointerMove(window, { pointerId: 5, clientX: 100, clientY: 100 })
    expect(layer()).toBe(start)
  })

  it('ホイールと拡大・縮小・全体表示のボタンで倍率を変える', async () => {
    const v = await openCanvas()
    expect(v.getByText('95%')).toBeTruthy()
    fireEvent.wheel(v.canvas, { deltaY: -100, clientX: 500, clientY: 400 })
    expect(v.getByText('106%')).toBeTruthy()
    fireEvent.wheel(v.canvas, { deltaY: 100, clientX: 500, clientY: 400 })
    expect(v.getByText('95%')).toBeTruthy()
    for (let i = 0; i < 30; i += 1) fireEvent.click(v.getByRole('button', { name: '拡大' }))
    expect(v.getByText('1200%')).toBeTruthy()
    for (let i = 0; i < 30; i += 1) fireEvent.click(v.getByRole('button', { name: '縮小' }))
    expect(v.getByText('5%')).toBeTruthy()
    fireEvent.click(v.getByRole('button', { name: /全体表示/ }))
    expect(v.getByText('95%')).toBeTruthy()
  })

  it('シンボルモードでドラッグして枠を描き、小さすぎる枠は作らない', async () => {
    const v = await openCanvas(undefined, 'b')
    expect(v.canvas.style.cursor).toBe('crosshair')
    v.down(0.5, 0.6)
    v.move(0.6, 0.7)
    expect(v.container.querySelector('rect[stroke-dasharray]')).toBeTruthy()
    v.up()
    expect(v.getByRole('button', { name: /^SYM-0003リレー$/ })).toBeTruthy()
    v.down(0.8, 0.8)
    v.up()
    expect(v.queryByRole('button', { name: /^SYM-0004/ })).toBeNull()
  })

  it('端子モードでシンボルを押すと端子を足し、何も無いところでは足さない', async () => {
    const v = await openCanvas()
    fireEvent.keyDown(window, { key: 't' })
    v.down(0.9, 0.9)
    v.down(0.31, 0.41)
    const row = v.getByRole('button', { name: /^SYM-0001リレー$/ })
    expect(row.getAttribute('aria-expanded')).toBe('true')
    expect(v.container.querySelector('input[value="1"]')).toBeTruthy()
  })

  it('配線モードで始点と終点を押して配線にし、端子があれば端子どうしをつなぐ。同じ配線は二重に作らない', async () => {
    apiMocks.getProject.mockImplementation(async (id: number) => {
      const p = project(id)
      p.symbols[0] = { ...p.symbols[0], terminals: [{ ref: 'SYM-0001-T1', name: '1', tx: 0.3, ty: 0.4 }] }
      return p
    })
    const v = await openCanvas()
    fireEvent.keyDown(window, { key: 'c' })
    v.down(0.3, 0.4)
    expect(v.getByRole('status').textContent).toContain('始点: SYM-0001:T1 → 終点をクリック')
    v.down(0.9, 0.9)
    expect(v.queryByText(/始点:/)).toBeNull()
    v.down(0.3, 0.4)
    v.down(0.3, 0.4)
    expect(v.queryByRole('button', { name: /SYM-0001:T1→SYM-0001:T1/ })).toBeNull()
    v.down(0.3, 0.4)
    v.down(0.7, 0.4)
    expect(v.getByRole('button', { name: 'SYM-0001:T1→SYM-0002' })).toBeTruthy()
    expect(v.container.querySelectorAll('line[opacity="0.85"]')).toHaveLength(1)
    v.down(0.3, 0.4)
    v.down(0.7, 0.4)
    expect(v.getByRole('status').textContent).toBe('同じ配線が既に登録されています')

    // 端子レイヤーを消すと端子ではなくシンボルにつなぐ
    fireEvent.click(v.getByRole('button', { name: '端子を非表示' }))
    v.down(0.3, 0.4)
    expect(v.getByRole('status').textContent).toContain('始点: SYM-0001 → 終点')
    // 始点を隠すと保留も外す
    fireEvent.click(v.getByRole('tab', { name: /^シンボル/ }))
    fireEvent.click(v.getByRole('button', { name: 'SYM-0001を非表示' }))
    expect(v.queryByText(/始点:/)).toBeNull()
    // 種別ごと隠したときも保留と選択を外す
    fireEvent.click(v.getByRole('button', { name: 'SYM-0001を表示' }))
    v.down(0.3, 0.4)
    fireEvent.click(v.getByRole('button', { name: /^SYM-0002リレー$/ }))
    fireEvent.click(v.getByRole('button', { name: 'リレーを非表示' }))
    expect(v.queryByText(/始点:/)).toBeNull()
    expect(v.getByRole('button', { name: /^SYM-0002リレー$/ }).getAttribute('aria-expanded')).toBe('false')
    // 端子の無い配線は端点を描かない（端子を消したら端点はシンボルの中心）
    fireEvent.click(v.getByRole('button', { name: 'すべて表示' }))
    expect(v.container.querySelectorAll('line[opacity="0.85"]')).toHaveLength(1)
  })

  it('別の種別だけ隠しても、選んでいるシンボルと保留中の始点はそのまま', async () => {
    apiMocks.getProject.mockImplementation(async (id: number) => {
      const p = project(id)
      p.symbols[1] = { ...p.symbols[1], class_key: 'lamp', origin: 'inference' }
      return p
    })
    const v = await openCanvas()
    fireEvent.keyDown(window, { key: 'c' })
    v.down(0.3, 0.4)
    fireEvent.click(v.getByRole('button', { name: /^SYM-0001リレー$/ }))
    fireEvent.click(v.getByRole('button', { name: 'lampを非表示' }))
    expect(v.getByText(/始点: SYM-0001/)).toBeTruthy()
    expect(v.getByRole('button', { name: /^SYM-0001リレー$/ }).getAttribute('aria-expanded')).toBe('true')
    // 別のシンボルを隠しても選択は外さない
    fireEvent.click(v.getByRole('button', { name: 'lampを表示' }))
    fireEvent.click(v.getByRole('button', { name: 'SYM-0002を非表示' }))
    expect(v.getByRole('button', { name: /^SYM-0001リレー$/ }).getAttribute('aria-expanded')).toBe('true')
    // 隠したシンボルを消すと隠した印も消す
    fireEvent.click(v.getByRole('button', { name: 'SYM-0002を削除' }))
    expect(v.getByRole('button', { name: 'すべて表示' })).toHaveProperty('disabled', true)
  })
})

describe('EditorPage 読み込み・保存・その他', () => {
  it('読み込めなければ理由と一覧へ戻るリンクを出す', async () => {
    apiMocks.getProject.mockRejectedValueOnce(new Error('図面がありません'))
    const v = renderEditor()
    expect(await v.findByText('図面がありません')).toBeTruthy()
    expect(v.getByRole('link', { name: /一覧に戻る/ })).toBeTruthy()
    v.unmount()
    apiMocks.getProject.mockRejectedValueOnce('文字列の失敗')
    const again = renderEditor()
    expect(await again.findByText('文字列の失敗')).toBeTruthy()
  })

  it('種別が無ければ空の種別で始め、未登録の種別は既定の色と名前で出す', async () => {
    apiMocks.listClasses.mockResolvedValue([])
    const v = await openCanvas()
    expect(v.getByRole('button', { name: /^SYM-0001relay$/ })).toBeTruthy()
    expect(rects(v.container)).toHaveLength(0)
    expect(v.container.querySelectorAll('svg rect[stroke="#0055a4"]')).toHaveLength(2)
  })

  it('?symbol= で開くとそのシンボルを選んで中央に寄せ、無い参照名なら何もしない', async () => {
    const v = await openCanvas('/projects/1?symbol=SYM-0002')
    await waitFor(() => expect(v.getByRole('button', { name: /^SYM-0002リレー$/ }).getAttribute('aria-expanded')).toBe('true'))
    v.unmount()
    const none = await openCanvas('/projects/1?symbol=NOPE')
    expect(none.getByRole('button', { name: /^SYM-0001リレー$/ }).getAttribute('aria-expanded')).toBe('false')
  })

  it('コイルを通り抜ける配線を分け、分けるものが無い・失敗したときを伝える', async () => {
    apiMocks.getReviewSummary.mockResolvedValue({
      ...reviewSummary,
      coils: { count: 1, issues: [{ symbol_ref: 'SYM-0001', class_key: 'relay', label: '', kind: 'through', message: '通り抜け' }] },
    })
    apiMocks.splitCoilWires
      .mockResolvedValueOnce({
        counts: { split: 1 },
        symbols: [
          { ...project(1).symbols[0], terminals: [{ ref: 'SYM-0001-T1', name: '1', tx: 0.24, ty: 0.4 }, { ref: 'SYM-0001-T2', name: '2', tx: 0.36, ty: 0.4 }] },
          project(1).symbols[1],
        ],
        connections: [
          { from_symbol_ref: 'SYM-0002', from_terminal_ref: null, to_symbol_ref: 'SYM-0001', to_terminal_ref: 'SYM-0001-T1', wire_no: null, net_id: null, kind: 'wire', external_ref: null, note: null },
        ],
      })
      .mockResolvedValueOnce({ counts: { split: 0 }, symbols: [], connections: [] })
      .mockRejectedValueOnce(new Error('分けられません'))
      .mockRejectedValueOnce('文字列')
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    const split = await v.findByRole('button', { name: /通り抜ける配線を分けて端子を付ける/ })
    fireEvent.click(split)
    expect(await v.findByText('コイル 1 個の通り抜ける配線を分け、端子を付けました')).toBeTruthy()
    expect(apiMocks.splitCoilWires).toHaveBeenCalledWith(expect.objectContaining({ symbols: expect.any(Array) }), 1000, 800)
    fireEvent.click(v.getByRole('tab', { name: /^配線/ }))
    expect(v.getByRole('button', { name: 'SYM-0002→SYM-0001:T1' })).toBeTruthy()
    expect(v.getByText('W-0001')).toBeTruthy()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    fireEvent.click(await v.findByRole('button', { name: /通り抜ける配線を分けて/ }))
    expect(await v.findByText(/分けられる配線はありませんでした/)).toBeTruthy()
    fireEvent.click(v.getByRole('button', { name: /通り抜ける配線を分けて/ }))
    expect((await v.findByRole('alert')).textContent).toContain('分けられません')
    fireEvent.click(v.getByRole('button', { name: /通り抜ける配線を分けて/ }))
    await waitFor(() => expect(v.getByRole('alert').textContent).toContain('文字列'))
  })

  it('AI 取り込みと書き出しの失敗を出す', async () => {
    apiMocks.aiImport.mockRejectedValueOnce(new Error('解析結果を読めません')).mockRejectedValueOnce('文字列')
    apiMocks.exportProject.mockRejectedValueOnce('書き出せない')
    const v = await openCanvas()
    fireEvent.click(v.getByRole('button', { name: /AI 取込/ }))
    expect((await v.findByRole('alert')).textContent).toContain('解析結果を読めません')
    fireEvent.click(v.getByRole('button', { name: /AI 取込/ }))
    await waitFor(() => expect(v.getByRole('alert').textContent).toContain('文字列'))
    fireEvent.click(v.getByRole('button', { name: 'この図面を書き出し' }))
    await waitFor(() => expect(v.getByRole('alert').textContent).toContain('書き出せない'))
  })

  it('保存できなかった配線の数を知らせ、取り消す履歴が無ければ何もしない', async () => {
    apiMocks.saveAnnotations.mockResolvedValue({ symbol_count: 3, connection_count: 0, skipped: ['x', 'y'] })
    const v = await openCanvas()
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true, shiftKey: true })
    expect(v.getAllByRole('button', { name: /^SYM-\d+リレー$/ })).toHaveLength(2)
    fireEvent.keyDown(window, { key: 'b' })
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    expect(await v.findByText('2 件の配線を保存できませんでした')).toBeTruthy()
  })

  it('画面幅が変わると詳細パネルの出し方を切り替える', async () => {
    const listeners: ((e: { matches: boolean }) => void)[] = []
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn(() => ({ matches: true, addEventListener: (_: string, cb: (e: { matches: boolean }) => void) => listeners.push(cb), removeEventListener: vi.fn() })),
    })
    const v = await openCanvas()
    expect(v.queryByRole('dialog')).toBeNull()
    act(() => listeners.forEach((cb) => cb({ matches: false })))
    expect(v.getByRole('dialog', { name: 'アノテーション詳細' })).toBeTruthy()
    expect(v.canvas.getAttribute('aria-hidden')).toBe('true')
  })

  it('パネル・レイヤー・操作ヒント・モードをボタンで切り替え、隠した数をバッジに出す', async () => {
    const v = await openCanvas()
    fireEvent.click(v.getByRole('button', { name: '端子を非表示' }))
    fireEvent.click(v.getByRole('button', { name: '配線を非表示' }))
    fireEvent.click(v.getByRole('button', { name: 'ラベル（TAG）を非表示' }))
    expect(v.getByTitle('表示をオフにしたアノテーション 3 件')).toBeTruthy()
    fireEvent.click(v.getByRole('button', { name: '配線を表示' }))
    expect(v.getByTitle('表示をオフにしたアノテーション 2 件')).toBeTruthy()
    fireEvent.click(v.getByRole('button', { name: /レイヤー/ }))
    expect(v.queryByRole('group', { name: 'アノテーションのレイヤー' })).toBeNull()
    fireEvent.click(v.getByRole('button', { name: /操作ヒント/ }))
    expect(v.getByText('図面操作')).toBeTruthy()
    fireEvent.click(v.getByRole('button', { name: '端子（Tキー）' }))
    expect(v.getByRole('button', { name: '端子（Tキー）' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(v.getAllByRole('button', { name: '詳細パネルを閉じる' }).at(-1)!)
    expect(v.queryByRole('complementary', { name: 'アノテーション詳細' })).toBeNull()
    fireEvent.click(v.getByRole('button', { name: /^詳細$/ }))
    expect(within(v.getByRole('complementary', { name: 'アノテーション詳細' })).getByText('選択内容と図面情報')).toBeTruthy()
  })

  it('書き出しの完了を知らせる', async () => {
    const v = await openCanvas()
    fireEvent.click(v.getByRole('button', { name: 'この図面を書き出し' }))
    expect(await v.findByText('この図面のデータを書き出しました')).toBeTruthy()
    expect(apiMocks.exportProject).toHaveBeenCalledWith(1)
  })

  it('つながる先のシンボルや端子が無い配線は、描けるところだけ描く', async () => {
    apiMocks.getProject.mockImplementation(async (id: number) => ({
      ...project(id),
      connections: [
        { from_symbol_ref: 'SYM-0001', from_terminal_ref: 'SYM-0001-T9', to_symbol_ref: 'SYM-0002', to_terminal_ref: null, wire_no: null, net_id: null, kind: 'wire', external_ref: null, note: null },
        { from_symbol_ref: 'GONE', from_terminal_ref: null, to_symbol_ref: 'SYM-0002', to_terminal_ref: null, wire_no: null, net_id: null, kind: 'wire', external_ref: null, note: null },
      ],
    }))
    const v = await openCanvas()
    const lines = v.container.querySelectorAll('line[opacity="0.85"]')
    expect(lines).toHaveLength(1)
    expect(lines[0].getAttribute('x1')).toBe('300')
    // シンボルを消すと、それにつながる配線も消す（選んでいないシンボルを消しても選択はそのまま）
    fireEvent.click(v.getByRole('button', { name: /^SYM-0001リレー$/ }))
    fireEvent.click(v.getByRole('button', { name: 'SYM-0002を削除' }))
    expect(v.getByRole('tab', { name: '配線 (0)' })).toBeTruthy()
    expect(v.getByRole('button', { name: /^SYM-0001リレー$/ }).getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(v.getByRole('button', { name: 'SYM-0001を削除' }))
    expect(v.getByRole('tab', { name: 'シンボル (0)' })).toBeTruthy()
  })

  it('取り消しの履歴は 100 件までにする', async () => {
    const v = await openCanvas()
    fireEvent.click(v.getByRole('button', { name: /^SYM-0001リレー$/ }))
    for (let i = 0; i < 102; i += 1) fireEvent.keyDown(v.canvas, { key: 'ArrowRight', altKey: true })
    for (let i = 0; i < 101; i += 1) fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    // 最初の 2 回の動きは履歴から落ちている
    expect(box(v.container, 0)[0]).toBe(242)
    expect(v.getByRole('button', { name: '元に戻す' })).toHaveProperty('disabled', true)
  })

  it('狭い画面では読める倍率より小さくしない', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      bottom: 300, height: 300, left: 0, right: 300, top: 0, width: 300, x: 0, y: 0, toJSON: () => ({}),
    })
    const v = await openCanvas()
    expect(v.getByText('34%')).toBeTruthy()
    fireEvent.click(v.getByRole('button', { name: /全体表示/ }))
    expect(v.getByText('26%')).toBeTruthy()
  })

  it('読み込み中の Ctrl+S では何もしない', async () => {
    let resolve: (p: ProjectDetail) => void = () => undefined
    apiMocks.getProject.mockReturnValueOnce(new Promise((r) => { resolve = r }))
    const v = renderEditor()
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    fireEvent.keyDown(window, { key: 'Escape' })
    await act(async () => resolve(project(1)))
    await v.findByRole('region', { name: '図面アノテーションキャンバス' })
    expect(apiMocks.saveAnnotations).not.toHaveBeenCalled()
  })

  it('一時保存できない間はページを閉じる前に確かめ、保存を再試行できる', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    apiMocks.saveAnnotations.mockRejectedValueOnce(new Error('サーバーに繋がりません'))
    const v = await openCanvas(undefined, 'b')
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    expect(await v.findByText(/ブラウザの一時保存領域を利用できません/)).toBeTruthy()
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))
    fireEvent.click(v.getByRole('button', { name: '保存を再試行' }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(v.queryByText(/ブラウザの一時保存領域を利用できません/)).toBeNull())
  })

  it('別のページへ移った後に終わった図面情報の保存は、今のページの表示を変えない', async () => {
    let resolveMeta: (value: unknown) => void = () => undefined
    apiMocks.updateMeta.mockReturnValueOnce(new Promise((r) => { resolveMeta = r })).mockRejectedValueOnce('文字列の失敗')
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    fireEvent.change(v.getByRole('textbox', { name: '名称' }), { target: { value: '変えた名前' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    fireEvent.click(v.getByRole('link', { name: '次の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenLastCalledWith(2))
    await v.findByText('匿名ページ2')
    await act(async () => resolveMeta({ id: 1 }))
    expect(v.queryByRole('alert')).toBeNull()

    // 今のページの保存が文字列で失敗したとき
    fireEvent.click(await v.findByRole('tab', { name: '図面情報' }))
    fireEvent.change(v.getByRole('textbox', { name: '名称' }), { target: { value: '2 ページ目の名前' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    expect((await v.findByRole('alert')).textContent).toContain('文字列の失敗')
  })

  it('図面情報を保存中にもう一度保存を押しても重ねて送らず、元の値へ戻したら待っている保存を取り消す', async () => {
    let resolveMeta: (value: unknown) => void = () => undefined
    apiMocks.updateMeta.mockReturnValueOnce(new Promise((r) => { resolveMeta = r }))
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    const name = () => v.getByRole('textbox', { name: '名称' })
    fireEvent.change(name(), { target: { value: 'A' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    fireEvent.change(name(), { target: { value: 'B' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1)
    // 保存中の A と同じ値に戻すと、待っている B は送らず A の完了を待つ
    fireEvent.change(name(), { target: { value: 'A' } })
    await act(async () => resolveMeta({ id: 1 }))
    await waitFor(() => expect(v.getByRole('button', { name: '図面情報は自動保存済み' })).toBeTruthy())
    expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1)

    // 入力の途中（自動保存の前）に元へ戻したら送らない
    fireEvent.change(name(), { target: { value: 'C' } })
    fireEvent.change(name(), { target: { value: 'A' } })
    await new Promise((r) => setTimeout(r, 450))
    expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1)
  })

  it('保存するものが無いときの Ctrl+S は送らず、失敗した保存は Ctrl+S で送り直す', async () => {
    apiMocks.saveAnnotations.mockRejectedValueOnce('文字列の失敗')
    const v = await openCanvas(undefined, 'b')
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await new Promise((r) => setTimeout(r, 0))
    expect(apiMocks.saveAnnotations).not.toHaveBeenCalled()
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    expect((await v.findByRole('alert')).textContent).toContain('文字列の失敗')
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(v.getByRole('button', { name: '自動保存済み' })).toBeTruthy())
  })

  it('保存中にさらに変えたら、終わった後に続けて保存し、その間は「変更を検出」と出す', async () => {
    let resolveSave: (value: unknown) => void = () => undefined
    apiMocks.saveAnnotations.mockReturnValueOnce(new Promise((r) => { resolveSave = r }))
    const v = await openCanvas(undefined, 'b')
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await act(async () => resolveSave({ symbol_count: 3, connection_count: 0, skipped: [] }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(2))
    expect(apiMocks.saveAnnotations.mock.calls[1][1].symbols).toHaveLength(4)
  })

  it('開く前の下書きがサーバーの内容と同じなら捨てる', async () => {
    const server = project(1)
    const annotationKey = JSON.stringify(buildAnnotationPayload(server.symbols, server.connections))
    const meta = buildMetaForm(server)
    window.sessionStorage.setItem('seq-annotator:project-draft:annotations:1', JSON.stringify({ projectId: 1, key: annotationKey, payload: JSON.parse(annotationKey) }))
    window.sessionStorage.setItem('seq-annotator:project-draft:meta:1', JSON.stringify({ projectId: 1, key: JSON.stringify(meta), payload: meta }))
    const v = await openCanvas()
    expect(v.getByRole('button', { name: '自動保存済み' })).toBeTruthy()
    expect(window.sessionStorage.getItem('seq-annotator:project-draft:annotations:1')).toBeNull()
    expect(window.sessionStorage.getItem('seq-annotator:project-draft:meta:1')).toBeNull()
  })

  it('読み込みの途中で別のページへ移ったら、古い応答も古い失敗も使わない', async () => {
    let resolveOld: (p: ProjectDetail) => void = () => undefined
    let rejectOld: (e: unknown) => void = () => undefined
    apiMocks.getProject
      .mockReturnValueOnce(new Promise((r) => { resolveOld = r }))
    const v = renderEditor()
    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    await act(async () => resolveOld(project(1)))
    expect(v.getByText('匿名ページ2')).toBeTruthy()

    apiMocks.getProject.mockReturnValueOnce(new Promise((_, r) => { rejectOld = r }))
    await act(() => v.router.navigate('/projects/1'))
    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    await act(async () => rejectOld(new Error('古い失敗')))
    expect(v.queryByRole('alert')).toBeNull()
  })
})

describe('EditorPage 保存の順番', () => {
  const deferred = () => {
    let resolve: (value: unknown) => void = () => undefined
    const promise = new Promise((r) => { resolve = r })
    return { promise, resolve }
  }

  it('保存中にページを行き来しても、戻ったページは待っている保存と保存中の状態を引き継ぐ', async () => {
    const annotation = deferred()
    const meta = deferred()
    apiMocks.saveAnnotations.mockReturnValueOnce(annotation.promise)
    apiMocks.updateMeta.mockReturnValueOnce(meta.promise)
    const v = await openCanvas(undefined, 'b')
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    fireEvent.change(v.getByRole('textbox', { name: '名称' }), { target: { value: 'A' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    fireEvent.change(v.getByRole('textbox', { name: '名称' }), { target: { value: 'B' } })

    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    await act(() => v.router.navigate('/projects/1'))
    expect(await v.findByRole('button', { name: '変更を検出' })).toBeTruthy()
    expect(v.getByRole('button', { name: '図面情報に変更あり' })).toBeTruthy()
    expect(rects(v.container)).toHaveLength(4)

    await act(async () => annotation.resolve({ symbol_count: 3, connection_count: 0, skipped: [] }))
    await act(async () => meta.resolve({ id: 1 }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(2))
    expect(apiMocks.updateMeta.mock.calls[1][1].name).toBe('B')
    await waitFor(() => expect(v.getByRole('button', { name: '自動保存済み' })).toBeTruthy())
  })

  it('別のページへ移った後に終わった保存は、今のページで待っている保存へ続け、状態は今のページのものを出す', async () => {
    const annotation = deferred()
    apiMocks.saveAnnotations.mockReturnValueOnce(annotation.promise)
    const v = await openCanvas(undefined, 'b')
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))
    // 図面情報は自動保存の待ち時間のうちに別のページへ移る
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    fireEvent.change(v.getByRole('textbox', { name: '名称' }), { target: { value: '1 ページ目の名前' } })
    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledWith(1, expect.objectContaining({ name: '1 ページ目の名前' })))
    fireEvent.keyDown(v.canvas, { key: 'b' })
    fireEvent.keyDown(await v.findByRole('region', { name: '図面アノテーションキャンバス' }), { key: 'Enter' })
    expect(await v.findByRole('button', { name: '変更を検出' })).toBeTruthy()
    await act(async () => annotation.resolve({ symbol_count: 3, connection_count: 0, skipped: [] }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(2))
    expect(apiMocks.saveAnnotations.mock.calls[1][0]).toBe(2)
    await waitFor(() => expect(v.getByRole('button', { name: '自動保存済み' })).toBeTruthy())
  })

  it('保存中のものと同じ内容へ戻した保存は、前の保存が終われば送らずに済ませる', async () => {
    const annotation = deferred()
    const meta = deferred()
    apiMocks.saveAnnotations.mockReturnValueOnce(annotation.promise)
    apiMocks.updateMeta.mockReturnValueOnce(meta.promise)
    const v = await openCanvas(undefined, 'b')
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    await waitFor(() => expect(rects(v.container)).toHaveLength(3))

    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    const name = () => v.getByRole('textbox', { name: '名称' })
    fireEvent.change(name(), { target: { value: 'A' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    fireEvent.change(name(), { target: { value: 'B' } })
    fireEvent.change(name(), { target: { value: 'A' } })

    await act(async () => annotation.resolve({ symbol_count: 3, connection_count: 0, skipped: [] }))
    await act(async () => meta.resolve({ id: 1 }))
    await new Promise((r) => setTimeout(r, 450))
    expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1)
    expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(v.getByRole('button', { name: '自動保存済み' })).toBeTruthy())
    expect(v.getByRole('button', { name: '図面情報は自動保存済み' })).toBeTruthy()
  })

  it('同じ内容へ戻した図面情報の保存を、別のページへ移った後に済ませても今のページの表示は変えない', async () => {
    const meta = deferred()
    apiMocks.updateMeta.mockReturnValueOnce(meta.promise)
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    const name = () => v.getByRole('textbox', { name: '名称' })
    fireEvent.change(name(), { target: { value: 'A' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    fireEvent.change(name(), { target: { value: 'B' } })
    fireEvent.change(name(), { target: { value: 'A' } })
    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    await act(async () => meta.resolve({ id: 1 }))
    await new Promise((r) => setTimeout(r, 450))
    expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1)
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    expect(v.getByRole('button', { name: '図面情報は自動保存済み' })).toBeTruthy()
  })

  it('同じ内容へ戻した保存を済ませた後も、別のページで待っている保存は続けて送る', async () => {
    const meta = deferred()
    apiMocks.updateMeta.mockReturnValueOnce(meta.promise)
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    const name = () => v.getByRole('textbox', { name: '名称' })
    fireEvent.change(name(), { target: { value: 'A' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    fireEvent.change(name(), { target: { value: 'B' } })
    fireEvent.change(name(), { target: { value: 'A' } })
    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    fireEvent.click(await v.findByRole('tab', { name: '図面情報' }))
    fireEvent.change(name(), { target: { value: '2 ページ目' } })
    await new Promise((r) => setTimeout(r, 450))
    await act(async () => meta.resolve({ id: 1 }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(2))
    expect(apiMocks.updateMeta.mock.calls[1]).toEqual([2, expect.objectContaining({ name: '2 ページ目' })])
  })

  it.each([
    ['移った先で何も変えなければ、移った先の表示は変えない', false],
    ['移った先でも変えていれば、そちらを続けて送る', true],
  ])('保存中のものと同じ内容へ戻してから別のページへ移ったとき: %s', async (_, editOnSecondPage) => {
    const annotation = deferred()
    apiMocks.saveAnnotations.mockReturnValueOnce(annotation.promise)
    const v = await openCanvas(undefined, 'b')
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 's', ctrlKey: true })
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))
    fireEvent.keyDown(v.canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    await waitFor(() => expect(rects(v.container)).toHaveLength(3))
    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    if (editOnSecondPage) {
      fireEvent.keyDown(window, { key: 'b' })
      fireEvent.keyDown(await v.findByRole('region', { name: '図面アノテーションキャンバス' }), { key: 'Enter' })
      await new Promise((r) => setTimeout(r, 450))
    }
    await act(async () => annotation.resolve({ symbol_count: 3, connection_count: 0, skipped: [] }))
    await new Promise((r) => setTimeout(r, 50))
    expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(editOnSecondPage ? 2 : 1)
    if (editOnSecondPage) expect(apiMocks.saveAnnotations.mock.calls[1][0]).toBe(2)
    await waitFor(() => expect(v.getByRole('button', { name: '自動保存済み' })).toBeTruthy())
  })

  it('図面情報を一時保存できないときは、閉じる前に確かめる警告を出す', async () => {
    const v = await openCanvas()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    fireEvent.change(v.getByRole('textbox', { name: '名称' }), { target: { value: '一時保存できない名前' } })
    expect(await v.findByText(/ブラウザの一時保存領域を利用できません/)).toBeTruthy()
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
  })

  it('図面情報を保存中にもう一度保存を押すと「保存中」と出し、待っている変更は押したときにすぐ送る', async () => {
    const first = deferred()
    const second = deferred()
    apiMocks.updateMeta.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    const name = () => v.getByRole('textbox', { name: '名称' })
    fireEvent.change(name(), { target: { value: 'A' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    fireEvent.click(v.getByRole('button', { name: '図面情報を自動保存中…' }))
    expect(v.getByRole('button', { name: '図面情報を自動保存中…' })).toBeTruthy()
    // 保存中に変えた B は待ち時間が過ぎても送らず、押せば前の保存の後にすぐ送る
    fireEvent.change(name(), { target: { value: 'B' } })
    await new Promise((r) => setTimeout(r, 450))
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1)
    await act(async () => first.resolve({ id: 1 }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(2))
    // 2 回目の保存中にページを行き来すると、戻った後も「保存中」と出す
    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    await act(() => v.router.navigate('/projects/1'))
    fireEvent.click(await v.findByRole('tab', { name: '図面情報' }))
    expect(await v.findByRole('button', { name: '図面情報を自動保存中…' })).toBeTruthy()
    expect(name()).toHaveProperty('value', 'B')
    await act(async () => second.resolve({ id: 1 }))
    await waitFor(() => expect(v.getByRole('button', { name: '図面情報は自動保存済み' })).toBeTruthy())
  })

  it('開く前に残っていた図面情報の下書きがサーバーと違えば、送れていない変更として出す', async () => {
    const meta = { ...buildMetaForm(project(1)), name: '送れていなかった名前' }
    window.sessionStorage.setItem('seq-annotator:project-draft:meta:1', JSON.stringify({ projectId: 1, key: JSON.stringify(meta), payload: meta }))
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    expect(v.getByRole('textbox', { name: '名称' })).toHaveProperty('value', '送れていなかった名前')
    fireEvent.click(v.getByRole('button', { name: '図面情報の保存を再試行' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledWith(1, expect.objectContaining({ name: '送れていなかった名前' })))
  })

  it('図面情報の保存中に始めた読み込みが古い内容を返しても、保存した内容で表示する', async () => {
    const meta = deferred()
    apiMocks.updateMeta.mockReturnValueOnce(meta.promise)
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    fireEvent.change(v.getByRole('textbox', { name: '名称' }), { target: { value: '保存した名前' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    await act(() => v.router.navigate('/projects/2'))
    await v.findByText('匿名ページ2')
    const reload = deferred()
    apiMocks.getProject.mockReturnValueOnce(reload.promise)
    await act(() => v.router.navigate('/projects/1'))
    await act(async () => meta.resolve({ id: 1 }))
    await act(async () => reload.resolve(project(1)))
    fireEvent.click(await v.findByRole('tab', { name: '図面情報' }))
    await waitFor(() => expect(v.getByRole('textbox', { name: '名称' })).toHaveProperty('value', '保存した名前'))
    expect(v.getByRole('button', { name: '図面情報は自動保存済み' })).toBeTruthy()
  })

  it('図面情報の保存が失敗しても、その間に次の変更があれば失敗した分は残さず次を送る', async () => {
    const meta = deferred()
    apiMocks.updateMeta.mockReturnValueOnce(meta.promise.then(() => { throw new Error('一時的な失敗') }))
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    const name = () => v.getByRole('textbox', { name: '名称' })
    fireEvent.change(name(), { target: { value: 'A' } })
    fireEvent.click(v.getByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))
    fireEvent.change(name(), { target: { value: 'B' } })
    await act(async () => meta.resolve(null))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(2))
    expect(apiMocks.updateMeta.mock.calls[1][1].name).toBe('B')
    await waitFor(() => expect(v.getByRole('button', { name: '図面情報は自動保存済み' })).toBeTruthy())
  })

  it('図面情報を変えてすぐ画面を閉じても、待っている保存を送る', async () => {
    const v = await openCanvas()
    fireEvent.click(v.getByRole('tab', { name: '図面情報' }))
    fireEvent.change(v.getByRole('textbox', { name: '名称' }), { target: { value: '閉じる前の名前' } })
    v.unmount()
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledWith(1, expect.objectContaining({ name: '閉じる前の名前' })))
  })
})
