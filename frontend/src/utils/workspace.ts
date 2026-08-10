export type ProjectNavigation = {
  position: number
  total: number
  previousId: number | null
  nextId: number | null
}

export type ProjectScopedSave<T> = {
  projectId: number
  key: string
  payload: T
}

export type ProjectSaveKind = 'annotations' | 'meta'
export type ProjectSavePayloadGuard<T> = (value: unknown) => value is T

const DRAFT_STORAGE_PREFIX = 'seq-annotator:project-draft'
const inMemoryDrafts = new Map<string, ProjectScopedSave<unknown>>()
const volatileDraftKeys = new Set<string>()

function draftStorageKey(kind: ProjectSaveKind, projectId: number): string {
  return `${DRAFT_STORAGE_PREFIX}:${kind}:${projectId}`
}

function resolveSessionStorage(storage?: Storage): Storage | null {
  if (storage) return storage
  if (typeof window === 'undefined') return null
  return window.sessionStorage
}

export function persistProjectSave<T>(
  kind: ProjectSaveKind,
  save: ProjectScopedSave<T>,
  storage?: Storage,
): boolean {
  const storageKey = draftStorageKey(kind, save.projectId)
  inMemoryDrafts.set(storageKey, save)
  try {
    const resolvedStorage = resolveSessionStorage(storage)
    if (!resolvedStorage) {
      volatileDraftKeys.add(storageKey)
      return false
    }
    resolvedStorage.setItem(storageKey, JSON.stringify(save))
    volatileDraftKeys.delete(storageKey)
    return true
  } catch {
    volatileDraftKeys.add(storageKey)
    return false
  }
}

export function loadPersistedProjectSave<T>(
  kind: ProjectSaveKind,
  projectId: number,
  isPayload: ProjectSavePayloadGuard<T>,
  storage?: Storage,
): ProjectScopedSave<T> | null {
  const storageKey = draftStorageKey(kind, projectId)
  const parseCandidate = (value: unknown): ProjectScopedSave<T> | null => {
    if (typeof value !== 'object' || value === null) return null
    const candidate = value as Record<string, unknown>
    if (candidate.projectId !== projectId || typeof candidate.key !== 'string' || !isPayload(candidate.payload)) {
      return null
    }
    try {
      if (candidate.key !== JSON.stringify(candidate.payload)) return null
    } catch {
      return null
    }
    return { projectId, key: candidate.key, payload: candidate.payload }
  }

  const memoryDraft = parseCandidate(inMemoryDrafts.get(storageKey))
  if (memoryDraft) return memoryDraft
  inMemoryDrafts.delete(storageKey)

  try {
    const resolvedStorage = resolveSessionStorage(storage)
    const raw = resolvedStorage?.getItem(storageKey)
    if (!raw) return null
    const candidate = parseCandidate(JSON.parse(raw))
    if (!candidate) {
      resolvedStorage?.removeItem(storageKey)
      return null
    }
    inMemoryDrafts.set(storageKey, candidate)
    return candidate
  } catch {
    try {
      resolveSessionStorage(storage)?.removeItem(storageKey)
    } catch {
      // 読み取り自体を拒否する環境では、画面外メモリの退避だけを使う。
    }
    return null
  }
}

export function clearPersistedProjectSave(
  kind: ProjectSaveKind,
  projectId: number,
  storage?: Storage,
): void {
  const storageKey = draftStorageKey(kind, projectId)
  inMemoryDrafts.delete(storageKey)
  volatileDraftKeys.delete(storageKey)
  try {
    resolveSessionStorage(storage)?.removeItem(storageKey)
  } catch {
    // ストレージが利用できない環境でも、API保存処理は継続する。
  }
}

export function hasVolatileProjectSave(kind: ProjectSaveKind, projectId: number): boolean {
  return volatileDraftKeys.has(draftStorageKey(kind, projectId))
}

export function createProjectScopedSave<T>(projectId: number, payload: T): ProjectScopedSave<T> {
  return { projectId, key: JSON.stringify(payload), payload }
}

export function enqueueProjectSave<T>(
  queue: Map<number, ProjectScopedSave<T>>,
  save: ProjectScopedSave<T>,
): void {
  queue.set(save.projectId, save)
}

export function takeNextProjectSave<T>(
  queue: Map<number, ProjectScopedSave<T>>,
): ProjectScopedSave<T> | null {
  const next = queue.values().next().value ?? null
  if (next) queue.delete(next.projectId)
  return next
}

/**
 * 失敗した保存を再試行キューへ戻す。保存中に同じページの新しい変更が
 * 追加されている場合は、古い内容で上書きせず最新の変更を優先する。
 */
export function restoreFailedProjectSave<T>(
  queue: Map<number, ProjectScopedSave<T>>,
  failed: ProjectScopedSave<T>,
): boolean {
  if (queue.has(failed.projectId)) return false
  queue.set(failed.projectId, failed)
  return true
}

export function getProjectNavigation(projects: ReadonlyArray<{ id: number }>, currentId: number): ProjectNavigation {
  const sortedIds = projects.map(({ id }) => id).sort((left, right) => left - right)
  const currentIndex = sortedIds.indexOf(currentId)

  if (currentIndex < 0) {
    return {
      position: 0,
      total: sortedIds.length,
      previousId: null,
      nextId: null,
    }
  }

  return {
    position: currentIndex + 1,
    total: sortedIds.length,
    previousId: sortedIds[currentIndex - 1] ?? null,
    nextId: sortedIds[currentIndex + 1] ?? null,
  }
}

export function isEditorPath(pathname: string): boolean {
  return /^\/projects\/\d+$/.test(pathname)
}

export function isCurrentProject(project: { id: number } | null, routeProjectId: number): boolean {
  return project?.id === routeProjectId
}
