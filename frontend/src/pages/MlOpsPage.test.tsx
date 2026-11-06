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
  trainingClassStats: vi.fn(),
  trainingHardDrawings: vi.fn(),
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
  api.trainingClassStats.mockResolvedValue({ done_drawings: 0, thin_threshold: 30, classes: [] })
  api.trainingHardDrawings.mockResolvedValue({ threshold: 0.9, drawings: [] })
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

  it('採用判定に基準による推奨とクラス別の差を示す', async () => {
    const metric = (map50_95: number, instances: number, label: string) => ({
      label, instances, precision: 0.9, recall: 0.9, map50: 0.95, map50_95,
    })
    api.listTrainingRuns.mockResolvedValue([
      trainingRun(5, {
        decision: 'pending',
        result_model_id: model.id,
        result_model: model,
        per_class: { contact_b: metric(0.3, 8, 'b接点') },
        baseline_per_class: { contact_b: metric(0.4, 8, 'b接点') },
        review: { verdict: 'hold', reasons: ['b接点（評価用 8 件）の mAP50-95 が 0.400 から 0.300 に下がりました。'] },
      }),
    ])
    const view = openPage()

    expect(await view.findByText('見送りをおすすめします')).toBeTruthy()
    expect(view.getByText(/b接点（評価用 8 件）の mAP50-95 が 0.400 から 0.300/)).toBeTruthy()
    const table = view.getByRole('table', { name: 'クラス別の評価' })
    expect(table.textContent).toContain('b接点')
    expect(table.textContent).toContain('-0.100')
  })

  it('学習データのクラス別件数と難例の図面を示す', async () => {
    api.trainingClassStats.mockResolvedValue({
      done_drawings: 30,
      thin_threshold: 30,
      classes: [
        { key: 'contact_a', label: 'a接点', color: '#123456', done_count: 230, done_drawings: 28, total_count: 240, total_drawings: 29, thin: false },
        { key: 'contact_b', label: 'b接点', color: '#654321', done_count: 13, done_drawings: 5, total_count: 13, total_drawings: 5, thin: true },
      ],
    })
    api.trainingHardDrawings.mockResolvedValue({
      threshold: 0.9,
      drawings: [
        { project_id: 7, name: 'ER21302.png', sheet_no: 'ER21302', f1: 0.82, ai_count: 60, gold_count: 62, missed: 6, false_detections: 4, relabeled: 1, hard: true },
        { project_id: 8, name: 'ER21216.png', sheet_no: 'ER21216', f1: 0.97, ai_count: 50, gold_count: 50, missed: 1, false_detections: 1, relabeled: 0, hard: false },
      ],
    })
    const view = openPage()

    expect(await view.findByText(/30 件未満のクラス（b接点）/)).toBeTruthy()
    expect(view.getByText('少ない')).toBeTruthy()
    expect(view.getByText(/0.9 未満の図面が 1 枚あります（難例）/)).toBeTruthy()
    const link = view.getByRole('link', { name: 'ER21302' }) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/projects/7')
    expect(view.getAllByText('難例')).toHaveLength(1)
  })
})
