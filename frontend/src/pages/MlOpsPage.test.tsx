// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { AnalysisJob, MlModel, ProjectRow, TrainingRun } from '../types'
import MlOpsPage from './MlOpsPage'

const api = vi.hoisted(() => ({
  mlStatus: vi.fn(),
  listModels: vi.fn(),
  listProjects: vi.fn(),
  listPredictions: vi.fn(),
  listTrainingRuns: vi.fn(),
  startTraining: vi.fn(),
  startAnalysisJob: vi.fn(),
  getAnalysisJob: vi.fn(),
  listAnalysisJobs: vi.fn(),
  cancelAnalysisJob: vi.fn(),
  trainingClassStats: vi.fn(),
  uploadModel: vi.fn(),
  activateModel: vi.fn(),
  deleteModel: vi.fn(),
  modelDownloadUrl: vi.fn((id: number) => `/api/ml/models/${id}/download`),
  importPredictionsZip: vi.fn(),
  decideTrainingRun: vi.fn(),
  getPredictions: vi.fn(),
  exportStructure: vi.fn(),
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

function analysisJob(overrides: Partial<AnalysisJob>): AnalysisJob {
  return {
    id: 5,
    status: 'running',
    total: 1,
    done: 0,
    current: '匿名ページ1',
    conf: 0.5,
    connector_conf: 0.6,
    results: [],
    failures: [],
    error: null,
    cancel_requested: false,
    detection_count: 0,
    started_at: null,
    finished_at: null,
    model,
    ...overrides,
  }
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
  api.startAnalysisJob.mockResolvedValue(analysisJob({ status: 'running' }))
  api.getAnalysisJob.mockResolvedValue(analysisJob({ status: 'done', done: 1 }))
  api.listAnalysisJobs.mockResolvedValue([])
  api.trainingClassStats.mockResolvedValue({ done_drawings: 0, thin_threshold: 30, classes: [] })
  api.trainingHardDrawings.mockResolvedValue({ threshold: 0.9, drawings: [] })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

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
    await waitFor(() => expect(api.startAnalysisJob).toHaveBeenCalledWith([], 0.5, 0.6))
  })

  it('裏の解析の進み具合を出し、終われば結果と失敗した図面を知らせる', async () => {
    api.mlStatus.mockResolvedValue({ ultralytics: true, active_model: model, model_count: 1, training_running: null })
    api.listAnalysisJobs.mockResolvedValue([analysisJob({ total: 2, done: 1, current: '匿名ページ2' })])
    api.getAnalysisJob.mockResolvedValue(
      analysisJob({
        status: 'done',
        total: 2,
        done: 2,
        detection_count: 4,
        results: [{ project_id: 1, name: '匿名ページ1', detections: 4, wire_count: 3 }],
        failures: [
          { project_id: 2, name: '匿名ページ2', message: '図面画像が見つかりません' },
          { project_id: 3, name: null, message: '失敗' },
        ],
      }),
    )
    const view = openPage()
    expect(await view.findByText('解析中 1 / 2 図面')).toBeTruthy()
    expect(view.getByText(/いま: 匿名ページ2/)).toBeTruthy()
    expect(await view.findByText(/2 図面は解析できませんでした（匿名ページ2、3）/, undefined, { timeout: 3000 })).toBeTruthy()
    expect(view.queryByText('解析中 1 / 2 図面')).toBeNull()
  })

  it('解析を中止できる', async () => {
    api.mlStatus.mockResolvedValue({ ultralytics: true, active_model: model, model_count: 1, training_running: null })
    api.listAnalysisJobs.mockResolvedValue([analysisJob({ total: 3 })])
    api.getAnalysisJob.mockResolvedValue(analysisJob({ total: 3 }))
    api.cancelAnalysisJob.mockResolvedValue(analysisJob({ total: 3, cancel_requested: true }))
    const view = openPage()
    fireEvent.click(await view.findByRole('button', { name: '中止' }))
    await waitFor(() => expect(api.cancelAnalysisJob).toHaveBeenCalledWith(5))
    expect(await view.findByRole('button', { name: '中止します…' })).toBeTruthy()
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

const ready = { ultralytics: true, active_model: model, model_count: 1, training_running: null, card_model: null }

describe('MlOpsPage 操作', () => {
  it('外部実行モードの案内と、読み込みの失敗を出す', async () => {
    api.mlStatus.mockResolvedValue({ ultralytics: false, active_model: null, model_count: 0, training_running: null })
    api.listProjects.mockResolvedValue([])
    const view = openPage()
    expect(await view.findByText(/ultralytics が入っていません（外部実行モード）/)).toBeTruthy()
    expect((view.getByRole('button', { name: 'モデルを先に登録してください' }) as HTMLButtonElement).disabled).toBe(true)
    expect(view.getAllByText('図面がありません').length).toBe(1)
    cleanup()
    api.mlStatus.mockResolvedValue({ ...ready, ultralytics: false })
    const external = openPage()
    expect(await external.findByRole('button', { name: 'この環境では実行できません（外部実行）' })).toBeTruthy()
    cleanup()
    api.mlStatus.mockRejectedValueOnce(new Error('サーバに接続できません'))
    const failed = openPage()
    expect((await failed.findByRole('alert')).textContent).toBe('サーバに接続できません')
    cleanup()
    api.mlStatus.mockRejectedValueOnce('文字列')
    expect((await openPage().findByRole('alert')).textContent).toBe('文字列')
  })

  it('対象の図面を選んで、選んだしきい値で解析し、結果の JSON を開閉して構造 JSON を出す', async () => {
    api.mlStatus.mockResolvedValue({ ...ready, card_model: { name: 'cards_v1' } })
    api.listProjects.mockResolvedValue([row, { id: 2, name: '匿名ページ2', symbol_count: 0 } as ProjectRow])
    api.listPredictions.mockResolvedValue([{ project_id: 1, count: 7 }])
    api.startAnalysisJob.mockResolvedValue(analysisJob({ status: 'running', total: 2 }))
    api.getPredictions.mockResolvedValueOnce({ count: 1, detections: [{ class_key: 'lamp' }] })
    api.exportStructure.mockResolvedValueOnce({ name: 'structure.json' }).mockRejectedValueOnce(new Error('構造解析が未実行の図面があります'))
      .mockRejectedValueOnce('x')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const view = openPage()
    expect(await view.findByText('カードは別モデル（cards_v1）')).toBeTruthy()
    expect(view.getByText('seq-yolo v3')).toBeTruthy()
    expect(view.getByText('検出 7')).toBeTruthy()
    fireEvent.change(view.getByLabelText('信頼度しきい値'), { target: { value: '0.4' } })
    fireEvent.change(view.getByLabelText('コネクタのしきい値'), { target: { value: '0.7' } })
    const [inferAll] = view.getAllByRole('button', { name: '全選択' })
    fireEvent.click(inferAll)
    expect(view.getByText('対象: 選んだ 2 枚')).toBeTruthy()
    fireEvent.click(view.getAllByRole('button', { name: '選択解除' })[0])
    expect(view.getByText('対象: 全 2 枚（未選択のため）')).toBeTruthy()
    fireEvent.click(view.getAllByRole('checkbox')[0])
    fireEvent.click(view.getAllByRole('checkbox')[0])
    fireEvent.click(view.getAllByRole('checkbox')[1])
    fireEvent.click(view.getByRole('button', { name: 'AI 解析を実行' }))
    await waitFor(() => expect(api.startAnalysisJob).toHaveBeenCalledWith([2], 0.4, 0.7))
    expect(await view.findByRole('button', { name: '解析中…' })).toBeTruthy()

    api.getAnalysisJob.mockResolvedValue(analysisJob({
      status: 'done', total: 2, done: 2, detection_count: 3,
      results: [
        { project_id: 1, name: '匿名ページ1', detections: 2, wire_count: 4, structure_available: false, warnings: ['ラベル'] },
        { project_id: 2, name: '匿名ページ2', detections: 1, structure_available: true },
      ] as AnalysisJob['results'],
    }))
    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(await view.findByText('解析結果を保存しました')).toBeTruthy()
    expect(view.getByText(/3 件のシンボル、4 本の配線を検出しました（2 図面）。一部のラベル読み取りに問題があります/)).toBeTruthy()

    fireEvent.click(view.getByRole('button', { name: 'JSON' }))
    expect(await view.findByText(/"class_key": "lamp"/)).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'JSON を閉じる' }))
    expect(view.queryByText(/"class_key": "lamp"/)).toBeNull()
    fireEvent.click(view.getByRole('button', { name: 'JSON' }))
    expect(await view.findByText(/"class_key": "lamp"/)).toBeTruthy()
    expect(api.getPredictions).toHaveBeenCalledTimes(1)

    fireEvent.click(view.getByRole('button', { name: '構造JSON' }))
    await waitFor(() => expect(api.exportStructure).toHaveBeenCalledWith([1, 2]))
    fireEvent.click(view.getByRole('button', { name: '構造JSONをダウンロード' }))
    expect(await view.findByText('構造解析が未実行の図面があります')).toBeTruthy()
    expect(api.exportStructure).toHaveBeenLastCalledWith([2])
    fireEvent.click(view.getByRole('button', { name: '構造JSONをダウンロード' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('x'))
    fireEvent.click(view.getByRole('button', { name: '閉じる' }))
    expect(view.queryByRole('alert')).toBeNull()
  })

  it('JSON を取れなければ理由を出す', async () => {
    api.mlStatus.mockResolvedValue(ready)
    api.listAnalysisJobs.mockResolvedValue([analysisJob({})])
    api.getAnalysisJob.mockResolvedValue(analysisJob({
      status: 'cancelled', done: 0, total: 2,
      results: [{ project_id: 1, name: '匿名ページ1', detections: 0 }] as AnalysisJob['results'],
    }))
    api.getPredictions.mockRejectedValueOnce(new Error('推論結果がありません')).mockRejectedValueOnce('y')
    const view = openPage()
    expect(await view.findByText('解析を中止しました（0 / 2 図面）', undefined, { timeout: 3000 })).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'JSON' }))
    expect(await view.findByText('推論結果の取得に失敗しました')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'JSON' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('y'))
  })

  it('解析のジョブの失敗・始められない・中止できないを知らせる', async () => {
    api.mlStatus.mockResolvedValue(ready)
    api.startAnalysisJob.mockRejectedValueOnce(new Error('別の解析を実行中です')).mockRejectedValueOnce('z')
    const view = openPage()
    fireEvent.click(await view.findByRole('button', { name: 'AI 解析を実行' }))
    expect(await view.findByText('別の解析を実行中です')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'AI 解析を実行' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('z'))

    api.startAnalysisJob.mockResolvedValue(analysisJob({}))
    api.getAnalysisJob.mockResolvedValue(analysisJob({}))
    api.cancelAnalysisJob.mockRejectedValueOnce(new Error('見つかりません')).mockRejectedValueOnce('w')
    fireEvent.click(view.getByRole('button', { name: 'AI 解析を実行' }))
    fireEvent.click(await view.findByRole('button', { name: '中止' }))
    expect(await view.findByText('中止できませんでした')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: '中止' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('w'))
    api.getAnalysisJob.mockResolvedValue(analysisJob({ status: 'failed', error: null }))
    await waitFor(() => expect(view.getAllByText('解析に失敗しました')).toHaveLength(2), { timeout: 3000 })
  })

  it('解析のジョブの失敗の理由を出す', async () => {
    api.mlStatus.mockResolvedValue(ready)
    api.listAnalysisJobs.mockResolvedValue([analysisJob({})])
    api.getAnalysisJob.mockResolvedValue(analysisJob({ status: 'failed', error: 'モデルを読み込めません' }))
    const view = openPage()
    expect(await view.findByText('モデルを読み込めません', undefined, { timeout: 3000 })).toBeTruthy()
  })

  it('外部の推論結果 ZIP を取り込み、対応づけできなかったものを示す', async () => {
    api.mlStatus.mockResolvedValue(ready)
    api.importPredictionsZip.mockResolvedValueOnce({
      count: 4, results: [{ project_id: 1, name: '匿名ページ1', detections: 4 }], unmatched_files: ['x.txt'], missing_project_ids: [99],
    }).mockResolvedValueOnce({ count: 1, results: [{ project_id: 1, name: '匿名ページ1', detections: 1 }], unmatched_files: [], missing_project_ids: [] })
      .mockResolvedValueOnce({ count: 1, results: [], unmatched_files: ['y.txt'], missing_project_ids: [] })
      .mockResolvedValueOnce({ count: 1, results: [], unmatched_files: [], missing_project_ids: [5] })
      .mockRejectedValueOnce(new Error('ZIP を読めません')).mockRejectedValueOnce('v')
    const view = openPage()
    await view.findByRole('button', { name: /推論結果 ZIP を選択/ })
    const input = view.container.querySelector('input[accept=".zip,application/zip"]') as HTMLInputElement
    const click = vi.spyOn(input, 'click').mockImplementation(() => undefined)
    fireEvent.click(view.getByRole('button', { name: /推論結果 ZIP を選択/ }))
    expect(click).toHaveBeenCalled()
    fireEvent.change(input, { target: { files: [] } })
    fireEvent.change(input, { target: { files: [new File(['z'], 'labels.zip')] } })
    expect(await view.findByText('labels.zip から 4 件を読み込みました。')).toBeTruthy()
    expect(view.getByText(/対応づけできなかったファイル: x.txt ／ 存在しない図面 ID: 99/)).toBeTruthy()
    expect(view.queryByRole('button', { name: '構造JSON' })).toBeNull()
    fireEvent.change(input, { target: { files: [new File(['z'], 'labels.zip')] } })
    await waitFor(() => expect(view.queryByText(/対応づけできなかったファイル/)).toBeNull())
    fireEvent.change(input, { target: { files: [new File(['z'], 'labels.zip')] } })
    expect(await view.findByText(/対応づけできなかったファイル: y.txt ／ 存在しない図面 ID: なし/)).toBeTruthy()
    fireEvent.change(input, { target: { files: [new File(['z'], 'labels.zip')] } })
    expect(await view.findByText(/対応づけできなかったファイル: なし ／ 存在しない図面 ID: 5/)).toBeTruthy()
    fireEvent.change(input, { target: { files: [new File(['z'], 'labels.zip')] } })
    expect(await view.findByText('ZIP を読めません')).toBeTruthy()
    fireEvent.change(input, { target: { files: [new File(['z'], 'labels.zip')] } })
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('v'))
  })

  it('モデルを登録・切り替え・削除し、失敗を知らせる', async () => {
    api.listModels.mockResolvedValue([
      { ...model, id: 1, is_active: true, source: 'upload', size_bytes: 3 * 1024 * 1024, metrics: { 'metrics/mAP50-95(B)': '0.71' } },
      { ...model, id: 2, source: 'bundled', metrics: {} },
      { ...model, id: 9 },
    ])
    api.listTrainingRuns.mockResolvedValue([trainingRun(3, { status: 'running', decision: 'pending', result_model_id: 9 })])
    api.uploadModel.mockResolvedValueOnce({ ...model, name: 'new', version: 4 }).mockRejectedValueOnce(new Error('pt ではありません'))
      .mockRejectedValueOnce('u')
    api.activateModel.mockResolvedValueOnce(model).mockRejectedValueOnce(new Error('切替不可')).mockRejectedValueOnce('t')
    api.deleteModel.mockResolvedValueOnce({ deleted: 2 }).mockRejectedValueOnce(new Error('削除不可')).mockRejectedValueOnce('s')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)
    const view = openPage()
    expect(await view.findByText('使用中')).toBeTruthy()
    expect(view.getByText('同梱')).toBeTruthy()
    expect(view.getByText('学習済み')).toBeTruthy()
    expect(view.getAllByText('採用待ち').length).toBeGreaterThan(0)
    expect(view.getByText(/3.0 MB/).textContent).toContain('mAP50-95=0.71')
    expect(view.getAllByText(/mAP50-95=—/).length).toBe(1)
    expect(view.container.querySelector('a[href="/api/ml/models/2/download"]')).toBeTruthy()

    const input = view.container.querySelector('input[accept=".pt,.pth,.torchscript"]') as HTMLInputElement
    const click = vi.spyOn(input, 'click').mockImplementation(() => undefined)
    fireEvent.click(view.getByRole('button', { name: /モデル登録/ }))
    expect(click).toHaveBeenCalled()
    fireEvent.change(input, { target: { files: [] } })
    fireEvent.change(input, { target: { files: [new File(['w'], 'best.pt')] } })
    expect(await view.findByText('モデルを登録しました')).toBeTruthy()
    expect(view.getByText('new (v4)')).toBeTruthy()
    fireEvent.change(input, { target: { files: [new File(['w'], 'best.pt')] } })
    expect(await view.findByText('pt ではありません')).toBeTruthy()
    fireEvent.change(input, { target: { files: [new File(['w'], 'best.pt')] } })
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('u'))

    for (const message of [null, '切替不可', 't']) {
      fireEvent.click(view.getAllByRole('button', { name: '使用する' })[0])
      if (message) await waitFor(() => expect(view.getByRole('alert').textContent).toContain(message))
      else await waitFor(() => expect(api.activateModel).toHaveBeenCalledWith(2))
    }

    fireEvent.click(view.getAllByTitle('削除')[1])
    expect(api.deleteModel).not.toHaveBeenCalled()
    for (const message of [null, '削除不可', 's']) {
      fireEvent.click(view.getAllByTitle('削除')[1])
      if (message) await waitFor(() => expect(view.getByRole('alert').textContent).toContain(message))
      else await waitFor(() => expect(api.deleteModel).toHaveBeenCalledWith(2))
    }
    expect(confirm).toHaveBeenCalled()
  })

  it('学習の設定を変えて始め、学習中は状況を取り直す', async () => {
    api.mlStatus.mockResolvedValue(ready)
    api.listProjects.mockResolvedValue([row, { id: 2, name: '匿名ページ2', symbol_count: 5 } as ProjectRow])
    api.startTraining.mockRejectedValueOnce(new Error('学習対象の図面がありません')).mockRejectedValueOnce('r')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const view = openPage()
    await view.findByText('5 シンボル')
    fireEvent.change(view.getByLabelText('エポック数'), { target: { value: '5' } })
    fireEvent.change(view.getByLabelText('画像サイズ (imgsz)'), { target: { value: '640' } })
    fireEvent.click(view.getByLabelText('完了（done）の図面だけを学習に使う'))
    const [, trainAll] = view.getAllByRole('button', { name: '全選択' })
    fireEvent.click(trainAll)
    fireEvent.click(view.getAllByRole('button', { name: '選択解除' })[1])
    fireEvent.click(view.getAllByRole('checkbox').at(-1)!)
    fireEvent.click(view.getAllByRole('checkbox').at(-1)!)
    fireEvent.click(view.getAllByRole('checkbox').at(-1)!)
    fireEvent.click(view.getByRole('button', { name: '学習を開始' }))
    expect(await view.findByText('学習対象の図面がありません')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: '学習を開始' }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('r'))
    expect(api.startTraining).toHaveBeenLastCalledWith({ project_ids: [2], only_done: false, epochs: 5, imgsz: 640, holdout: true })

    api.mlStatus.mockResolvedValue({ ...ready, training_running: trainingRun(20, { status: 'running' }) })
    api.listTrainingRuns.mockResolvedValue([trainingRun(20, { status: 'running', log_tail: 'epoch 1/5' })])
    fireEvent.click(view.getByRole('button', { name: '学習を開始' }))
    expect(await view.findByText('学習を開始しました（ジョブ #20）')).toBeTruthy()
    expect(await view.findByRole('button', { name: '学習を実行中…' })).toBeTruthy()
    expect(view.getByText('学習中')).toBeTruthy()
    const calls = api.mlStatus.mock.calls.length
    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(api.mlStatus.mock.calls.length).toBeGreaterThan(calls)
    fireEvent.click(view.getByRole('button', { name: 'ログ' }))
    expect(view.getByText('epoch 1/5')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'ログ' }))
    expect(view.queryByText('epoch 1/5')).toBeNull()
    expect(view.getByText('実行中')).toBeTruthy()
  })

  it('採用・見送りを登録し、指標の差を色分けする', async () => {
    const pending = trainingRun(6, {
      decision: 'pending', result_model_id: 9, result_model: model, baseline_label: null,
      metrics: { 'metrics/mAP50-95(B)': '0.7', 'metrics/mAP50(B)': '0.8', 'metrics/precision(B)': '0.5' },
      baseline_metrics: { 'metrics/mAP50-95(B)': '0.6', 'metrics/mAP50(B)': '0.9', 'metrics/precision(B)': '0.5' },
    })
    api.listTrainingRuns.mockResolvedValue([pending, trainingRun(5, { decision: 'adopted', metrics: { 'metrics/mAP50-95(B)': '0.6' } })])
    api.decideTrainingRun.mockResolvedValueOnce({ ...pending, decision: 'adopted' })
      .mockResolvedValueOnce({ ...pending, decision: 'rejected', result_model: null })
      .mockRejectedValueOnce(new Error('判定済みです')).mockRejectedValueOnce('q')
    const view = openPage()
    expect(await view.findByText(/を蓄積済みデータ（10 枚）で評価し/)).toBeTruthy()
    expect(view.getByText('+0.1000').className).toContain('emerald')
    expect(view.getByText('-0.1000').className).toContain('rose')
    expect(view.getByText('0.0000').className).toContain('slate')
    expect(view.getByText('mAP 0.6')).toBeTruthy()
    expect(view.getByText('比較対象（—）')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /このモデルを採用する/ }))
    expect(await view.findByText('seq-yolo v3 を採用しました')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /見送る/ }))
    expect(await view.findByText('新しいモデル を見送りました')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /見送る/ }))
    expect(await view.findByText('判定済みです')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /見送る/ }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('q'))
  })

  it('指標が無い採用待ちは、エディタで確かめるよう案内する', async () => {
    api.listTrainingRuns.mockResolvedValue([trainingRun(7, { decision: 'pending', result_model_id: 9, result_model: model })])
    const view = openPage()
    expect(await view.findByText(/評価指標を取得できませんでした/)).toBeTruthy()
  })
})

describe('TrainingQuality', () => {
  it('クラス別の差に片方しか無いクラス・推奨の種類・読み込みの失敗を出す', async () => {
    const metric = (map50_95: number) => ({ label: 'L', instances: 1, precision: 1, recall: 1, map50: 1, map50_95 })
    api.listTrainingRuns.mockResolvedValue([
      trainingRun(8, {
        decision: 'pending', result_model_id: 9, result_model: model,
        per_class: { a: metric(0.5), only_new: metric(0.4), same: metric(0.3) },
        baseline_per_class: { a: metric(0.4), only_old: { ...metric(0.2), label: '' }, same: metric(0.3) },
        review: { verdict: 'caution', reasons: [] },
      }),
      trainingRun(9, { decision: 'pending', result_model_id: 9, result_model: model, review: { verdict: 'adopt', reasons: ['良い'] } }),
      trainingRun(10, { decision: 'pending', result_model_id: 9, result_model: model, review: { verdict: 'unknown', reasons: [] } }),
    ])
    api.trainingClassStats.mockRejectedValue(new Error('集計できません'))
    const view = openPage()
    const table = await view.findByRole('table', { name: 'クラス別の評価' })
    expect(table.textContent).toContain('+0.100')
    expect(table.textContent).toContain('0.000')
    expect(table.textContent).toContain('only_old')
    expect(view.getByText('この評価だけでは判断できません')).toBeTruthy()
    expect(view.getByText('採用をおすすめします')).toBeTruthy()
    expect(view.getByText('自動では判定できません')).toBeTruthy()
    expect(await view.findByText(/学習データの状態を読み込めませんでした: 集計できません/)).toBeTruthy()
  })

  it('十分な件数・難例なし・AI の結果なしの案内と、文字列の失敗', async () => {
    api.trainingClassStats.mockResolvedValue({ done_drawings: 3, thin_threshold: 30, classes: [
      { key: 'a', label: 'a接点', color: '#000', done_count: 40, done_drawings: 3, total_count: 40, total_drawings: 3, thin: false },
    ] })
    api.trainingHardDrawings.mockResolvedValue({ threshold: 0.9, drawings: [
      { project_id: 1, name: 'n.png', sheet_no: null, f1: 0.95, ai_count: 1, gold_count: 1, missed: 0, false_detections: 0, relabeled: 0, hard: false },
    ] })
    const view = openPage()
    expect(await view.findByText(/すべてのクラスに十分な件数があります/)).toBeTruthy()
    expect(view.getByText(/どの図面も 0.9 以上です/)).toBeTruthy()
    expect(view.getByRole('link', { name: 'n.png' })).toBeTruthy()
    cleanup()
    api.trainingHardDrawings.mockResolvedValue({ threshold: 0.9, drawings: [] })
    const empty = openPage()
    expect(await empty.findByText(/AI の結果がある完了の図面はまだありません/)).toBeTruthy()
    cleanup()
    api.trainingHardDrawings.mockRejectedValue('文字列')
    expect(await openPage().findByText(/学習データの状態を読み込めませんでした: 文字列/)).toBeTruthy()
    cleanup()
    api.trainingHardDrawings.mockResolvedValue({ threshold: 0.9, drawings: [] })
    // 画面を離れたあとに返った結果は捨てる
    let late: (v: unknown) => void = () => undefined
    let lateFail: (e: unknown) => void = () => undefined
    api.trainingClassStats.mockImplementationOnce(() => new Promise((resolve) => { late = resolve }))
    openPage().unmount()
    late({ done_drawings: 0, thin_threshold: 30, classes: [] })
    api.trainingClassStats.mockImplementationOnce(() => new Promise((_, reject) => { lateFail = reject }))
    openPage().unmount()
    lateFail(new Error('late'))
    await Promise.resolve()
  })
})
