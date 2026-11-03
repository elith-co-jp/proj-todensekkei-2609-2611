// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { MlModel, ProjectRow, TrainingRun } from '../types'
import MlOpsPage from './MlOpsPage'

const api = vi.hoisted(() => ({
  mlStatus: vi.fn(),
  listModels: vi.fn(),
  listProjects: vi.fn(),
  listPredictions: vi.fn(),
  listTrainingRuns: vi.fn(),
  startTraining: vi.fn(),
  runAnalysis: vi.fn(),
}))
vi.mock('../api/client', () => ({ api }))

const model: MlModel = {
  id: 9,
  name: 'seq-yolo',
  version: 3,
  file_name: 'best.pt',
  sha256: 'demo',
  size_bytes: 1024,
  source: 'trained',
  is_active: false,
  metrics: null,
  note: null,
  created_at: null,
}

function trainingRun(id: number, overrides: Partial<TrainingRun>): TrainingRun {
  return {
    id,
    status: 'success',
    project_ids: [],
    image_count: 10,
    epochs: 100,
    imgsz: 1280,
    base_model: null,
    result_model_id: null,
    result_model: null,
    metrics: null,
    baseline_metrics: null,
    baseline_label: '同梱モデル',
    decision: null,
    eval_scope: null,
    log_tail: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

const row = { id: 1, name: '匿名ページ1', symbol_count: 3 } as ProjectRow

beforeEach(() => {
  vi.clearAllMocks()
  api.mlStatus.mockResolvedValue({ ultralytics: true, active_model: null, model_count: 0, training_running: null })
  api.listModels.mockResolvedValue([])
  api.listProjects.mockResolvedValue([row])
  api.listPredictions.mockResolvedValue([])
  api.listTrainingRuns.mockResolvedValue([])
  api.startTraining.mockResolvedValue(trainingRun(20, { status: 'running' }))
  api.runAnalysis.mockResolvedValue({ model, conf: 0.5, connector_conf: 0.6, results: [], detection_count: 0 })
})

afterEach(() => cleanup())

function openPage() {
  return render(
    <MemoryRouter>
      <MlOpsPage />
    </MemoryRouter>,
  )
}

describe('MlOpsPage 学習の評価範囲', () => {
  it('ER 系のコネクタしきい値を解析に渡す', async () => {
    api.mlStatus.mockResolvedValue({ ultralytics: true, active_model: model, model_count: 1, training_running: null })
    const view = openPage()
    const start = await view.findByRole('button', { name: 'AI 解析を実行' })
    expect((view.getByLabelText('コネクタのしきい値') as HTMLInputElement).value).toBe('0.6')
    fireEvent.click(start)
    await waitFor(() => expect(api.runAnalysis).toHaveBeenCalledWith([], 0.5, 0.6))
  })

  it('既定で未学習の図面で評価し、チェックを外すとその指定で学習を開始する', async () => {
    const view = openPage()
    const holdout = (await view.findByRole('checkbox', { name: '未学習の図面で評価する（推奨）' })) as HTMLInputElement
    const start = view.getByRole('button', { name: '学習を開始' }) as HTMLButtonElement
    await waitFor(() => expect(start.disabled).toBe(false))
    expect(holdout.checked).toBe(true)

    fireEvent.click(start)
    await waitFor(() => expect(api.startTraining).toHaveBeenCalledWith(expect.objectContaining({ holdout: true })))

    fireEvent.click(holdout)
    await waitFor(() => expect(start.disabled).toBe(false))
    fireEvent.click(start)
    await waitFor(() => expect(api.startTraining).toHaveBeenLastCalledWith(expect.objectContaining({ holdout: false })))
  })

  it('採用判定と学習履歴に、どの図面で評価したかを示す', async () => {
    api.listTrainingRuns.mockResolvedValue([
      trainingRun(4, {
        decision: 'pending',
        result_model_id: model.id,
        result_model: model,
        eval_scope: { mode: 'holdout', train_project_ids: [1, 2, 4, 5, 6, 8, 9, 10], val_project_ids: [3, 7] },
      }),
      trainingRun(3, {
        decision: 'rejected',
        eval_scope: { mode: 'train', train_project_ids: [1, 2, 3], val_project_ids: [1, 2, 3] },
      }),
      trainingRun(2, {
        decision: 'adopted',
        eval_scope: { mode: 'train', train_project_ids: [1, 2, 3, 4, 5, 6], val_project_ids: [1, 2, 3, 4, 5, 6] },
      }),
      trainingRun(1, { status: 'failed', eval_scope: null }),
    ])
    const view = openPage()

    expect(await view.findByText(/同じ図面で評価して比較しました/)).toBeTruthy()
    expect(view.getAllByText('評価: 学習に使っていない図面 2 枚')).toHaveLength(2)
    expect(view.getByText('図面が5枚未満のため、学習に使った図面で評価しています（実力より高く出ます）')).toBeTruthy()
    expect(view.getByText('学習に使った図面で評価しています（実力より高く出ます）')).toBeTruthy()
    expect(view.getAllByText(/^評価: |で評価しています（実力より高く出ます）$/)).toHaveLength(4)
  })
})
