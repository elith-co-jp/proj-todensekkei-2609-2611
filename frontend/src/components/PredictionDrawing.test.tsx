// @vitest-environment jsdom

import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PredictionDrawing from './PredictionDrawing'
import type { StructureResult } from '../types'

let observed: (() => void) | null = null
let rect = { left: 0, top: 0, width: 532, height: 432 }

beforeEach(() => {
  observed = null
  rect = { left: 0, top: 0, width: 532, height: 432 }
  vi.stubGlobal('ResizeObserver', class {
    constructor(cb: () => void) { observed = cb }
    observe() {}
    disconnect() {}
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => rect as DOMRect)
  HTMLElement.prototype.setPointerCapture = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const structure = { image_size: { width: 2000, height: 1600 }, wires: [{ id: 'w', polyline: [[200, 400], [1000, 400]] }] } as unknown as StructureResult

function draw(props: Partial<Parameters<typeof PredictionDrawing>[0]> = {}) {
  return render(
    <PredictionDrawing imageUrl="/img" name="図面" width={1000} height={800}
      detections={[{ class_key: 'relay_coil', class_label: 'コイル', cx: 0.5, cy: 0.5, w: 0.1, h: 0.1 } as never]}
      regions={[{ class_key: 'connector', class_label: '枠', cx: 0.5, cy: 0.5, w: 0.5, h: 0.5 } as never]}
      structure={structure} showSymbols showRegions showWires wireOpacity={0.5} {...props} />,
  )
}

const zoomLabel = (view: ReturnType<typeof draw>) => view.getByText(/%$/).textContent

describe('PredictionDrawing', () => {
  it('fits the drawing, scales wires from the analysed image size, and draws every layer', () => {
    const view = draw()
    expect(zoomLabel(view)).toBe('50%')
    expect(view.getByTestId('wire-layer').querySelector('polyline')?.getAttribute('points')).toBe('100,200 500,200')
    expect(view.getByTestId('region-layer').querySelector('title')?.textContent).toBe('領域枠（シンボル対象外）')
    expect(view.getByTestId('symbol-layer').querySelector('title')?.textContent).toBe('コイル')
  })

  it('zooms with the buttons and the wheel, refits, and follows resizes', () => {
    const view = draw()
    fireEvent.click(view.getByRole('button', { name: '拡大' }))
    expect(zoomLabel(view)).toBe('63%')
    fireEvent.click(view.getByRole('button', { name: '縮小' }))
    expect(zoomLabel(view)).toBe('50%')
    fireEvent.wheel(view.getByRole('region'), { deltaY: -1000, clientX: 10, clientY: 10 })
    expect(zoomLabel(view)).toBe('61%')
    fireEvent.wheel(view.getByRole('region'), { deltaY: 1000, clientX: 10, clientY: 10 })
    expect(zoomLabel(view)).toBe('50%')
    fireEvent.click(view.getByRole('button', { name: '拡大' }))
    fireEvent.click(view.getByRole('button', { name: '全体表示' }))
    expect(zoomLabel(view)).toBe('50%')
    rect = { left: 0, top: 0, width: 1032, height: 832 }
    act(() => observed?.())
    expect(zoomLabel(view)).toBe('100%')
  })

  it('does not refit or zoom while the viewport has no size', () => {
    rect = { left: 0, top: 0, width: 0, height: 0 }
    const view = draw()
    expect(zoomLabel(view)).toBe('100%')
    rect = { left: 0, top: 0, width: 100, height: 0 }
    fireEvent.click(view.getByRole('button', { name: '全体表示' }))
    expect(zoomLabel(view)).toBe('100%')
  })

  it('pans with the primary button only', () => {
    const view = draw()
    const region = view.getByRole('region')
    const layer = region.firstElementChild as HTMLElement
    const before = layer.style.transform
    fireEvent.pointerMove(region, { clientX: 50, clientY: 50 })
    fireEvent.pointerDown(region, { button: 2, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(region, { clientX: 50, clientY: 50 })
    expect(layer.style.transform).toBe(before)
    fireEvent.pointerDown(region, { button: 0, clientX: 0, clientY: 0, pointerId: 1 })
    fireEvent.pointerMove(region, { clientX: 30, clientY: 20 })
    expect(layer.style.transform).toBe('translate(46px, 36px) scale(0.5)')
    fireEvent.pointerUp(region)
    fireEvent.pointerMove(region, { clientX: 90, clientY: 90 })
    expect(layer.style.transform).toBe('translate(46px, 36px) scale(0.5)')
    fireEvent.pointerDown(region, { button: 0, clientX: 0, clientY: 0 })
    fireEvent.pointerCancel(region)
    fireEvent.pointerDown(region, { button: 0, clientX: 0, clientY: 0 })
    fireEvent(region, new Event('lostpointercapture', { bubbles: true }))
    fireEvent.pointerMove(region, { clientX: 90, clientY: 90 })
    expect(layer.style.transform).toBe('translate(46px, 36px) scale(0.5)')
  })

  it('hides layers that are switched off and works without an analysis result', () => {
    const view = draw({ showSymbols: false, showRegions: false, structure: null })
    expect(view.queryByTestId('wire-layer')).toBeNull()
    expect(view.queryByTestId('symbol-layer')).toBeNull()
    expect(view.queryByTestId('region-layer')).toBeNull()
    cleanup()
    const zeroSize = draw({ structure: { ...structure, image_size: { width: 0, height: 0 } } as StructureResult })
    expect(zeroSize.getByTestId('wire-layer').querySelector('polyline')?.getAttribute('points')).toBe('200,400 1000,400')
  })
})
