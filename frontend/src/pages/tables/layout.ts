import type { TableCellValue, TableGrid } from '../../types'

// 表の欄を、図面の上と同じ位置に並べるための計算（DD-0050）。座標は図面のピクセル
export type CellRect = { row: number; col: number; colspan: number; x: number; y: number; w: number; h: number }

export const CELL_PAD = 3
export const LOW_SCORE = 0.7

export const cellKey = (row: number, col: number) => `${row}:${col}`

// 隣の行・列との境目は、文字の枠の間の真ん中にする。端の欄は文字の枠から少し広げる
function bounds(spans: { a: number; b: number }[]): { start: number; end: number }[] {
  return spans.map((span, i) => ({
    start: i === 0 ? span.a - CELL_PAD : (spans[i - 1].b + span.a) / 2,
    end: i === spans.length - 1 ? span.b + CELL_PAD : (span.b + spans[i + 1].a) / 2,
  }))
}

export function tableLayout(grid: TableGrid) {
  const rows = bounds(grid.rows.map((r) => ({ a: r.y0, b: r.y1 })))
  const cols = bounds(grid.cols.map((c) => ({ a: c.x0, b: c.x1 })))
  const [bx0, by0, bx1, by1] = grid.bbox
  const view = {
    x: Math.min(bx0, cols[0]?.start ?? bx0),
    y: Math.min(by0, rows[0]?.start ?? by0),
    x1: Math.max(bx1, cols.at(-1)?.end ?? bx1),
    y1: Math.max(by1, rows.at(-1)?.end ?? by1),
  }
  const spans = new Map(grid.cells.map((c) => [cellKey(c.row, c.col), Math.max(1, c.colspan)]))
  const rects: CellRect[] = []
  rows.forEach((r, row) => {
    let col = 0
    while (col < cols.length) {
      const colspan = Math.min(spans.get(cellKey(row, col)) ?? 1, cols.length - col)
      rects.push({ row, col, colspan, x: cols[col].start, y: r.start, w: cols[col + colspan - 1].end - cols[col].start, h: r.end - r.start })
      col += colspan
    }
  })
  return { view: { x: view.x, y: view.y, w: view.x1 - view.x, h: view.y1 - view.y }, rects }
}

// 文字の高さ（行の高さの中央値、図面のピクセル）
export function rowHeight(grid: TableGrid): number {
  const heights = grid.rows.map((r) => r.y1 - r.y0).sort((a, b) => a - b)
  return heights[Math.floor(heights.length / 2)] || 20
}

// 行の高さが画面で 26 px ほどになる倍率（読みやすい大きさ）
export function defaultScale(grid: TableGrid): number {
  const median = rowHeight(grid)
  return Math.min(4, Math.max(0.5, Math.round((26 / median) * 4) / 4))
}

export function valueMap(values: TableCellValue[]): Map<string, string> {
  return new Map(values.filter((v) => v.text !== '').map((v) => [cellKey(v.row, v.col), v.text]))
}

export function toValues(map: Map<string, string>): TableCellValue[] {
  return [...map.entries()]
    .filter(([, text]) => text.trim() !== '')
    .map(([key, text]) => {
      const [row, col] = key.split(':').map(Number)
      return { row, col, text: text.trim() }
    })
    .sort((a, b) => a.row - b.row || a.col - b.col)
}

export function sameValues(a: Map<string, string>, b: Map<string, string>): boolean {
  const keys = new Set([...a.keys(), ...b.keys()])
  return [...keys].every((k) => (a.get(k) ?? '').trim() === (b.get(k) ?? '').trim())
}
