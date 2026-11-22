// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { OnboardingTour } from './OnboardingTour'

afterEach(cleanup)

describe('OnboardingTour', () => {
  it('walks through every step, goes back, and finishes', () => {
    const onClose = vi.fn()
    const view = render(<OnboardingTour open onClose={onClose} />)
    expect(view.getByText('STEP 01')).toBeTruthy()
    expect(view.queryByRole('button', { name: /戻る/ })).toBeNull()
    fireEvent.click(view.getByRole('button', { name: /次へ/ }))
    expect(view.getByText('AI 解析で下書きを作る')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /戻る/ }))
    expect(view.getByText('STEP 01')).toBeTruthy()
    for (let i = 0; i < 5; i += 1) fireEvent.click(view.getByRole('button', { name: /次へ/ }))
    expect(view.getByText('回路検証で動きを確かめる')).toBeTruthy()
    expect(view.getByText('6 / 6')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'ツアーを完了' }))
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('starts from the first step again when reopened, and closes with Escape', () => {
    const onClose = vi.fn()
    const view = render(<OnboardingTour open onClose={onClose} />)
    fireEvent.click(view.getByRole('button', { name: /次へ/ }))
    view.rerender(<OnboardingTour open={false} onClose={onClose} />)
    expect(view.queryByRole('dialog')).toBeNull()
    view.rerender(<OnboardingTour open onClose={onClose} />)
    expect(view.getByText('STEP 01')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })
})
