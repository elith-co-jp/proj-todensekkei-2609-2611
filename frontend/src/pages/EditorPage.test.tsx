// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'

import type { ProjectDetail, ProjectRow, SymbolClass } from '../types'
import { clearPersistedProjectSave } from '../utils/workspace'
import EditorPage from './EditorPage'

const apiMocks = vi.hoisted(() => ({
  exportProject: vi.fn(),
  getProject: vi.fn(),
  imageUrl: vi.fn((id: number) => `/api/projects/${id}/image`),
  listClasses: vi.fn(),
  listProjects: vi.fn(),
  saveAnnotations: vi.fn(),
  updateMeta: vi.fn(),
}))

vi.mock('../api/client', () => ({ api: apiMocks }))

const symbolClass: SymbolClass = {
  id: 1,
  key: 'relay',
  label: 'リレー',
  yolo_index: 0,
  color: '#0891b2',
  is_active: true,
  sort_order: 0,
}

function project(id: number): ProjectDetail {
  return {
    id,
    name: `匿名ページ${id}`,
    sheet_no: String(id),
    page_no: String(id),
    revision: null,
    source_file: null,
    image_width: 1000,
    image_height: 800,
    status: 'draft',
    assignee: null,
    note: null,
    symbols: [
      {
        ref: 'SYM-0001',
        class_key: 'relay',
        label: null,
        cx: 0.3,
        cy: 0.4,
        w: 0.12,
        h: 0.08,
        note: null,
        terminals: [],
      },
      {
        ref: 'SYM-0002',
        class_key: 'relay',
        label: null,
        cx: 0.7,
        cy: 0.4,
        w: 0.12,
        h: 0.08,
        note: null,
        terminals: [],
      },
    ],
    connections: [],
    images: [{ filename: 'anonymous.png', sha256: 'demo', width: 1000, height: 800 }],
  }
}

function row(id: number): ProjectRow {
  return {
    id,
    name: `匿名ページ${id}`,
    sheet_no: String(id),
    page_no: String(id),
    revision: null,
    status: 'draft',
    assignee: null,
    image_width: 1000,
    image_height: 800,
    symbol_count: 2,
    connection_count: 0,
    terminal_count: 0,
    updated_at: null,
  }
}

function renderEditor() {
  const router = createMemoryRouter(
    [
      { path: '/', element: <div>図面一覧</div> },
      { path: '/projects/:id', element: <EditorPage /> },
    ],
    { initialEntries: ['/projects/1'] },
  )
  return { ...render(<RouterProvider router={router} />), router }
}

beforeEach(() => {
  vi.clearAllMocks()
  window.sessionStorage.clear()
  for (const projectId of [1, 2]) {
    clearPersistedProjectSave('annotations', projectId)
    clearPersistedProjectSave('meta', projectId)
  }
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({
      matches: true,
      media: '',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    callback(0)
    return 1
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 800,
    height: 800,
    left: 0,
    right: 1000,
    top: 0,
    width: 1000,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  })
  apiMocks.listClasses.mockResolvedValue([symbolClass])
  apiMocks.listProjects.mockResolvedValue([row(1), row(2)])
  apiMocks.getProject.mockImplementation(async (id: number) => project(id))
  apiMocks.saveAnnotations.mockResolvedValue({ symbol_count: 2, connection_count: 0, skipped: [] })
  apiMocks.updateMeta.mockResolvedValue({ id: 1, status: 'draft' })
  apiMocks.exportProject.mockResolvedValue({ name: 'anonymous.zip', size: 10 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('EditorPage interactions', () => {
  it('キーボードで矩形・端子・配線を作成できる', async () => {
    const { container, findByRole, getByRole } = renderEditor()
    const canvas = await findByRole('region', { name: '図面アノテーションキャンバス' })

    fireEvent.keyDown(canvas, { key: 'Enter' })
    expect(getByRole('button', { name: /^SYM-0003リレー$/ })).toBeTruthy()

    fireEvent.click(getByRole('button', { name: /^SYM-0001リレー$/ }))
    fireEvent.keyDown(window, { key: 't' })
    fireEvent.keyDown(canvas, { key: 'Enter' })
    expect(container.querySelector('input[value="1"]')).toBeTruthy()

    fireEvent.keyDown(window, { key: 'c' })
    fireEvent.keyDown(canvas, { key: 'Enter' })
    fireEvent.click(getByRole('button', { name: /^SYM-0002リレー$/ }))
    fireEvent.keyDown(canvas, { key: 'Enter' })
    fireEvent.click(getByRole('tab', { name: /^配線/ }))

    expect(getByRole('button', { name: 'SYM-0001→SYM-0002' })).toBeTruthy()
    expect(getByRole('button', { name: '選択・移動（Vキー）' })).toBeTruthy()
  })

  it('Escでポインターを解放し、次の描画を開始できる', async () => {
    const capturedPointers = new Set<number>()
    const setPointerCapture = vi.fn((pointerId: number) => capturedPointers.add(pointerId))
    const releasePointerCapture = vi.fn((pointerId: number) => capturedPointers.delete(pointerId))
    Object.defineProperties(HTMLElement.prototype, {
      setPointerCapture: { configurable: true, value: setPointerCapture },
      releasePointerCapture: { configurable: true, value: releasePointerCapture },
      hasPointerCapture: { configurable: true, value: (pointerId: number) => capturedPointers.has(pointerId) },
    })
    const { findByRole } = renderEditor()
    const canvas = await findByRole('region', { name: '図面アノテーションキャンバス' })

    fireEvent.pointerDown(canvas, { pointerId: 7, button: 0, clientX: 100, clientY: 100 })
    fireEvent.keyDown(window, { key: 'Escape' })
    fireEvent.pointerDown(canvas, { pointerId: 8, button: 0, clientX: 200, clientY: 200 })

    expect(releasePointerCapture).toHaveBeenCalledWith(7)
    expect(setPointerCapture).toHaveBeenCalledTimes(2)
  })

  it('保存中の追加編集をページ移動後も同じページIDで保存する', async () => {
    type SaveResult = { symbol_count: number; connection_count: number; skipped: string[] }
    let resolveFirstSave: (value: SaveResult) => void = () => {
      throw new Error('保存リクエストが開始されていません')
    }
    apiMocks.saveAnnotations
      .mockImplementationOnce(
        () =>
          new Promise<SaveResult>((resolve) => {
            resolveFirstSave = resolve
          }),
      )
      .mockResolvedValue({ symbol_count: 4, connection_count: 0, skipped: [] })

    const { findByRole, getByRole } = renderEditor()
    const canvas = await findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.keyDown(canvas, { key: 'Enter' })
    const saveButton = await findByRole('button', { name: '変更を検出' })
    fireEvent.click(saveButton)
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))

    fireEvent.click(getByRole('link', { name: '次の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenCalledWith(2))
    fireEvent.click(await findByRole('link', { name: '前の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenLastCalledWith(1))
    const restoredCanvas = await findByRole('region', { name: '図面アノテーションキャンバス' })
    expect(getByRole('button', { name: /^SYM-0003リレー$/ })).toBeTruthy()
    fireEvent.keyDown(restoredCanvas, { key: 'Enter' })

    resolveFirstSave({ symbol_count: 3, connection_count: 0, skipped: [] })
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(2))

    expect(apiMocks.saveAnnotations.mock.calls[1][0]).toBe(1)
    expect(apiMocks.saveAnnotations.mock.calls[1][1].symbols).toHaveLength(4)
  })

  it('複数ページの保存失敗を各世代1回で停止し、APIを連打しない', async () => {
    let rejectFirstSave: (reason: Error) => void = () => {
      throw new Error('保存リクエストが開始されていません')
    }
    apiMocks.saveAnnotations
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectFirstSave = reject
          }),
      )
      .mockRejectedValue(new Error('一時的な通信障害'))

    const { findByRole, getByRole } = renderEditor()
    const firstCanvas = await findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.keyDown(firstCanvas, { key: 'Enter' })
    fireEvent.click(await findByRole('button', { name: '変更を検出' }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))

    fireEvent.keyDown(firstCanvas, { key: 'Enter' })
    fireEvent.click(getByRole('link', { name: '次の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenCalledWith(2))
    const secondCanvas = await findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.keyDown(secondCanvas, { key: 'Enter' })
    fireEvent.click(await findByRole('button', { name: '変更を検出' }))

    rejectFirstSave(new Error('一時的な通信障害'))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(3))
    await new Promise((resolve) => window.setTimeout(resolve, 20))

    expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(3)
  })

  it('保存中に開始した古いGETを補完し、保存完了後に開始したGETはサーバーを正とする', async () => {
    type SaveResult = { symbol_count: number; connection_count: number; skipped: string[] }
    let resolveSave: (value: SaveResult) => void = () => {
      throw new Error('保存リクエストが開始されていません')
    }
    let resolveStaleGet: (value: ProjectDetail) => void = () => {
      throw new Error('図面取得リクエストが開始されていません')
    }
    apiMocks.saveAnnotations.mockImplementationOnce(
      () =>
        new Promise<SaveResult>((resolve) => {
          resolveSave = resolve
        }),
    )

    const { findByRole, getByRole, queryByRole } = renderEditor()
    const canvas = await findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.keyDown(canvas, { key: 'Enter' })
    fireEvent.click(await findByRole('button', { name: '変更を検出' }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))

    fireEvent.click(getByRole('link', { name: '次の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenLastCalledWith(2))
    apiMocks.getProject.mockImplementationOnce(
      () =>
        new Promise<ProjectDetail>((resolve) => {
          resolveStaleGet = resolve
        }),
    )
    fireEvent.click(await findByRole('link', { name: '前の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenCalledTimes(3))
    resolveSave({ symbol_count: 3, connection_count: 0, skipped: [] })
    resolveStaleGet(project(1))
    expect(await findByRole('button', { name: /^SYM-0003リレー$/ })).toBeTruthy()

    fireEvent.click(getByRole('link', { name: '次の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenLastCalledWith(2))
    fireEvent.click(await findByRole('link', { name: '前の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenCalledTimes(5))
    await findByRole('region', { name: '図面アノテーションキャンバス' })

    expect(queryByRole('button', { name: /^SYM-0003リレー$/ })).toBeNull()
  })

  it('アノテーション保存中に既知の状態へ戻しても後続保存で反映する', async () => {
    type SaveResult = { symbol_count: number; connection_count: number; skipped: string[] }
    let resolveFirstSave: (value: SaveResult) => void = () => {
      throw new Error('保存リクエストが開始されていません')
    }
    apiMocks.saveAnnotations.mockImplementationOnce(
      () =>
        new Promise<SaveResult>((resolve) => {
          resolveFirstSave = resolve
        }),
    )

    const { findByRole, queryByRole } = renderEditor()
    const canvas = await findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.keyDown(canvas, { key: 'Enter' })
    fireEvent.click(await findByRole('button', { name: '変更を検出' }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))

    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    await waitFor(() => expect(queryByRole('button', { name: /^SYM-0003リレー$/ })).toBeNull())
    resolveFirstSave({ symbol_count: 3, connection_count: 0, skipped: [] })
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(2))

    expect(apiMocks.saveAnnotations.mock.calls[1][1].symbols).toHaveLength(2)
  })

  it('ページ移動後に失敗した図面情報を保持し、戻ったページから再試行できる', async () => {
    let rejectFirstMetaSave: (reason: Error) => void = () => {
      throw new Error('図面情報の保存リクエストが開始されていません')
    }
    apiMocks.updateMeta
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectFirstMetaSave = reject
          }),
      )
      .mockResolvedValue({ id: 1, status: 'draft' })

    const { findByRole, findByText, getByRole } = renderEditor()
    fireEvent.click(await findByRole('tab', { name: '図面情報' }))
    const nameInput = getByRole('textbox', { name: '名称' })
    fireEvent.change(nameInput, { target: { value: '保持される編集' } })
    fireEvent.click(await findByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))

    fireEvent.click(getByRole('link', { name: '次の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenCalledWith(2))
    rejectFirstMetaSave(new Error('一時的な通信障害'))
    await findByText('ページ 1 の図面情報を保存できませんでした')
    fireEvent.click(await findByRole('link', { name: '前の図面' }))
    await waitFor(() => expect(apiMocks.getProject).toHaveBeenLastCalledWith(1))
    fireEvent.click(await findByRole('tab', { name: '図面情報' }))

    expect((getByRole('textbox', { name: '名称' }) as HTMLInputElement).value).toBe('保持される編集')
    fireEvent.click(getByRole('button', { name: /図面情報(に変更あり|の保存を再試行)/ }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(2))
    expect(apiMocks.updateMeta.mock.calls[1][0]).toBe(1)
    expect(apiMocks.updateMeta.mock.calls[1][1].name).toBe('保持される編集')
  })

  it('図面情報の保存中に既知の値へ戻しても後続保存で反映する', async () => {
    let resolveFirstMetaSave: (value: { id: number; status: string }) => void = () => {
      throw new Error('図面情報の保存リクエストが開始されていません')
    }
    apiMocks.updateMeta.mockImplementationOnce(
      () =>
        new Promise<{ id: number; status: string }>((resolve) => {
          resolveFirstMetaSave = resolve
        }),
    )

    const { findByRole, getByRole } = renderEditor()
    fireEvent.click(await findByRole('tab', { name: '図面情報' }))
    const nameInput = getByRole('textbox', { name: '名称' })
    fireEvent.change(nameInput, { target: { value: '保存中の変更' } })
    fireEvent.click(await findByRole('button', { name: '図面情報に変更あり' }))
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(1))

    fireEvent.change(getByRole('textbox', { name: '名称' }), { target: { value: '匿名ページ1' } })
    await waitFor(() =>
      expect((getByRole('textbox', { name: '名称' }) as HTMLInputElement).value).toBe('匿名ページ1'),
    )
    resolveFirstMetaSave({ id: 1, status: 'draft' })
    await waitFor(() => expect(apiMocks.updateMeta).toHaveBeenCalledTimes(2))

    expect(apiMocks.updateMeta.mock.calls[1][1].name).toBe('匿名ページ1')
  })

  it('個別出力の失敗を画面に通知する', async () => {
    apiMocks.exportProject.mockRejectedValueOnce(new Error('出力できませんでした'))
    const { findByRole, getAllByRole } = renderEditor()
    await findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.click(getAllByRole('button', { name: '詳細パネルを閉じる' })[0])
    const exportButton = await findByRole('button', { name: 'この図面を出力' })

    fireEvent.click(exportButton)

    expect((await findByRole('alert')).textContent).toContain('出力できませんでした')
  })

  it('保存失敗後にエディタを再生成しても下書きを復元して再試行できる', async () => {
    let rejectSave: (reason: Error) => void = () => {
      throw new Error('保存リクエストが開始されていません')
    }
    apiMocks.saveAnnotations.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectSave = reject
        }),
    )

    const firstRender = renderEditor()
    const canvas = await firstRender.findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.keyDown(canvas, { key: 'Enter' })
    fireEvent.click(await firstRender.findByRole('button', { name: '変更を検出' }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))
    firstRender.unmount()

    await act(async () => {
      rejectSave(new Error('一時的な通信障害'))
      await Promise.resolve()
    })

    const secondRender = renderEditor()
    expect(await secondRender.findByRole('button', { name: /^SYM-0003リレー$/ })).toBeTruthy()
    fireEvent.click(
      await secondRender.findByRole('button', {
        name: /(変更を検出|保存に失敗しました。押すと再試行します)/,
      }),
    )
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(2))

    expect(apiMocks.saveAnnotations.mock.calls[1][0]).toBe(1)
    expect(apiMocks.saveAnnotations.mock.calls[1][1].symbols).toHaveLength(3)
  })

  it('セッション保存が拒否された場合はAPI保存完了まで画面遷移を止める', async () => {
    type SaveResult = { symbol_count: number; connection_count: number; skipped: string[] }
    let resolveSave: (value: SaveResult) => void = () => {
      throw new Error('保存リクエストが開始されていません')
    }
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('容量不足', 'QuotaExceededError')
    })
    apiMocks.saveAnnotations.mockImplementationOnce(
      () =>
        new Promise<SaveResult>((resolve) => {
          resolveSave = resolve
        }),
    )

    const { findByRole, findByText, getByRole, queryByText } = renderEditor()
    const canvas = await findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.keyDown(canvas, { key: 'Enter' })
    const warning = await findByRole('alert')
    expect(warning.textContent).toContain('サーバー保存が完了するまで')
    fireEvent.click(await findByRole('button', { name: '変更を検出' }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))

    fireEvent.click(getByRole('link', { name: '図面一覧に戻る' }))
    expect(queryByText('図面一覧')).toBeNull()

    await act(async () => {
      resolveSave({ symbol_count: 3, connection_count: 0, skipped: [] })
      await Promise.resolve()
    })
    expect(await findByText('図面一覧')).toBeTruthy()
  })

  it('保存中payloadへ戻した場合は重複送信せず揮発下書きを解放する', async () => {
    type SaveResult = { symbol_count: number; connection_count: number; skipped: string[] }
    let resolveSave: (value: SaveResult) => void = () => {
      throw new Error('保存リクエストが開始されていません')
    }
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('容量不足', 'QuotaExceededError')
    })
    apiMocks.saveAnnotations.mockImplementationOnce(
      () =>
        new Promise<SaveResult>((resolve) => {
          resolveSave = resolve
        }),
    )

    const { findByRole, findByText, getByRole, queryByText } = renderEditor()
    const canvas = await findByRole('region', { name: '図面アノテーションキャンバス' })
    fireEvent.keyDown(canvas, { key: 'Enter' })
    fireEvent.click(await findByRole('button', { name: '変更を検出' }))
    await waitFor(() => expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1))

    fireEvent.keyDown(canvas, { key: 'Enter' })
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    fireEvent.click(getByRole('link', { name: '図面一覧に戻る' }))
    expect(queryByText('図面一覧')).toBeNull()

    await act(async () => {
      resolveSave({ symbol_count: 3, connection_count: 0, skipped: [] })
      await Promise.resolve()
    })
    expect(await findByText('図面一覧')).toBeTruthy()
    expect(apiMocks.saveAnnotations).toHaveBeenCalledTimes(1)
  })
})
