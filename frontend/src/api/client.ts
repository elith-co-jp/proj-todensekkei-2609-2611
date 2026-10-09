import type {
  InferenceImportResult,
  InferenceRunResponse,
  MlModel,
  MlStatus,
  PredictionSummary,
  ProjectDetail,
  ProjectPredictions,
  ProjectRow,
  Stats,
  SymbolClass,
  StructureResult,
  TrainingRun,
} from '../types'

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    let detail = `通信に失敗しました (${res.status})`
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
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

  bulkDelete: (ids: number[]) =>
    req<{ deleted: number[] }>('/api/projects/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    }),

  imageUrl: (id: number) => `${BASE}/api/projects/${id}/image`,

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

  runInference: (projectIds: number[], conf?: number) =>
    req<InferenceRunResponse>('/api/ml/inference/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, conf: conf ?? 0.25 }),
    }),

  runAnalysis: (projectIds: number[], conf = 0.25) =>
    req<InferenceRunResponse>('/api/ml/analysis/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_ids: projectIds, conf }),
    }),

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

  startTraining: (opts: {
    project_ids?: number[]
    only_done?: boolean
    epochs?: number
    imgsz?: number
    base_model?: string | null
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
}
