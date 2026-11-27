import type {
  AnalysisJob,
  IoTableSummary,
  DrawingSet,
  DrawingSetCoverage,
  DrawingSetSuggestion,
  AiImportResult,
  AttributeDefinitions,
  AttributeImportReport,
  CircuitChange,
  CircuitChangeSet,
  CircuitEditOp,
  CircuitEditPreview,
  CircuitEditTools,
  CircuitImpact,
  CircuitResult,
  CircuitTimeline,
  CircuitTimelineEvent,
  CircuitWhatIf,
  ConsistencyResult,
  TraceBackward,
  TraceForward,
  TraceTargets,
  CircuitSummary,
  CoilWiringSplitResult,
  ClassStats,
  HardDrawings,
  InferenceImportResult,
  LabelLinkResult,
  InferenceRunResponse,
  MlModel,
  MlStatus,
  PredictionSummary,
  ProjectDetail,
  ProjectPredictions,
  ProjectRow,
  ReviewSummary,
  SheetNumberCandidate,
  Stats,
  SymbolClass,
  StructureResult,
  TrainingRun,
  DeviceIndex,
  LogicDiagramView,
  LogicSimulation,
  DemoView,
  LogicTimelineEvent,
  ProjectTables,
  TableCellValue,
} from '../types'

import { DEFAULT_DETECTION_CONF } from './defaults'

export const BASE = import.meta.env.VITE_API_BASE ?? ''

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    let detail = `通信に失敗しました (${res.status})`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
      else if (body?.detail?.message) detail = String(body.detail.message)
      else if (body?.detail) detail = JSON.stringify(body.detail)
    } catch {
      /* JSON でない場合はそのまま */
    }
    throw new Error(detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

async function download(path: string, init: RequestInit, fallbackName: string) {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    let detail = `出力に失敗しました (${res.status})`
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* noop */
    }
    throw new Error(detail)
  }
  const disposition = res.headers.get('content-disposition') ?? ''
  const match = /filename="?([^"]+)"?/.exec(disposition)
  const name = match ? match[1] : fallbackName
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
  return { name, size: blob.size }
}

export const api = {
  health: () => req<{ status: string; desktop: boolean }>('/api/health'),

  desktopHeartbeat: () => req<{ status: string }>('/api/desktop/heartbeat', { method: 'POST' }),

  shutdownDesktop: () => req<{ status: string }>('/api/desktop/shutdown', { method: 'POST' }),

  listClasses: () => req<SymbolClass[]>('/api/classes'),

  listProjects: (q?: string) =>
    req<ProjectRow[]>(`/api/projects${q ? `?q=${encodeURIComponent(q)}` : ''}`),

  getProject: (id: number) => req<ProjectDetail>(`/api/projects/${id}`),

  createProjects: (files: File[]) => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    return req<{ project_ids: number[]; count: number }>('/api/projects', {
      method: 'POST',
      body: fd,
    })
  },

  saveAnnotations: (id: number, payload: unknown) =>
    req<{ symbol_count: number; connection_count: number; skipped: string[] }>(
      `/api/projects/${id}/annotations`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    ),

  updateMeta: (id: number, payload: Record<string, string | null>) =>
    req<{ id: number; status: string }>(`/api/projects/${id}/meta`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  deleteProject: (id: number) => req<{ deleted: number }>(`/api/projects/${id}`, { method: 'DELETE' }),

  labelLinks: (id: number, symbols: unknown[]) =>
    req<LabelLinkResult>(`/api/projects/${id}/label-links`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols }),
    }),

  suggestSheetNumber: (id: number) =>
    req<{ candidates: SheetNumberCandidate[] }>(`/api/projects/${id}/sheet-number/suggest`, {
      method: 'POST',
    }),

  bulkDelete: (ids: number[]) =>
    req<{ deleted: number[] }>('/api/projects/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    }),

  imageUrl: (id: number) => `${BASE}/api/projects/${id}/image`,

  thumbnailUrl: (id: number) => `${BASE}/api/projects/${id}/thumbnail`,

  stats: () => req<Stats>('/api/stats'),

  exportProject: (id: number) =>
    download(`/api/projects/${id}/export`, {}, `seqanno_project_${id}.zip`),

  exportBulk: (opts: {
    ids: number[]
    val_ratio?: number
    seed?: number
    include_compat_layout?: boolean
  }) =>
    download(
      '/api/export',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(opts),
      },
      'seqanno_export.zip',
    ),

  importZip: (file: File) => {
    const fd = new FormData()
    fd.append('archive', file)
    return req<{ mode: string; project_ids: number[]; count: number }>('/api/import', {
      method: 'POST',
      body: fd,
    })
  },

  // ---------- AI 改善サイクル ----------
  mlStatus: () => req<MlStatus>('/api/ml/status'),
  /**
   * サーバで AI 解析できない理由（モデル未登録・ultralytics なし）。できるなら null。
   * できないと分かっている解析の要求を送らない（送ると 400 になり、ブラウザの console にエラーが出る）。
   */
  analysisUnavailableReason: async (): Promise<string | null> => {
    const status = await req<MlStatus>('/api/ml/status')
    if (!status.active_model) return '推論に使うモデルがありません。「AI 学習」の画面で先にモデルを登録してください'
    if (!status.ultralytics) return 'この環境では ultralytics が入っていないため、ここでは AI 解析できません。「AI 学習」の画面から外部で実行した結果を取り込んでください'
    return null
  },

  listModels: () => req<MlModel[]>('/api/ml/models'),

  uploadModel: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<MlModel>('/api/ml/models', { method: 'POST', body: fd })
  },

  activateModel: (id: number) =>
    req<MlModel>(`/api/ml/models/${id}/activate`, { method: 'POST' }),

  deleteModel: (id: number) =>
    req<{ deleted: number }>(`/api/ml/models/${id}`, { method: 'DELETE' }),

  modelDownloadUrl: (id: number) => `${BASE}/api/ml/models/${id}/download`,

  runInference: (projectIds: number[], conf = DEFAULT_DETECTION_CONF, connectorConf: number | null = null) =>
    req<InferenceRunResponse>('/api/ml/inference/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, conf, connector_conf: connectorConf }),
    }),

  runAnalysis: (projectIds: number[], conf = DEFAULT_DETECTION_CONF, connectorConf: number | null = null) =>
    req<InferenceRunResponse>('/api/ml/analysis/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, conf, connector_conf: connectorConf }),
    }),

  // 全図面の解析を裏で回す（DD-0039）。進み具合は getAnalysisJob で取りに行く
  startAnalysisJob: (projectIds: number[], conf = DEFAULT_DETECTION_CONF, connectorConf: number | null = null) =>
    req<AnalysisJob>('/api/ml/analysis/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, conf, connector_conf: connectorConf }),
    }),

  getAnalysisJob: (jobId: number) => req<AnalysisJob>(`/api/ml/analysis/jobs/${jobId}`),

  listAnalysisJobs: () => req<AnalysisJob[]>('/api/ml/analysis/jobs'),

  cancelAnalysisJob: (jobId: number) => req<AnalysisJob>(`/api/ml/analysis/jobs/${jobId}/cancel`, { method: 'POST' }),

  // 図面セット（DD-0041）
  listDrawingSets: () => req<DrawingSet[]>('/api/drawing-sets'),

  createDrawingSet: (name: string, projectIds: number[]) =>
    req<DrawingSet>('/api/drawing-sets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, project_ids: projectIds }),
    }),

  updateDrawingSet: (id: number, change: { name?: string; project_ids?: number[]; note?: string }) =>
    req<DrawingSet>(`/api/drawing-sets/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(change),
    }),

  deleteDrawingSet: (id: number) => req<void>(`/api/drawing-sets/${id}`, { method: 'DELETE' }),

  drawingSetCoverage: (id: number) => req<DrawingSetCoverage>(`/api/drawing-sets/${id}/coverage`),

  suggestDrawingSets: () => req<DrawingSetSuggestion[]>('/api/drawing-sets/suggest'),

  getTables: (projectId: number) => req<ProjectTables>(`/api/projects/${projectId}/tables`),

  saveTable: (projectId: number, key: string, values: TableCellValue[], confirm: boolean) =>
    req<ProjectTables>(`/api/projects/${projectId}/tables/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values, confirm }),
    }),

  resetTable: (projectId: number, key: string) =>
    req<ProjectTables>(`/api/projects/${projectId}/tables/${encodeURIComponent(key)}`, { method: 'DELETE' }),

  getStructure: (projectId: number) =>
    req<{ project_id: number; result: StructureResult | null }>(`/api/ml/projects/${projectId}/structure`),

  exportStructure: (projectIds: number[]) =>
    download('/api/ml/analysis/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds }),
    }, 'structure.json'),

  importPredictionsZip: (file: File) => {
    const fd = new FormData()
    fd.append('archive', file)
    return req<InferenceImportResult>('/api/ml/inference/import', {
      method: 'POST',
      body: fd,
    })
  },

  listPredictions: () => req<PredictionSummary[]>('/api/ml/predictions'),

  getPredictions: (projectId: number) =>
    req<ProjectPredictions>(`/api/ml/projects/${projectId}/predictions`),

  getReviewSummary: (projectId: number) =>
    req<ReviewSummary>(`/api/ml/projects/${projectId}/review-summary`),

  /** エディタの現在の内容（未保存を含む）を送り、足す AI の候補だけを受け取る。サーバーには保存しない */
  aiImport: (projectId: number, payload: unknown) =>
    req<AiImportResult>(`/api/ml/projects/${projectId}/ai-import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  startTraining: (opts: {
    project_ids?: number[]
    only_done?: boolean
    epochs?: number
    imgsz?: number
    base_model?: string | null
    holdout?: boolean
  }) =>
    req<TrainingRun>('/api/ml/training/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    }),

  listTrainingRuns: () => req<TrainingRun[]>('/api/ml/training/runs'),

  getTrainingRun: (id: number) => req<TrainingRun>(`/api/ml/training/runs/${id}`),

  decideTrainingRun: (id: number, decision: 'adopt' | 'reject') =>
    req<TrainingRun>(`/api/ml/training/runs/${id}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    }),

  trainingClassStats: () => req<ClassStats>('/api/ml/training/class-stats'),

  trainingHardDrawings: () => req<HardDrawings>('/api/ml/training/hard-drawings'),

  buildCircuit: (projectIds: number[]) =>
    req<CircuitSummary>('/api/circuit/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds }),
    }),

  simulateCircuit: (projectIds: number[], inputs: Record<string, boolean>, energized: Record<string, boolean>) =>
    req<CircuitResult>('/api/circuit/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, inputs, energized }),
    }),

  downloadNetlist: (projectIds: number[], inputs: Record<string, boolean>) =>
    download('/api/circuit/netlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, inputs }),
    }, 'circuit.cir'),

  circuitWhatIf: (projectIds: number[], inputs: Record<string, boolean>, energized: Record<string, boolean>, change: CircuitChange) =>
    req<CircuitWhatIf>('/api/circuit/what-if', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, inputs, energized, change }),
    }),

  circuitTimeline: (projectIds: number[], events: CircuitTimelineEvent[], delays: Record<string, number>, until?: number) =>
    req<CircuitTimeline>('/api/circuit/timeline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, events, delays, until }),
    }),

  logicDiagram: (projectIds: number[], options: { targets?: string[]; inputs?: Record<string, boolean>; values?: Record<string, boolean> } = {}) =>
    req<LogicDiagramView>('/api/circuit/logic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, targets: options.targets ?? [], inputs: options.inputs ?? {}, values: options.values ?? null }),
    }),

  logicSimulate: (projectIds: number[], events: LogicTimelineEvent[], options: { targets?: string[]; inputs?: Record<string, boolean>; until?: number } = {}) =>
    req<LogicSimulation>('/api/circuit/logic/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, events, targets: options.targets ?? [], inputs: options.inputs ?? {}, until: options.until, compare: true }),
    }),

  demoView: (projectIds: number[], inputs: Record<string, boolean> = {}, energized: Record<string, boolean> = {}) =>
    req<DemoView>('/api/circuit/demo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, inputs, energized }),
    }),

  /** I/O 表（他盤との信号の一覧。DD-0050）。登録すると回路を組むたびに接点へ外部入力と信号名を入れる */
  ioTable: () => req<IoTableSummary>('/api/circuit/io-table'),
  uploadIoTable: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<IoTableSummary>('/api/circuit/io-table', { method: 'PUT', body: fd })
  },
  deleteIoTable: () => req<IoTableSummary>('/api/circuit/io-table', { method: 'DELETE' }),

  downloadLogicSvg: (projectIds: number[], options: { targets?: string[]; values?: Record<string, boolean> } = {}) =>
    download('/api/circuit/logic/svg', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, targets: options.targets ?? [], values: options.values ?? null }),
    }, 'logic.svg'),

  circuitImpact: (projectIds: number[], device: string) =>
    req<CircuitImpact>('/api/circuit/impact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, device }),
    }),

  circuitEditTools: () => req<CircuitEditTools>('/api/circuit/edit/tools'),

  circuitEditPreview: (projectIds: number[], ops: CircuitEditOp[]) =>
    req<CircuitEditPreview>('/api/circuit/edit/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, ops }),
    }),

  circuitEditApply: (projectIds: number[], ops: CircuitEditOp[], title: string, reason: string | null, baseHash: string | null) =>
    req<{ change_set: CircuitChangeSet; preview: CircuitEditPreview }>('/api/circuit/edit/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, ops, title, reason, author: 'user', base_hash: baseHash }),
    }),

  circuitEditHistory: (projectIds: number[]) =>
    req<{ change_sets: CircuitChangeSet[] }>(`/api/circuit/edit/history?project_ids=${projectIds.join(',')}`),

  circuitEditUndo: (id: number, force = false) =>
    req<{ change_set: CircuitChangeSet }>(`/api/circuit/edit/${id}/undo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force }),
    }),

  downloadCircuitCode: (projectIds: number[]) =>
    download('/api/circuit/code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds }),
    }, 'circuit_code.py'),

  deviceIndex: (projectIds: number[]) =>
    req<DeviceIndex>('/api/circuit/devices', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds }),
    }),

  downloadDeviceIndex: (projectIds: number[]) =>
    download(`/api/circuit/devices.xlsx?project_ids=${projectIds.join(',')}`, {}, 'device_index.xlsx'),

  traceTargets: (projectIds: number[]) =>
    req<TraceTargets>('/api/circuit/trace/targets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds }),
    }),

  traceBackward: (projectIds: number[], target: string, states: Record<string, boolean | null>, energized: Record<string, boolean> = {}) =>
    req<TraceBackward>('/api/circuit/trace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, mode: 'backward', target, states, energized }),
    }),

  traceForward: (projectIds: number[], device: string, states?: Record<string, boolean | null>) =>
    req<TraceForward>('/api/circuit/trace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, mode: 'forward', device, states: states ?? {} }),
    }),

  downloadAnnotationTable: (projectIds: number[], drawingSet = '') =>
    download(`/api/exchange/table?project_ids=${projectIds.join(',')}&drawing_set=${encodeURIComponent(drawingSet)}`,
      { method: 'GET' }, 'annotation_table.xlsx'),

  downloadCvat: (projectIds: number[]) =>
    download(`/api/exchange/cvat?project_ids=${projectIds.join(',')}`, { method: 'GET' }, 'annotations.xml'),

  checkConsistency: (projectIds: number[]) =>
    req<ConsistencyResult>('/api/circuit/consistency', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds }),
    }),

  applyConsistencyFixes: (projectIds: number[], issueIds: string[]) =>
    req<ConsistencyResult>('/api/circuit/consistency/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, issue_ids: issueIds }),
    }),

  splitCoilWires: (annotations: { symbols: unknown[]; connections: unknown[] }, imageWidth: number, imageHeight: number) =>
    req<CoilWiringSplitResult>('/api/circuit/split-coil-wires', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...annotations, image_width: imageWidth, image_height: imageHeight }),
    }),

  attributeDefinitions: () => req<AttributeDefinitions>('/api/attributes/definitions'),

  exportAttributes: (projectIds: number[], template: boolean) =>
    download('/api/attributes/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, template }),
    }, template ? 'object_attributes_template.json' : 'object_attributes.json'),

  importAttributes: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<AttributeImportReport>('/api/attributes/import', { method: 'POST', body: fd })
  },
}

