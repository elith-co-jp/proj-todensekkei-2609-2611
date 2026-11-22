// @vitest-environment jsdom

import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CircuitResult } from '../../types'
import DrawingViewer from './DrawingViewer'

const overlay = {
  wires: [{ x1: 0, y1: 0, x2: 1, y2: 1, live: true }, { x1: 0, y1: 1, x2: 1, y2: 0, live: false }],
  coils: [
    { symbol_ref: 'C1', cx: 0.5, cy: 0.5, w: 0.1, h: 0.1, energized: true },
    { symbol_ref: 'C2', cx: 0.2, cy: 0.5, w: 0.1, h: 0.1, energized: false },
  ],
  contacts: [
    { symbol_ref: 'K1', device: 'PB1', kind: 'a', cx: 0.1, cy: 0.1, w: 0.05, h: 0.05, closed: false },
    { symbol_ref: 'K2', device: 'PB2', kind: 'a', cx: 0.2, cy: 0.1, w: 0.05, h: 0.05, closed: true },
    { symbol_ref: 'K3', device: 'X1', kind: 'b', cx: 0.3, cy: 0.1, w: 0.05, h: 0.05, closed: true },
  ],
} as unknown as CircuitResult['overlay'][number]

let resizeCallback: (() => void) | null = null
const disconnect = vi.fn()

beforeEach(() => {
  resizeCallback = null
  vi.stubGlobal('ResizeObserver', class {
    constructor(cb: () => void) { resizeCallback = cb }
    observe() {}
    disconnect() { disconnect() }
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

type Props = Partial<Parameters<typeof DrawingViewer>[0]>
function renderViewer(props: Props = {}) {
  const onToggleInput = vi.fn()
  const all = {
    imageUrl: '/a.png', alt: '図面', width: 800, height: 400, overlay,
    inputs: { PB2: true }, inputDevices: new Set(['PB1', 'PB2']), onToggleInput, ...props,
  }
  const view = render(<DrawingViewer {...all}><div data-overlay-ui>上の部品</div></DrawingViewer>)
  const stage = view.container.firstElementChild as HTMLDivElement
  const zoom = () => view.getByLabelText('表示倍率').textContent
  return { ...view, stage, zoom, onToggleInput, props: all }
}

describe('DrawingViewer', () => {
  it('全体表示から拡大・縮小・キー操作・ダブルクリックで戻す', () => {
    const { getByRole, getByText, stage, zoom } = renderViewer({ insets: { top: 10, right: 20, bottom: 30, left: 40 } })
    // jsdom は大きさが 0 なので 80px の枠に合わせる → 800px 幅の図面は 10%
    expect(zoom()).toBe('10%')
    fireEvent.click(getByRole('button', { name: '拡大' }))
    expect(zoom()).toBe('13%')
    fireEvent.click(getByRole('button', { name: '縮小' }))
    expect(zoom()).toBe('10%')
    fireEvent.keyDown(window, { key: '+' })
    fireEvent.keyDown(window, { key: '=' })
    expect(zoom()).toBe('16%')
    fireEvent.keyDown(window, { key: '-' })
    fireEvent.keyDown(window, { key: '_' })
    expect(zoom()).toBe('10%')
    fireEvent.keyDown(window, { key: '+' })
    fireEvent.keyDown(window, { key: 'F' })
    expect(zoom()).toBe('10%')
    fireEvent.keyDown(window, { key: '+' })
    fireEvent.keyDown(window, { key: 'f' })
    expect(zoom()).toBe('10%')
    // 修飾キー・入力欄・関係ないキーは無視する
    fireEvent.keyDown(window, { key: '+', ctrlKey: true })
    fireEvent.keyDown(window, { key: '+', metaKey: true })
    fireEvent.keyDown(window, { key: '+', altKey: true })
    fireEvent.keyDown(window, { key: 'x' })
    const input = document.createElement('input')
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: '+' })
    input.remove()
    expect(zoom()).toBe('10%')

    fireEvent.click(getByRole('button', { name: '拡大' }))
    fireEvent.doubleClick(getByText('上の部品'))
    expect(zoom()).toBe('13%')
    fireEvent.doubleClick(stage)
    expect(zoom()).toBe('10%')
    fireEvent.click(getByRole('button', { name: '拡大' }))
    fireEvent.click(getByText('全体表示'))
    expect(zoom()).toBe('10%')
  })

  it('ホイールでカーソル位置を中心に拡大し、上限・下限で止める。部品の上では拡大しない', () => {
    const { getByText, stage, zoom } = renderViewer()
    fireEvent.wheel(getByText('上の部品'), { deltaY: -500 })
    expect(zoom()).toBe('10%')
    for (let i = 0; i < 20; i += 1) fireEvent.wheel(stage, { deltaY: -1000, clientX: 10, clientY: 10 })
    expect(zoom()).toBe('800%')
    for (let i = 0; i < 20; i += 1) fireEvent.wheel(stage, { deltaY: 1000 })
    expect(zoom()).toBe('5%')
  })

  it('ドラッグで動かし、少しの揺れはクリックとして接点を切り替える', () => {
    vi.useFakeTimers()
    const { stage, getByRole, getByText, onToggleInput } = renderViewer()
    const capture = vi.fn()
    stage.setPointerCapture = capture
    const inner = stage.firstElementChild as HTMLDivElement
    const before = inner.style.transform

    // 右クリック・部品の上・他の指は無視する
    fireEvent.pointerDown(stage, { button: 2, pointerId: 1, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 50, clientY: 0 })
    fireEvent.pointerDown(getByText('上の部品'), { button: 0, pointerId: 1 })
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 50, clientY: 0 })
    expect(inner.style.transform).toBe(before)

    fireEvent.pointerDown(stage, { button: 0, pointerId: 1, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(stage, { pointerId: 2, clientX: 50, clientY: 0 })
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 2, clientY: 0 })
    expect(inner.style.transform).toBe(before)
    fireEvent.pointerUp(stage, { pointerId: 1 })
    const pb1 = getByRole('button', { name: 'PB1 を ON にする' })
    fireEvent.click(pb1)
    expect(onToggleInput).toHaveBeenCalledWith('PB1')
    expect(getByRole('button', { name: 'PB2 を OFF にする' })).toBeTruthy()

    fireEvent.pointerDown(stage, { button: 0, pointerId: 1, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 30, clientY: 40 })
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 31, clientY: 40 })
    expect(capture).toHaveBeenCalledTimes(1)
    expect(inner.style.transform).not.toBe(before)
    fireEvent.pointerUp(stage, { pointerId: 9 })
    fireEvent.pointerUp(stage, { pointerId: 1 })
    // 動かした直後のクリックでは切り替えない
    fireEvent.click(pb1)
    expect(onToggleInput).toHaveBeenCalledTimes(1)
    act(() => { vi.runAllTimers() })
    fireEvent.click(pb1)
    expect(onToggleInput).toHaveBeenCalledTimes(2)

    // 離す前に次のドラッグが始まっていたら、遅れて消す処理はそれを消さない
    delete (stage as Partial<HTMLDivElement>).setPointerCapture
    fireEvent.pointerDown(stage, { button: 0, pointerId: 3, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(stage, { pointerId: 3, clientX: 30, clientY: 0 })
    fireEvent.pointerCancel(stage, { pointerId: 3 })
    fireEvent.pointerDown(stage, { button: 0, pointerId: 4, clientX: 0, clientY: 0 })
    act(() => { vi.runAllTimers() })
    fireEvent.pointerMove(stage, { pointerId: 4, clientX: 30, clientY: 0 })
    expect(inner.style.transform).toContain('translate(')
  })

  it('操作中（disabled）は接点を切り替えず、入力でない接点は押せない', () => {
    const { getByRole, queryByRole, onToggleInput, container } = renderViewer({ disabled: true })
    fireEvent.click(getByRole('button', { name: 'PB1 を ON にする' }))
    expect(onToggleInput).not.toHaveBeenCalled()
    expect(queryByRole('button', { name: /X1/ })).toBeNull()
    expect(container.querySelector('title')?.textContent).toBe('PB1（a 接点）: 開　クリックで ON')
    expect([...container.querySelectorAll('title')].map((t) => t.textContent)).toContain('X1（b 接点）: 閉')
  })

  it('選んだ記号へ寄せて枠を出し、画面の大きさが変わったら全体表示のときだけ合わせ直す', () => {
    const view = renderViewer()
    expect(view.zoom()).toBe('10%')
    act(() => resizeCallback?.())
    expect(view.zoom()).toBe('10%')

    view.rerender(
      <DrawingViewer {...view.props} focus={{ cx: 0.5, cy: 0.5, w: 0.01, h: 0.01, key: 1, label: 'PB1 の a 接点' }} />,
    )
    expect(view.getByTestId('viewer-focus').querySelector('title')?.textContent).toBe('PB1 の a 接点')
    const zoomed = view.zoom()
    act(() => resizeCallback?.())
    expect(view.zoom()).toBe(zoomed)

    view.rerender(<DrawingViewer {...view.props} focus={{ cx: 0.5, cy: 0.5, w: 0.5, h: 0.5, key: 2 }} />)
    expect(view.getByTestId('viewer-focus').querySelector('title')).toBeNull()
    view.unmount()
    expect(disconnect).toHaveBeenCalled()
  })

  it('大きさの分からない図面・重ねる結果が無い・ResizeObserver が無い環境でも描ける', () => {
    vi.unstubAllGlobals()
    const view = renderViewer({ width: 0, height: 0, overlay: undefined, focus: { cx: 0.5, cy: 0.5, w: 0.1, h: 0.1, key: 1 } })
    expect(view.zoom()).toBe('100%')
    expect(view.queryByLabelText('シミュレーション結果')).toBeNull()
    fireEvent.click(view.getByText('全体表示'))
    expect(view.zoom()).toBe('100%')
  })
})
