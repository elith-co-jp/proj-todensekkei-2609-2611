// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DeviceIndex } from '../../types'
import DevicePanel, { normalizeQuery } from './DevicePanel'

const api = vi.hoisted(() => ({ deviceIndex: vi.fn(), downloadDeviceIndex: vi.fn() }))
vi.mock('../../api/client', () => ({ api }))

const at = (project_id: number, page: string, symbol_ref: string, label: string, area: string, cx = 0.5, cy = 0.5) => ({
  project_id, page, symbol_ref, label, cx, cy, w: 0.02, h: 0.02, area,
})

const index: DeviceIndex = {
  pages: [{ id: 7, name: 'ER21236' }, { id: 8, name: 'ER21237' }],
  devices: [
    {
      device: '33HB FV2306',
      role: 'relay',
      role_label: 'リレー',
      coils: [at(7, 'ER21236', 'S1', '33HB FV2306', '右上', 0.8, 0.2)],
      contacts: [
        { ...at(7, 'ER21236', 'S2', '33HB FV2306', '左中', 0.1, 0.5), kind: 'a', kind_label: 'a 接点' },
        { ...at(8, 'ER21237', 'S9', '33H8 FV2306 (ER21236)', '中下', 0.5, 0.9), kind: 'b', kind_label: 'b 接点', coil_sheet: 'ER21236' },
      ],
      loads: [],
      a_count: 1,
      b_count: 1,
      pages: ['ER21236', 'ER21237'],
      variants: ['33H8 FV2306'],
      timer: null,
      notes: [{ kind: 'label_variants', message: '表記が違うものを同じ器具にまとめた: 33H8 FV2306' }],
    },
    {
      device: 'PB1',
      role: 'input',
      role_label: '外部入力',
      coils: [],
      contacts: [{ ...at(7, 'ER21236', 'S3', 'PB1', '左上', 0.1, 0.1), kind: 'a', kind_label: 'a 接点' }],
      loads: [],
      a_count: 1,
      b_count: 0,
      pages: ['ER21236'],
      variants: [],
      timer: null,
      notes: [],
    },
  ],
  unlabeled_contacts: [{ ...at(8, 'ER21237', 'S7', '', '右下', 0.9, 0.9), kind: 'a', kind_label: 'a 接点' }],
  wire_numbers: [
    {
      wire_no: '101',
      pages: ['ER21236'],
      connections: 2,
      node_count: 1,
      note: '',
      ends: [
        { ...at(7, 'ER21236', 'S3', 'PB1', '左上'), class_label: 'a 接点' },
        { ...at(7, 'ER21236', 'S1', '33HB FV2306', '右上'), class_label: 'リレーコイル' },
      ],
    },
  ],
  counts: { relays: 1, inputs: 1, loads: 0, contacts: 4, wire_numbers: 1, notes: 2 },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const renderPanel = (onLocate = vi.fn()) =>
  render(
    <MemoryRouter>
      <DevicePanel projectIds={[7, 8]} onLocate={onLocate} />
    </MemoryRouter>,
  )

describe('DevicePanel', () => {
  it('lists coils and contacts by page and jumps to the clicked place', async () => {
    api.deviceIndex.mockResolvedValue(index)
    const onLocate = vi.fn()
    const view = renderPanel(onLocate)
    await waitFor(() => expect(view.getByText('33HB FV2306')).toBeTruthy())
    expect(api.deviceIndex).toHaveBeenCalledWith([7, 8])
    expect(view.getByText('a 1 · b 1')).toBeTruthy()
    fireEvent.click(view.getByTitle('33HB FV2306 の b 接点（S9・33H8 FV2306 (ER21236)）を図面で表示'))
    expect(onLocate).toHaveBeenCalledWith({ project_id: 8, cx: 0.5, cy: 0.9, w: 0.02, h: 0.02, label: '33HB FV2306 の b 接点' })
  })

  it('searches device names, variants and wire numbers in half or full width', async () => {
    api.deviceIndex.mockResolvedValue(index)
    const view = renderPanel()
    await waitFor(() => expect(view.getByText('PB1')).toBeTruthy())
    const box = view.getByPlaceholderText('器具番号・電線番号・ページで探す')
    fireEvent.change(box, { target: { value: '３３ｈ８' } })
    expect(view.queryByText('PB1')).toBeNull()
    expect(view.getByText('33HB FV2306')).toBeTruthy()
    fireEvent.click(view.getByRole('tab', { name: /電線番号/ }))
    fireEvent.change(box, { target: { value: '101' } })
    expect(view.getByText('101')).toBeTruthy()
  })

  it('shows only items to check, including unlabeled contacts, and downloads Excel', async () => {
    api.deviceIndex.mockResolvedValue(index)
    api.downloadDeviceIndex.mockResolvedValue({ name: 'device_index.xlsx', size: 10 })
    const view = renderPanel()
    await waitFor(() => expect(view.getByText('PB1')).toBeTruthy())
    fireEvent.click(view.getByRole('tab', { name: /要確認/ }))
    expect(view.queryByText('PB1')).toBeNull()
    expect(view.getByText('器具番号の無い接点（1）')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /Excel/ }))
    await waitFor(() => expect(api.downloadDeviceIndex).toHaveBeenCalledWith([7, 8]))
  })

  it('normalizes queries', () => {
    expect(normalizeQuery(' ３３ｈｂ fv ')).toBe('33HBFV')
  })

  it('負荷・タイマー・AI の候補・電線の注意を出し、場所を押すとそこへ移る', async () => {
    const onLocate = vi.fn()
    api.deviceIndex.mockResolvedValue({
      ...index,
      devices: [
        {
          device: 'RL1', role: 'load', role_label: '負荷',
          coils: [], contacts: [],
          loads: [{ ...at(7, 'ER21236', 'L1', '', '中央'), origin: 'ai' }],
          a_count: 0, b_count: 0, pages: ['ER21236'], variants: [], timer: null, notes: [],
        },
        { ...index.devices[0], timer: { mode: 'on_delay', mode_label: 'オンディレー', delay_s: 8 }, notes: [] },
      ],
      wire_numbers: [{
        wire_no: '205', pages: ['ER21236', 'ER21237'], connections: 3, node_count: 2, note: '2 つに分かれています',
        ends: [{ ...at(7, 'ER21236', 'T5', '', '右下'), class_label: '端子' }],
      }],
    })
    const view = renderPanel(onLocate)
    expect(await view.findByText('RL1')).toBeTruthy()
    expect(view.getByText('本体')).toBeTruthy()
    expect(view.getByText('AI')).toBeTruthy()
    expect(view.getByText('オンディレー 8 秒')).toBeTruthy()
    fireEvent.click(view.getByTitle('RL1 の本体（L1）を図面で表示'))
    expect(onLocate).toHaveBeenLastCalledWith(expect.objectContaining({ project_id: 7, label: 'RL1 の本体' }))

    fireEvent.click(view.getByRole('tab', { name: /電線番号/ }))
    expect(view.getByText('3 本 · ER21236、ER21237')).toBeTruthy()
    expect(view.getByText('2 つに分かれています')).toBeTruthy()
    fireEvent.click(view.getByTitle('端子 T5 を図面で表示'))
    expect(onLocate).toHaveBeenLastCalledWith(expect.objectContaining({ label: '電線番号 205: T5' }))

    // 要確認には電線の注意と器具番号の無い接点が並び、検索で絞れる
    fireEvent.click(view.getByRole('tab', { name: /要確認/ }))
    expect(view.getByText('205')).toBeTruthy()
    const box = view.getByPlaceholderText('器具番号・電線番号・ページで探す')
    fireEvent.change(box, { target: { value: 'S7' } })
    expect(view.getByText('器具番号の無い接点（1）')).toBeTruthy()
    expect(view.getByRole('link', { name: '編集' }).getAttribute('href')).toBe('/projects/8?symbol=S7')
    fireEvent.change(box, { target: { value: 'どこにもない' } })
    expect(view.getByText('見つかりません')).toBeTruthy()
  })

  it('何も無いときはそう出し、多すぎるときは件数だけ出す', async () => {
    api.deviceIndex.mockResolvedValueOnce({ ...index, devices: [], wire_numbers: [], unlabeled_contacts: [] })
    const view = renderPanel()
    expect(await view.findByText('ありません')).toBeTruthy()
    view.unmount()

    const many = Array.from({ length: 203 }, (_, i) => ({ ...index.devices[1], device: `PB${i}` }))
    const wires = Array.from({ length: 201 }, (_, i) => ({ ...index.wire_numbers[0], wire_no: `W${i}` }))
    api.deviceIndex.mockResolvedValueOnce({ ...index, devices: many, wire_numbers: wires })
    const big = renderPanel()
    expect(await big.findByText('ほか 3 件。検索で絞り込んでください')).toBeTruthy()
    fireEvent.click(big.getByRole('tab', { name: /電線番号/ }))
    expect(big.getByText('ほか 1 件。検索で絞り込んでください')).toBeTruthy()
  })

  it('読み込み・書き出しの失敗を出し、閉じた後に返った結果は捨てる', async () => {
    api.deviceIndex.mockRejectedValueOnce(new Error('回路を組めません'))
    const first = renderPanel()
    expect((await first.findByRole('alert')).textContent).toBe('回路を組めません')
    first.unmount()
    api.deviceIndex.mockRejectedValueOnce('文字列の失敗')
    const second = renderPanel()
    expect((await second.findByRole('alert')).textContent).toBe('文字列の失敗')
    second.unmount()

    let resolve: (value: DeviceIndex) => void = () => undefined
    let reject: (reason: unknown) => void = () => undefined
    api.deviceIndex.mockReturnValueOnce(new Promise((r) => { resolve = r })).mockReturnValueOnce(new Promise((_, r) => { reject = r }))
    renderPanel().unmount()
    renderPanel().unmount()
    resolve(index)
    reject(new Error('遅い失敗'))
    await Promise.resolve()

    api.deviceIndex.mockResolvedValue(index)
    api.downloadDeviceIndex.mockRejectedValueOnce(new Error('書き出せません')).mockRejectedValueOnce('文字列')
    const view = renderPanel()
    await view.findByText('PB1')
    fireEvent.click(view.getByRole('button', { name: /Excel/ }))
    expect((await view.findByRole('alert')).textContent).toBe('書き出せません')
    expect(view.getByText('PB1')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /Excel/ }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toBe('文字列'))
  })
})

