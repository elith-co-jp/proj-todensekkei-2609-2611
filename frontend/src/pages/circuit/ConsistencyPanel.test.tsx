// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { ConsistencyResult } from '../../types'
import ConsistencyPanel from './ConsistencyPanel'

const api = vi.hoisted(() => ({ checkConsistency: vi.fn(), applyConsistencyFixes: vi.fn() }))
vi.mock('../../api/client', () => ({ api }))

const issue = {
  id: 'abc', check: 'relay_table', severity: 'error' as const, title: '接点の種類（a/b）が表と違う',
  message: 'ER21216 の 10D の X R100 の接点は、図面では b 接点、表では a 接点',
  locations: [{ project_id: 7, symbol_ref: 'XA', page: 'ER21216' }],
  review: ['窓の中で器具番号が一致する接点はこれ 1 つ → 表の種類が正しい可能性が高い'],
  proposal: '接点 XA を a 接点にする',
  fix: { type: 'set_class' as const, project_id: 7, symbol_ref: 'XA', value: 'contact_a' },
}

const found: ConsistencyResult = {
  checks: [
    { check: 'relay_table', method_a: '図面の記号', method_b: 'リレー表', compared: 3, issues: 1, status: 'issues', skipped_reason: null },
    { check: 'solvers', method_a: '内蔵の回路計算', method_b: 'ngspice・Lcapy', compared: 0, issues: 0, status: 'skipped', skipped_reason: 'ngspice も Lcapy も入っていません' },
  ],
  issues: [issue],
  summary: { errors: 1, warnings: 0, fixable: 1 },
}

const fixed: ConsistencyResult = {
  checks: [{ ...found.checks[0], issues: 0, status: 'ok' }],
  issues: [],
  summary: { errors: 0, warnings: 0, fixable: 0 },
  applied: [{ type: 'set_class', project_id: 7, symbol_ref: 'XA', value: 'contact_a', before: 'contact_b' }],
  not_applied: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.checkConsistency.mockResolvedValue(found)
  api.applyConsistencyFixes.mockResolvedValue(fixed)
})

afterEach(() => cleanup())

describe('ConsistencyPanel', () => {
  it('矛盾と精査の結果を出し、直し方を適用してチェックし直す', async () => {
    const onApplied = vi.fn()
    const view = render(<MemoryRouter><ConsistencyPanel projectIds={[7]} onApplied={onApplied} /></MemoryRouter>)
    fireEvent.click(view.getByRole('button', { name: /チェック実行/ }))
    await waitFor(() => expect(view.getByText('接点の種類（a/b）が表と違う')).toBeTruthy())
    expect(view.getByText(/矛盾あり（1）/)).toBeTruthy()
    expect(view.getByText(/ngspice も Lcapy も入っていません/)).toBeTruthy()
    expect(view.getByRole('link', { name: /ER21216 XA/ }).getAttribute('href')).toBe('/projects/7?symbol=XA')

    fireEvent.click(view.getByRole('button', { name: /修正を適用/ }))
    await waitFor(() => expect(view.getByText(/1 件を修正し、再チェックしました/)).toBeTruthy())
    expect(api.applyConsistencyFixes).toHaveBeenCalledWith([7], ['abc'])
    expect(onApplied).toHaveBeenCalled()
    expect(view.getByText('矛盾は見つかりませんでした')).toBeTruthy()
  })
})
