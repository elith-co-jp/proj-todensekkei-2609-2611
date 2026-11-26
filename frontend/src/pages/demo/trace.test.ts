import { describe, expect, it } from 'vitest'

import type { DemoLocation } from '../../types'
import { tourSteps, traceFrom } from './trace'

// 紙芝居の形: 6X ← A・C（反転）、A ← D009、C ← A（S）
const deps = {
  '6X': [{ signal: 'A', negated: false, port: '' }, { signal: 'C', negated: true, port: '' }],
  'C': [{ signal: 'A', negated: false, port: 'S' }, { signal: 'R1', negated: false, port: 'R' }],
  'A': [{ signal: 'D009', negated: false, port: '' }],
  'D009': [], 'R1': [], '5X': [{ signal: 'C', negated: false, port: '' }],
}

const loc = (device: string, role: 'coil' | 'contact', project_id: number): DemoLocation => ({
  project_id, page: `P${project_id}`, symbol_ref: `${device}-${role}-${project_id}`, device, role, contact: role === 'contact' ? 'a' : null,
  negated: false, label: device, cx: 0.5, cy: 0.5, w: 0.01, h: 0.01,
})

describe('traceFrom', () => {
  it('上流へ段数の浅い順に辿り、同じ信号は 1 回だけ', () => {
    const up = traceFrom(deps, '6X', 'up', 99)
    expect(up.map((n) => [n.signal, n.depth])).toEqual([['6X', 0], ['A', 1], ['C', 1], ['D009', 2], ['R1', 2]])
    expect(up.find((n) => n.signal === 'C')?.negated).toBe(true)
    expect(traceFrom(deps, '6X', 'up', 1).map((n) => n.signal)).toEqual(['6X', 'A', 'C'])
  })

  it('下流へは、その信号を使う側へ辿る', () => {
    const down = traceFrom(deps, 'A', 'down', 99)
    expect(down.map((n) => n.signal).sort()).toEqual(['5X', '6X', 'A', 'C'])
    expect(traceFrom(deps, 'A', 'both', 1).map((n) => n.side)).toEqual(['self', 'up', 'down', 'down'])
  })
})

describe('tourSteps', () => {
  it('上流の信号は親のコイルと同じ図面の接点を先に、その信号のコイルをあとに見る', () => {
    const nodes = traceFrom(deps, '6X', 'up', 1)
    const locations = {
      '6X': [loc('6X', 'coil', 6)],
      'A': [loc('83DX', 'contact', 2), loc('83DX', 'contact', 6), loc('83DX', 'coil', 2)],
    }
    const steps = tourSteps(nodes, locations, (s) => s)
    expect(steps.map((s) => [s.node.signal, s.location.role, s.location.project_id])).toEqual([
      ['6X', 'coil', 6], ['A', 'contact', 6], ['A', 'contact', 2], ['A', 'coil', 2],
    ])
    expect(steps[1].why).toContain('6X の条件に入る')
  })
})
