import type { ProjectDetail, ProjectRow, Stats, SymbolClass } from '../types'

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
  health: () => req<{ status: string }>('/api/health'),

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
}
