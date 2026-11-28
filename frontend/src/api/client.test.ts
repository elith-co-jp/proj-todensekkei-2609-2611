import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './client'

const status = (patch: Record<string, unknown>) => {
  const body = { ultralytics: true, active_model: { id: 1 }, model_count: 1, training_running: null, analysis_running: false, ...patch }
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })))
}

afterEach(() => { vi.unstubAllGlobals() })

describe('analysisUnavailableReason', () => {
  it('解析できるときは null', async () => {
    status({})
    expect(await api.analysisUnavailableReason()).toBeNull()
  })

  it('モデル未登録・ultralytics なし・解析中は理由を返す（失敗する要求を送らない）', async () => {
    status({ active_model: null })
    expect(await api.analysisUnavailableReason()).toContain('モデルがありません')
    status({ ultralytics: false })
    expect(await api.analysisUnavailableReason()).toContain('ultralytics')
    status({ analysis_running: true })
    expect(await api.analysisUnavailableReason()).toContain('別の解析を実行中')
  })
})
