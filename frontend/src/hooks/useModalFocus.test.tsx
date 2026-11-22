// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useModalFocus } from './useModalFocus'

function DialogHarness({ onClose, open = true, buttons = true, autofocus = true }: {
  onClose: () => void; open?: boolean; buttons?: boolean; autofocus?: boolean
}) {
  const dialogRef = useRef<HTMLDivElement | null>(null)
  useModalFocus({ open, containerRef: dialogRef, onClose })

  return (
    <div ref={dialogRef} role="dialog" tabIndex={-1}>
      {buttons && <button type="button" data-autofocus={autofocus || undefined}>キャンセル</button>}
      {buttons && <button type="button">実行</button>}
    </div>
  )
}

beforeEach(() => {
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    callback(0)
    return 1
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  document.body.style.overflow = ''
})

describe('useModalFocus', () => {
  it('フォーカスをダイアログへ移し、Tabを閉じ込め、Escapeで閉じる', () => {
    const onClose = vi.fn()
    const { getByRole } = render(<DialogHarness onClose={onClose} />)
    const cancelButton = getByRole('button', { name: 'キャンセル' })
    const executeButton = getByRole('button', { name: '実行' })

    expect(document.activeElement).toBe(cancelButton)
    expect(document.body.style.overflow).toBe('hidden')

    executeButton.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(cancelButton)

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(executeButton)

    // 途中の要素では Tab をブラウザに任せる
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(executeButton)
    fireEvent.keyDown(document, { key: 'a' })

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('data-autofocus が無ければ最初のボタン、ボタンも無ければダイアログ自身にフォーカスする', () => {
    const first = render(<DialogHarness onClose={vi.fn()} autofocus={false} />)
    expect(document.activeElement).toBe(first.getByRole('button', { name: 'キャンセル' }))
    first.unmount()

    const empty = render(<DialogHarness onClose={vi.fn()} buttons={false} />)
    const dialog = empty.getByRole('dialog')
    expect(document.activeElement).toBe(dialog)
    ;(document.activeElement as HTMLElement).blur()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(dialog)
  })

  it('閉じたら背面のスクロールとフォーカスを元に戻し、閉じている間は何もしない', () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    document.body.style.overflow = 'auto'
    const onClose = vi.fn()
    const view = render(<DialogHarness onClose={onClose} />)
    expect(document.body.style.overflow).toBe('hidden')
    view.rerender(<DialogHarness onClose={onClose} open={false} />)
    expect(document.body.style.overflow).toBe('auto')
    expect(document.activeElement).toBe(outside)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    outside.remove()
  })

  it('ダイアログがまだ描かれていなくても落ちない', () => {
    function Detached() {
      const ref = useRef<HTMLDivElement | null>(null)
      useModalFocus({ open: true, containerRef: ref, onClose: vi.fn() })
      return null
    }
    const view = render(<Detached />)
    fireEvent.keyDown(document, { key: 'Tab' })
    view.unmount()
  })

  it('フォーカスが無いところから開いても閉じるときに落ちない', () => {
    ;(document.activeElement as HTMLElement | null)?.blur()
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    document.body.appendChild(svg)
    Object.defineProperty(document, 'activeElement', { configurable: true, get: () => svg })
    const view = render(<DialogHarness onClose={vi.fn()} />)
    delete (document as unknown as Record<string, unknown>).activeElement
    view.unmount()
    svg.remove()
  })
})
