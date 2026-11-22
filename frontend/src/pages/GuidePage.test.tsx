// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import GuidePage from './GuidePage'

const api = vi.hoisted(() => ({ listClasses: vi.fn() }))
vi.mock('../api/client', () => ({ api }))

beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

describe('GuidePage', () => {
  it('lists the workflow, the circuit check guide, shortcuts and the symbol types in order', async () => {
    api.listClasses.mockResolvedValue([
      { key: 'lamp', label: '表示灯', color: '#f00', yolo_index: 9 },
      { key: 'relay_coil', label: 'リレーコイル', color: '#0f0', yolo_index: 0 },
    ])
    const view = render(<GuidePage />)
    expect(view.getByText('シンボル種別を読み込んでいます。')).toBeTruthy()
    expect(await view.findByText('リレーコイル')).toBeTruthy()
    const numbers = [...view.container.querySelectorAll('.font-mono.text-\\[10px\\]')].map((el) => el.textContent)
    expect(numbers).toEqual(['1', '10'])
    expect(view.getByRole('region', { name: '回路検証の使い方' }).textContent).toContain('タイマー')
    expect(view.getByText('Ctrl+Shift+Z')).toBeTruthy()
  })

  it('says when the symbol types cannot be read, and ignores answers after leaving', async () => {
    api.listClasses.mockRejectedValueOnce(new Error('down'))
    const view = render(<GuidePage />)
    expect(await view.findByText('シンボル種別を読み込めませんでした。')).toBeTruthy()
    cleanup()
    let fail: (e: Error) => void = () => undefined
    let ok: (v: unknown[]) => void = () => undefined
    api.listClasses.mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }))
    render(<GuidePage />).unmount()
    fail(new Error('late'))
    api.listClasses.mockImplementationOnce(() => new Promise((resolve) => { ok = resolve }))
    render(<GuidePage />).unmount()
    ok([])
    await Promise.resolve()
  })
})
