import { useEffect } from 'react'

import type { Connection, SymbolBox, SymbolClass } from '../../types'

import { fitInside, MIN_BOX } from './model'
import type { Mode } from './model'

type PendingNode = { symbolRef: string; terminalRef: string | null }

/**
 * 編集画面のキーボードショートカット一式。
 * - window keydown: Ctrl+S 保存 / Ctrl+Z Undo / Delete・Backspace 削除 / Escape / モード切替(v,b,t,c) / クラス選択(1-9)
 * - canvas keydown: Enter での矩形作成・端子追加・配線確定、矢印キーでの移動・リサイズ
 */
export function useEditorShortcuts(opts: {
  mode: Mode
  classes: SymbolClass[]
  symbols: SymbolBox[]
  selectedRef: string | null
  selectedConn: number | null
  pending: PendingNode | null
  setSymbols: React.Dispatch<React.SetStateAction<SymbolBox[]>>
  setConnections: React.Dispatch<React.SetStateAction<Connection[]>>
  setMode: (mode: Mode) => void
  setClassKey: (key: string) => void
  setSelectedRef: (ref: string | null) => void
  setSelectedConn: (index: number | null) => void
  setPending: (node: PendingNode | null) => void
  saveNow: () => Promise<void>
  pushHistory: () => void
  undo: () => void
  redo: () => void
  deleteSymbol: (ref: string) => void
  addSymbol: (x1: number, y1: number, x2: number, y2: number) => void
  addTerminal: (ref: string, tx: number, ty: number) => void
  addConnection: (from: PendingNode, to: PendingNode) => void
  cancelActivePointer: () => void
}) {
  const {
    mode,
    classes,
    symbols,
    selectedRef,
    selectedConn,
    pending,
    setSymbols,
    setConnections,
    setMode,
    setClassKey,
    setSelectedRef,
    setSelectedConn,
    setPending,
    saveNow,
    pushHistory,
    undo,
    redo,
    deleteSymbol,
    addSymbol,
    addTerminal,
    addConnection,
    cancelActivePointer,
  } = opts
  const symbolByRef = new Map(symbols.map((symbol) => [symbol.ref, symbol]))

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (/input|textarea|select/i.test(el?.tagName ?? '')) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        void saveNow()
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo()
        else undo()
        return
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedRef) {
          e.preventDefault()
          deleteSymbol(selectedRef)
        } else if (selectedConn !== null) {
          e.preventDefault()
          pushHistory()
          setConnections((prev) => prev.filter((_, i) => i !== selectedConn))
          setSelectedConn(null)
        }
        return
      }
      if (e.key === 'Escape') {
        setPending(null)
        setSelectedRef(null)
        setSelectedConn(null)
        cancelActivePointer()
        return
      }
      const k = e.key.toLowerCase()
      if (k === 'v') setMode('select')
      if (k === 'b') setMode('box')
      if (k === 't') setMode('terminal')
      if (k === 'c') setMode('connect')
      if (/^[1-9]$/.test(e.key)) {
        const c = classes[Number(e.key) - 1]
        if (c) setClassKey(c.key)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cancelActivePointer, classes, redo, saveNow, selectedConn, selectedRef, undo])

  const onCanvasKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && mode === 'box') {
      event.preventDefault()
      addSymbol(0.44, 0.46, 0.56, 0.54)
      return
    }
    if (event.key === 'Enter' && mode === 'terminal' && selectedRef) {
      event.preventDefault()
      const selectedSymbol = symbolByRef.get(selectedRef)
      if (selectedSymbol) addTerminal(selectedRef, selectedSymbol.cx, selectedSymbol.cy)
      return
    }
    if (event.key === 'Enter' && mode === 'connect' && selectedRef) {
      event.preventDefault()
      const node = { symbolRef: selectedRef, terminalRef: null }
      if (pending) {
        addConnection(pending, node)
        setPending(null)
      } else {
        setPending(node)
      }
      return
    }
    if (!selectedRef || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return

    event.preventDefault()
    const positionStep = event.altKey ? 0.001 : 0.005
    const sizeStep = event.altKey ? 0.002 : 0.01
    pushHistory()
    setSymbols((previous) =>
      previous.map((symbol) => {
        if (symbol.ref !== selectedRef) return symbol
        if (event.shiftKey) {
          const widthDelta = event.key === 'ArrowRight' ? sizeStep : event.key === 'ArrowLeft' ? -sizeStep : 0
          const heightDelta = event.key === 'ArrowDown' ? sizeStep : event.key === 'ArrowUp' ? -sizeStep : 0
          return {
            ...symbol,
            ...fitInside(
              symbol.cx,
              symbol.cy,
              Math.max(MIN_BOX, symbol.w + widthDelta),
              Math.max(MIN_BOX, symbol.h + heightDelta),
            ),
          }
        }
        const xDelta = event.key === 'ArrowRight' ? positionStep : event.key === 'ArrowLeft' ? -positionStep : 0
        const yDelta = event.key === 'ArrowDown' ? positionStep : event.key === 'ArrowUp' ? -positionStep : 0
        return { ...symbol, ...fitInside(symbol.cx + xDelta, symbol.cy + yDelta, symbol.w, symbol.h) }
      }),
    )
  }

  return { onCanvasKeyDown }
}
