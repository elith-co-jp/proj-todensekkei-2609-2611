// @vitest-environment jsdom

import { fireEvent, render } from '@testing-library/react'
import { useRef } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { useModalFocus } from './useModalFocus'

function DialogHarness({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement | null>(null)
  useModalFocus({ open: true, containerRef: dialogRef, onClose })

  return (
    <div ref={dialogRef} role="dialog" tabIndex={-1}>
      <button type="button" data-autofocus>キャンセル</button>
      <button type="button">実行</button>
    </div>
  )
}

describe('useModalFocus', () => {
  it('フォーカスをダイアログへ移し、Tabを閉じ込め、Escapeで閉じる', () => {
    const onClose = vi.fn()
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(0)
      return 1
    })

    const { getByRole } = render(<DialogHarness onClose={onClose} />)
    const cancelButton = getByRole('button', { name: 'キャンセル' })
    const executeButton = getByRole('button', { name: '実行' })

    expect(document.activeElement).toBe(cancelButton)
    expect(document.body.style.overflow).toBe('hidden')

    executeButton.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(cancelButton)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })
})
