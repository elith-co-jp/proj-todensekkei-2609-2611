import { describe, expect, it } from 'vitest'

import {
  clearPersistedProjectSave,
  createProjectScopedSave,
  enqueueProjectSave,
  getProjectNavigation,
  hasVolatileProjectSave,
  isCurrentProject,
  isEditorPath,
  loadPersistedProjectSave,
  persistProjectSave,
  projectIdAt,
  restoreFailedProjectSave,
  takeNextProjectSave,
} from './workspace'
import type { ProjectScopedSave } from './workspace'

describe('getProjectNavigation', () => {
  it('ID順で前後の図面と現在位置を返す', () => {
    const navigation = getProjectNavigation([{ id: 30 }, { id: 10 }, { id: 20 }], 20)

    expect(navigation).toEqual({
      position: 2,
      total: 3,
      previousId: 10,
      nextId: 30,
    })
  })

  it('先頭と末尾では存在しない方向をnullにする', () => {
    expect(getProjectNavigation([{ id: 1 }, { id: 2 }], 1)).toMatchObject({
      position: 1,
      previousId: null,
      nextId: 2,
    })
    expect(getProjectNavigation([{ id: 1 }, { id: 2 }], 2)).toMatchObject({
      position: 2,
      previousId: 1,
      nextId: null,
    })
  })

  it('対象が一覧にない場合も安全な初期値を返す', () => {
    expect(getProjectNavigation([{ id: 1 }], 99)).toEqual({
      position: 0,
      total: 1,
      previousId: null,
      nextId: null,
    })
  })
})

describe('projectIdAt', () => {
  it('表示位置からID順の図面IDを返し、範囲外はnullを返す', () => {
    const projects = [{ id: 30 }, { id: 10 }, { id: 20 }]

    expect(projectIdAt(projects, 1)).toBe(10)
    expect(projectIdAt(projects, 3)).toBe(30)
    expect(projectIdAt(projects, 0)).toBeNull()
    expect(projectIdAt(projects, 4)).toBeNull()
  })
})

describe('isEditorPath', () => {
  it.each(['/projects/1', '/projects/130', '/projects/1/analysis'])('%sを編集ルートとして判定する', (path) => {
    expect(isEditorPath(path)).toBe(true)
  })

  it.each(['/', '/projects', '/projects/new', '/projects/1/history'])('%sは通常画面として判定する', (path) => {
    expect(isEditorPath(path)).toBe(false)
  })
})

describe('isCurrentProject', () => {
  it('読み込み済みプロジェクトとURLのIDが一致する場合だけtrueを返す', () => {
    expect(isCurrentProject({ id: 12 }, 12)).toBe(true)
    expect(isCurrentProject({ id: 11 }, 12)).toBe(false)
    expect(isCurrentProject(null, 12)).toBe(false)
  })
})

describe('createProjectScopedSave', () => {
  it('保存対象IDと内容を同じキュー要素に固定する', () => {
    const queued = createProjectScopedSave(41, { name: 'ページ41' })

    expect(queued).toEqual({
      projectId: 41,
      key: '{"name":"ページ41"}',
      payload: { name: 'ページ41' },
    })
  })

  it('同じ内容でもページIDを混同しない', () => {
    const payload = { symbols: [], connections: [] }

    expect(createProjectScopedSave(1, payload).projectId).toBe(1)
    expect(createProjectScopedSave(2, payload).projectId).toBe(2)
  })
})

describe('project scoped save queue', () => {
  it('ページごとに最新の変更を保持して、別ページへの移動でも破棄しない', () => {
    const queue = new Map<number, ProjectScopedSave<{ revision: string }>>()
    const page1First = createProjectScopedSave(1, { revision: 'A' })
    const page1Latest = createProjectScopedSave(1, { revision: 'B' })
    const page2 = createProjectScopedSave(2, { revision: 'C' })

    enqueueProjectSave(queue, page1First)
    expect(takeNextProjectSave(queue)).toEqual(page1First)

    enqueueProjectSave(queue, page1Latest)
    enqueueProjectSave(queue, page2)

    expect(Array.from(queue.values())).toEqual([page1Latest, page2])
  })

  it('先行保存の失敗では、保存中に追加された最新の変更を上書きしない', () => {
    const queue = new Map<number, ProjectScopedSave<{ revision: string }>>()
    const failed = createProjectScopedSave(1, { revision: 'A' })
    const latest = createProjectScopedSave(1, { revision: 'B' })

    enqueueProjectSave(queue, latest)

    expect(restoreFailedProjectSave(queue, failed)).toBe(false)
    expect(takeNextProjectSave(queue)).toEqual(latest)
  })

  it('新しい変更がなければ失敗した保存を再試行用に戻す', () => {
    const queue = new Map<number, ProjectScopedSave<{ revision: string }>>()
    const failed = createProjectScopedSave(3, { revision: 'A' })

    expect(restoreFailedProjectSave(queue, failed)).toBe(true)
    expect(takeNextProjectSave(queue)).toEqual(failed)
  })
})

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()

  get length() {
    return this.values.size
  }

  clear() {
    this.values.clear()
  }

  getItem(key: string) {
    return this.values.get(key) ?? null
  }

  key(index: number) {
    return Array.from(this.values.keys())[index] ?? null
  }

  removeItem(key: string) {
    this.values.delete(key)
  }

  setItem(key: string, value: string) {
    this.values.set(key, value)
  }
}

const isNameDraft = (value: unknown): value is { name: string } =>
  typeof value === 'object' && value !== null && typeof (value as { name?: unknown }).name === 'string'

describe('project draft persistence', () => {
  it('プロジェクト別の下書きを保存し、型検証後に復元する', () => {
    const storage = new MemoryStorage()
    const save = createProjectScopedSave(51, { name: '保持する編集' })

    expect(persistProjectSave('meta', save, storage)).toBe(true)
    expect(loadPersistedProjectSave('meta', 51, isNameDraft, storage)).toEqual(save)

    clearPersistedProjectSave('meta', 51, storage)
    expect(loadPersistedProjectSave('meta', 51, isNameDraft, storage)).toBeNull()
  })

  it.each([
    '{broken-json',
    JSON.stringify({ projectId: 52, key: '{}', payload: { name: 42 } }),
    JSON.stringify({ projectId: 52, key: '{"name":"改ざん前"}', payload: { name: '改ざん後' } }),
  ])('壊れたJSON・不正shape・key不一致を破棄する', (raw) => {
    const storage = new MemoryStorage()
    storage.setItem('seq-annotator:project-draft:meta:52', raw)

    expect(loadPersistedProjectSave('meta', 52, isNameDraft, storage)).toBeNull()
    expect(storage.length).toBe(0)
  })

  it('セッション保存が拒否されても画面外メモリへ退避し、揮発状態を通知する', () => {
    const storage = new MemoryStorage()
    storage.setItem = () => {
      throw new DOMException('容量不足', 'QuotaExceededError')
    }
    const save = createProjectScopedSave(53, { name: '失ってはいけない編集' })

    expect(persistProjectSave('meta', save, storage)).toBe(false)
    expect(hasVolatileProjectSave('meta', 53)).toBe(true)
    expect(loadPersistedProjectSave('meta', 53, isNameDraft, storage)).toEqual(save)

    clearPersistedProjectSave('meta', 53, storage)
    expect(hasVolatileProjectSave('meta', 53)).toBe(false)
  })
})

describe('退避の読み出しの異常系', () => {
  const isAny = (value: unknown): value is unknown => value !== undefined

  function memoryStorage(seed: Record<string, string> = {}): Storage {
    const data = new Map(Object.entries(seed))
    return {
      get length() { return data.size },
      clear: () => data.clear(),
      getItem: (k: string) => data.get(k) ?? null,
      key: (i: number) => [...data.keys()][i] ?? null,
      removeItem: (k: string) => void data.delete(k),
      setItem: (k: string, v: string) => void data.set(k, v),
    }
  }

  it('文字列にできない中身（BigInt）の退避は読まない', () => {
    const storage = memoryStorage()
    persistProjectSave('annotations', { projectId: 61, key: '1', payload: 1n }, storage)
    expect(loadPersistedProjectSave('annotations', 61, isAny, storage)).toBeNull()
  })

  it('壊れた退避は消し、消すのも拒まれたら黙って null を返す', () => {
    const key = 'seq-annotator:project-draft:annotations:62'
    const storage = memoryStorage({ [key]: '{壊れた JSON' })
    expect(loadPersistedProjectSave('annotations', 62, isAny, storage)).toBeNull()
    expect(storage.getItem(key)).toBeNull()

    const refusing = memoryStorage({ [key]: '{壊れた JSON' })
    refusing.removeItem = () => {
      throw new Error('SecurityError')
    }
    expect(loadPersistedProjectSave('annotations', 62, isAny, refusing)).toBeNull()
  })

  it('空のキューからは null を取り出す', () => {
    expect(takeNextProjectSave(new Map())).toBeNull()
  })

  it('ページを開き直したら、sessionStorage の退避を読み戻す', () => {
    const save = createProjectScopedSave(64, { b: 2 })
    const storage = memoryStorage({ 'seq-annotator:project-draft:annotations:64': JSON.stringify(save) })
    expect(loadPersistedProjectSave('annotations', 64, isAny, storage)).toEqual(save)
  })

  it('sessionStorage が使えない環境では、画面のメモリだけに退避する', () => {
    // このテストは node で動く（window が無い）。window.sessionStorage を読むと例外になる環境と同じ
    const save = createProjectScopedSave(63, { a: 1 })
    expect(persistProjectSave('meta', save)).toBe(false)
    expect(hasVolatileProjectSave('meta', 63)).toBe(true)
    expect(loadPersistedProjectSave('meta', 63, isAny)).toEqual(save)
    clearPersistedProjectSave('meta', 63)
    expect(loadPersistedProjectSave('meta', 63, isAny)).toBeNull()
  })
})
