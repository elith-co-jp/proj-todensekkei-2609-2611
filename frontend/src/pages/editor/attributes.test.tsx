// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Connection, SymbolBox } from '../../types'
import { AttributeEditor } from './AttributeEditor'
import { buildAnnotationPayload, isAnnotationPayload, withWireRefs } from './model'

afterEach(() => cleanup())

const wire = (ref?: string): Connection => ({
  ref, from_symbol_ref: 'SYM-0001', from_terminal_ref: null, to_symbol_ref: 'SYM-0002', to_terminal_ref: null,
  wire_no: null, net_id: null, kind: 'wire', external_ref: null, note: null,
})

describe('属性と配線の参照名', () => {
  it('参照名の無い配線に空き番号を振り、あるものは変えない', () => {
    expect(withWireRefs([wire('W-0002'), wire(), wire(), wire('W-0001')]).map((c) => c.ref))
      .toEqual(['W-0002', 'W-0003', 'W-0004', 'W-0001'])
  })

  it('保存のときに属性と配線の参照名を送り返す（送らないと保存のたびに消える）', () => {
    const symbol = { ref: 'SYM-0001', class_key: 'relay_coil', label: null, cx: 0.5, cy: 0.5, w: 0.1, h: 0.1, note: null,
      terminals: [], attributes: { maker: 'オムロン' } } as SymbolBox
    const payload = buildAnnotationPayload([symbol], [{ ...wire('W-0001'), attributes: { length_m: 12 } }])
    expect(payload.symbols[0].attributes).toEqual({ maker: 'オムロン' })
    expect(payload.connections[0]).toMatchObject({ ref: 'W-0001', attributes: { length_m: 12 } })
    expect(isAnnotationPayload(payload)).toBe(true)
    expect(isAnnotationPayload({ ...payload, connections: [{ ...payload.connections[0], attributes: { x: [1] } }] })).toBe(false)
  })

  it('標準の数値項目は数値にし、空にすると項目を消す。自由な項目を足して消せる', () => {
    const onChange = vi.fn()
    const defs = [{ key: 'length_m', label: '長さ', type: 'number' as const, unit: 'm' },
      { key: 'maker', label: 'メーカー', type: 'text' as const, unit: null }]
    const view = render(<AttributeEditor idPrefix="w" defs={defs} value={{ maker: 'A社', 納入日: '2026-11' }} onChange={onChange} />)
    fireEvent.change(view.getByLabelText('長さ（m）'), { target: { value: '1,250.5' } })
    expect(onChange).toHaveBeenLastCalledWith({ maker: 'A社', 納入日: '2026-11', length_m: 1250.5 })
    fireEvent.change(view.getByLabelText('メーカー'), { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith({ 納入日: '2026-11' })
    fireEvent.click(view.getByRole('button', { name: '納入日を削除' }))
    expect(onChange).toHaveBeenLastCalledWith({ maker: 'A社' })
    fireEvent.change(view.getByLabelText('追加する項目名'), { target: { value: '保守担当' } })
    fireEvent.click(view.getByRole('button', { name: /追加/ }))
    expect(onChange).toHaveBeenLastCalledWith({ maker: 'A社', 納入日: '2026-11', 保守担当: '' })
  })
})
