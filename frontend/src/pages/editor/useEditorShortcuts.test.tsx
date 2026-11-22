// @vitest-environment jsdom

import { cleanup, fireEvent, renderHook } from '@testing-library/react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SymbolBox, SymbolClass } from '../../types'
import { useEditorShortcuts } from './useEditorShortcuts'

afterEach(cleanup)

const box = (ref: string, patch: Partial<SymbolBox> = {}) =>
  ({ ref, class_key: 'relay', label: null, cx: 0.5, cy: 0.5, w: 0.1, h: 0.1, note: null, terminals: [], ...patch }) as SymbolBox

function setup(patch: Partial<Parameters<typeof useEditorShortcuts>[0]> = {}) {
  let symbols = [box('S1'), box('S2')]
  let connections = [{ from_symbol_ref: 'S1' }, { from_symbol_ref: 'S2' }] as never[]
  const fns = {
    setSymbols: vi.fn((next) => { symbols = typeof next === 'function' ? next(symbols) : next }),
    setConnections: vi.fn((next) => { connections = typeof next === 'function' ? next(connections) : next }),
    setMode: vi.fn(), setClassKey: vi.fn(), setSelectedRef: vi.fn(), setSelectedConn: vi.fn(), setPending: vi.fn(),
    saveNow: vi.fn(async () => undefined), pushHistory: vi.fn(), undo: vi.fn(), redo: vi.fn(), deleteSymbol: vi.fn(),
    addSymbol: vi.fn(), addTerminal: vi.fn(), addConnection: vi.fn(), cancelActivePointer: vi.fn(),
  }
  const props = {
    mode: 'select' as const, classes: [{ key: 'a' }, { key: 'b' }] as SymbolClass[], symbols, selectedRef: null, selectedConn: null,
    pending: null, ...fns, ...patch,
  }
  const hook = renderHook((p) => useEditorShortcuts(p), { initialProps: props })
  const canvas = (key: string, extra: Partial<ReactKeyboardEvent<HTMLDivElement>> = {}) => {
    const preventDefault = vi.fn()
    hook.result.current.onCanvasKeyDown({ key, altKey: false, shiftKey: false, preventDefault, ...extra } as ReactKeyboardEvent<HTMLDivElement>)
    return preventDefault
  }
  return { ...fns, hook, props, canvas, get symbols() { return symbols }, get connections() { return connections } }
}

describe('useEditorShortcuts（画面全体のキー）', () => {
  it('保存・取り消し・やり直し・モード・種別を切り替え、入力欄の中では動かない', () => {
    const s = setup()
    fireEvent.keyDown(document.body, { key: 's', ctrlKey: true })
    fireEvent.keyDown(document.body, { key: 'S', metaKey: true })
    expect(s.saveNow).toHaveBeenCalledTimes(2)
    fireEvent.keyDown(document.body, { key: 'z', ctrlKey: true })
    fireEvent.keyDown(document.body, { key: 'Z', metaKey: true, shiftKey: true })
    expect(s.undo).toHaveBeenCalledOnce()
    expect(s.redo).toHaveBeenCalledOnce()
    for (const key of ['v', 'B', 't', 'c']) fireEvent.keyDown(document.body, { key })
    expect(s.setMode.mock.calls.map(([m]) => m)).toEqual(['select', 'box', 'terminal', 'connect'])
    fireEvent.keyDown(document.body, { key: '2' })
    fireEvent.keyDown(document.body, { key: '9' })
    expect(s.setClassKey.mock.calls).toEqual([['b']])
    fireEvent.keyDown(window, { key: 'x' })

    const input = document.createElement('input')
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: 's', ctrlKey: true })
    input.remove()
    expect(s.saveNow).toHaveBeenCalledTimes(2)
  })

  it('Delete は選んだ記号か配線を消し、何も選んでいなければ何もしない。Escape で選択を外す', () => {
    const s = setup()
    fireEvent.keyDown(document.body, { key: 'Delete' })
    expect(s.deleteSymbol).not.toHaveBeenCalled()
    expect(s.pushHistory).not.toHaveBeenCalled()
    s.hook.rerender({ ...s.props, selectedRef: 'S1' } as never)
    fireEvent.keyDown(document.body, { key: 'Backspace' })
    expect(s.deleteSymbol).toHaveBeenCalledWith('S1')
    s.hook.rerender({ ...s.props, selectedConn: 0 } as never)
    fireEvent.keyDown(document.body, { key: 'Delete' })
    expect(s.pushHistory).toHaveBeenCalled()
    expect(s.connections).toEqual([{ from_symbol_ref: 'S2' }])
    expect(s.setSelectedConn).toHaveBeenCalledWith(null)
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(s.setPending).toHaveBeenCalledWith(null)
    expect(s.setSelectedRef).toHaveBeenCalledWith(null)
    expect(s.cancelActivePointer).toHaveBeenCalled()
  })
})

describe('useEditorShortcuts（図面の上のキー）', () => {
  it('Enter で枠を作り、端子を足し、配線の始点と終点を決める', () => {
    const box1 = setup({ mode: 'box' })
    expect(box1.canvas('Enter')).toHaveBeenCalled()
    expect(box1.addSymbol).toHaveBeenCalledWith(0.44, 0.46, 0.56, 0.54)
    cleanup()

    const term = setup({ mode: 'terminal', selectedRef: 'S1' })
    term.canvas('Enter')
    expect(term.addTerminal).toHaveBeenCalledWith('S1', 0.5, 0.5)
    term.hook.rerender({ ...term.props, selectedRef: 'gone' } as never)
    term.canvas('Enter')
    expect(term.addTerminal).toHaveBeenCalledOnce()
    cleanup()

    const conn = setup({ mode: 'connect', selectedRef: 'S1' })
    conn.canvas('Enter')
    expect(conn.setPending).toHaveBeenCalledWith({ symbolRef: 'S1', terminalRef: null })
    conn.hook.rerender({ ...conn.props, selectedRef: 'S2', pending: { symbolRef: 'S1', terminalRef: null } } as never)
    conn.canvas('Enter')
    expect(conn.addConnection).toHaveBeenCalledWith({ symbolRef: 'S1', terminalRef: null }, { symbolRef: 'S2', terminalRef: null })
    expect(conn.setPending).toHaveBeenLastCalledWith(null)
  })

  it('矢印で選んだ枠を動かし、Shift で大きさを変え、Alt で細かく動かす', () => {
    const s = setup({ mode: 'connect' })
    // 選んでいないとき・矢印以外は何もしない
    expect(s.canvas('ArrowRight')).not.toHaveBeenCalled()
    s.hook.rerender({ ...s.props, selectedRef: 'S1' } as never)
    expect(s.canvas('a')).not.toHaveBeenCalled()

    const at = () => s.symbols[0]
    s.canvas('ArrowRight')
    expect(at().cx).toBeCloseTo(0.505)
    s.canvas('ArrowLeft', { altKey: true })
    expect(at().cx).toBeCloseTo(0.504)
    s.canvas('ArrowDown')
    s.canvas('ArrowUp', { altKey: true })
    expect(at().cy).toBeCloseTo(0.504)
    s.canvas('ArrowRight', { shiftKey: true })
    expect(at().w).toBeCloseTo(0.11)
    s.canvas('ArrowLeft', { shiftKey: true, altKey: true })
    expect(at().w).toBeCloseTo(0.108)
    s.canvas('ArrowDown', { shiftKey: true })
    s.canvas('ArrowUp', { shiftKey: true, altKey: true })
    expect(at().h).toBeCloseTo(0.108)
    expect(s.symbols[1]).toEqual(box('S2'))
    expect(s.pushHistory).toHaveBeenCalledTimes(8)
  })
})
