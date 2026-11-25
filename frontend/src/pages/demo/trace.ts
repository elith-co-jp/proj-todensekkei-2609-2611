import type { DemoLocation } from '../../types'

/** 信号 → その信号を決める信号（上流）。port はキープリレーの S / R */
export type LogicDeps = Record<string, { signal: string; negated: boolean; port: string }[]>

export type TraceDirection = 'up' | 'down' | 'both'

export type TraceNode = {
  signal: string
  /** 注目点からの段数（注目点は 0） */
  depth: number
  /** up=注目点を決める側（原因）、down=注目点で決まる側（影響）、self=注目点 */
  side: 'up' | 'down' | 'self'
  /** 辿ってきた元の信号（親） */
  parent: string | null
  /** 親への効き方が反転（b 接点・NOT）か */
  negated: boolean
  port: string
}

/** 注目点から上流・下流へ、段数の浅い順に辿る（同じ信号は最初に着いたときだけ） */
export function traceFrom(deps: LogicDeps, focus: string, direction: TraceDirection, maxDepth: number): TraceNode[] {
  const users: LogicDeps = {}
  for (const [sig, refs] of Object.entries(deps)) {
    for (const r of refs) (users[r.signal] ??= []).push({ signal: sig, negated: r.negated, port: r.port })
  }
  const out: TraceNode[] = [{ signal: focus, depth: 0, side: 'self', parent: null, negated: false, port: '' }]
  const walk = (side: 'up' | 'down', graph: LogicDeps) => {
    const seen = new Set([focus])
    let frontier = [focus]
    for (let depth = 1; depth <= maxDepth && frontier.length; depth++) {
      const next: string[] = []
      for (const sig of frontier) {
        for (const r of graph[sig] ?? []) {
          if (seen.has(r.signal)) continue
          seen.add(r.signal)
          next.push(r.signal)
          out.push({ signal: r.signal, depth, side, parent: sig, negated: r.negated, port: r.port })
        }
      }
      frontier = next
    }
  }
  if (direction !== 'down') walk('up', deps)
  if (direction !== 'up') walk('down', users)
  return out
}

export type TourStep = { node: TraceNode; location: DemoLocation; why: string }

/**
 * 図面を順に見ていく手順。各信号でコイル（条件の回路）と接点を示す。
 * 上流へ辿るときは、親のコイルと同じ図面にある接点（親の条件に入っている接点）を先に見る。
 */
export function tourSteps(
  nodes: TraceNode[],
  locations: Record<string, DemoLocation[]>,
  label: (sig: string) => string,
  direction: TraceDirection = 'both',
): TourStep[] {
  const steps: TourStep[] = []
  const coilPages = (sig: string) => new Set((locations[sig] ?? []).filter((l) => l.role === 'coil').map((l) => l.project_id))
  for (const node of nodes) {
    const locs = locations[node.signal] ?? []
    const name = label(node.signal)
    const parent = node.parent ? label(node.parent) : ''
    const near = node.parent ? coilPages(node.side === 'up' ? node.parent : node.signal) : new Set<number>()
    // 原因だけを辿るときは、注目点の接点（注目点が効く先）は見ない
    const contacts = (node.side === 'self' && direction === 'up' ? [] : locs.filter((l) => l.role === 'contact'))
      .sort((a, b) => Number(!near.has(a.project_id)) - Number(!near.has(b.project_id)))
    const coils = locs.filter((l) => l.role === 'coil')
    const contactWhy = (l: DemoLocation) =>
      node.side === 'up'
        ? `${name} の${l.contact ?? ''}接点。${parent} の条件に${node.negated ? '反転して（NOT）' : ''}入る`
        : node.side === 'down'
          ? `${name} の${l.contact ?? ''}接点`
          : `${name} の${l.contact ?? ''}接点（ほかの回路へ効く）`
    const coilWhy = node.side === 'self'
      ? `${name} のコイル。ここに至る回路が ${name} の条件`
      : node.side === 'up'
        ? `${name} のコイル。${name} を決める回路`
        : `${name} のコイル。${parent} の接点が条件に入る`
    const ordered = node.side === 'up' ? [...contacts.map((l) => ({ l, why: contactWhy(l) })), ...coils.map((l) => ({ l, why: coilWhy }))]
      : [...coils.map((l) => ({ l, why: coilWhy })), ...contacts.map((l) => ({ l, why: contactWhy(l) }))]
    for (const { l, why } of ordered) steps.push({ node, location: l, why })
  }
  return steps
}
