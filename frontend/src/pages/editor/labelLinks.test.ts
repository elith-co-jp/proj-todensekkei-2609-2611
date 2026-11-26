import { describe, expect, it } from 'vitest'

import type { LabelLink, SymbolBox } from '../../types'
import { applyLabelLinks, buildAnnotationPayload, isAnnotationPayload, labelLinkNotice } from './model'

const box = { cx: 0.5, cy: 0.5, w: 0.04, h: 0.02 }

function symbol(ref: string, label: string | null, extra: Partial<SymbolBox> = {}): SymbolBox {
  return { ref, class_key: 'contact_a', label, cx: 0.4, cy: 0.5, w: 0.01, h: 0.03, note: null, terminals: [], ...extra }
}

function link(ref: string, label: string, status: LabelLink['status'] = 'linked'): LabelLink {
  return { ref, label, status, direction: 'right', label_box: status === '未検出' ? null : box, candidates: [] }
}

describe('近くの文字からのラベル（DD-0049）', () => {
  it('空のラベルだけ埋め、入力済みのラベルは変えずに位置だけ付ける', () => {
    const result = applyLabelLinks(
      [symbol('A', null), symbol('B', '83DX-1'), symbol('C', '6X'), symbol('D', null)],
      [link('A', '43LK P33P22'), link('B', '83DX-1 P33P22'), link('C', '5X P33P22'), link('D', '', '未検出')],
    )
    expect(result.symbols.map((s) => [s.label, s.label_box ?? null])).toEqual([
      ['43LK P33P22', box],
      ['83DX-1', box],
      ['6X', null],
      [null, null],
    ])
    expect(result).toMatchObject({ filled: 1, located: 1, review: 0 })
    expect(labelLinkNotice(result, 4)).toBe(
      '近くの文字から 1 個の記号にラベルを付けました。1 個は入力済みのラベルの文字の位置を付けました。',
    )
  })

  it('候補が複数のものは空のメモに候補を書き、人が書いたメモは残す', () => {
    const review = { ...link('A', 'TB3', '要確認'), candidates: [{ text: 'TB3', box }, { text: 'TB4', box }] }
    const kept = { ...link('B', 'TB5', '要確認'), candidates: [{ text: 'TB5', box }] }
    const result = applyLabelLinks([symbol('A', null), symbol('B', null, { note: '手書きのメモ' })], [review, kept])
    expect(result.symbols.map((s) => s.note)).toEqual(['ラベル要確認（候補: TB3 / TB4）', '手書きのメモ'])
    expect(result.review).toBe(2)
  })

  it('見つからなかったときはそう伝える', () => {
    expect(labelLinkNotice({ filled: 0, review: 0, located: 0 }, 3)).toBe(
      '近くに器具番号らしい文字が見つかりませんでした（記号 3 個）。ラベルは手で入力してください。',
    )
  })

  it('保存する内容にラベルの文字の位置が入り、壊れた位置は保存しない', () => {
    const payload = buildAnnotationPayload([symbol('A', '6X', { label_box: box }), symbol('B', null)], [])
    expect(payload.symbols.map((s) => s.label_box)).toEqual([box, null])
    expect(isAnnotationPayload(payload)).toBe(true)
    const broken = { ...payload, symbols: [{ ...payload.symbols[0], label_box: { cx: 'x' } }] }
    expect(isAnnotationPayload(broken)).toBe(false)
  })
})
