// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ExportPage from './ExportPage'

const apiMocks = vi.hoisted(() => ({
  exportAttributes: vi.fn(),
  exportBulk: vi.fn(),
  importAttributes: vi.fn(),
  importZip: vi.fn(),
  listClasses: vi.fn(),
  listProjects: vi.fn(),
}))

vi.mock('../api/client', () => ({ api: apiMocks }))

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.listProjects.mockResolvedValue([])
  apiMocks.listClasses.mockResolvedValue([])
  apiMocks.exportBulk.mockResolvedValue({ name: 'anonymous.zip', size: 1024 })
  apiMocks.importZip.mockResolvedValue({ mode: 'bundle', project_ids: [1], count: 1 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ExportPage', () => {
  it('読み込み中は背面コンテンツを操作対象から外す', async () => {
    apiMocks.listProjects.mockImplementation(() => new Promise(() => undefined))
    const { container, findByRole } = render(<ExportPage />)

    expect(await findByRole('status')).toBeTruthy()
    const background = container.querySelector('[inert]')
    expect(background).toBeTruthy()
    expect(background?.getAttribute('aria-hidden')).toBe('true')
  })

  it('出力結果をライブ実行ログへ通知する', async () => {
    const { findByRole } = render(<ExportPage />)
    const exportButton = await findByRole('button', { name: /ZIP\s*をダウンロード/ })

    fireEvent.click(exportButton)
    const log = await findByRole('log')
    await waitFor(() => expect(log.textContent).toContain('anonymous.zip'))

    expect(log.getAttribute('aria-live')).toBe('polite')
  })

  it('bundle.jsonを含まないファイルは読み込みAPIへ送らない', async () => {
    const { findByLabelText, findByRole, findByText } = render(<ExportPage />)
    const dropZone = await findByLabelText('ZIPファイルのドラッグアンドドロップ')
    const docx = new File(['not a seqanno archive'], 'sample.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })

    fireEvent.drop(dropZone, { dataTransfer: { files: [docx], items: [] } })
    const log = await findByRole('log')
    await waitFor(() => expect(log.textContent).toContain('ZIP ファイルが見つかりません'))
    expect(await findByText('読み込みできませんでした')).toBeTruthy()

    expect(apiMocks.importZip).not.toHaveBeenCalled()
  })

  it('bundle.jsonを含むZIPをドラッグ&ドロップで読み込む', async () => {
    const { findByLabelText, findByText } = render(<ExportPage />)
    const dropZone = await findByLabelText('ZIPファイルのドラッグアンドドロップ')
    const zip = new File(['header bundle.json body'], 'seqanno_export.zip', { type: 'application/zip' })

    fireEvent.drop(dropZone, { dataTransfer: { files: [zip], items: [] } })
    await waitFor(() => expect(apiMocks.importZip).toHaveBeenCalledWith(zip))
    expect(await findByText('読み込みが完了しました')).toBeTruthy()
  })

  const rows = [
    { id: 1, name: '図面1', symbol_count: 2, connection_count: 1 },
    { id: 2, name: '図面2', symbol_count: 0, connection_count: 0 },
  ]

  it('選んだ図面だけを書き出し、全選択・選択解除で対象の表示を変える', async () => {
    apiMocks.listProjects.mockResolvedValue(rows)
    const view = render(<ExportPage />)
    expect(await view.findByText('対象: 全 2 枚（未選択のため）')).toBeTruthy()
    expect(view.getByText(/対象: 全 2 枚（図面を選んでいないため）/)).toBeTruthy()
    fireEvent.click(view.getAllByRole('checkbox')[1])
    expect(view.getByText('対象: 選んだ 1 枚')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /ZIP\s*をダウンロード/ }))
    await waitFor(() => expect(apiMocks.exportBulk).toHaveBeenCalledWith({ ids: [2] }))
    expect(await view.findByText(/anonymous.zip を出力（1.0 KB／1 枚）/)).toBeTruthy()
    fireEvent.click(view.getAllByRole('checkbox')[1])
    fireEvent.click(view.getByRole('button', { name: '全選択' }))
    expect(view.getByText('対象: 選んだ 2 枚')).toBeTruthy()
    expect((view.getByRole('button', { name: '全選択' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(view.getByRole('button', { name: '選択解除' }))
    expect(view.getByText('対象: 全 2 枚（未選択のため）')).toBeTruthy()
  })

  it('一覧を読めない・書き出せないときは理由を出す', async () => {
    apiMocks.listProjects.mockRejectedValueOnce(new Error('サーバに接続できません'))
    const view = render(<ExportPage />)
    expect((await view.findByRole('alert')).textContent).toBe('サーバに接続できません')
    cleanup()
    apiMocks.listProjects.mockRejectedValueOnce('文字列')
    apiMocks.exportBulk.mockRejectedValueOnce(new Error('対象の図面が見つかりません')).mockRejectedValueOnce('x')
    const again = render(<ExportPage />)
    expect((await again.findByRole('alert')).textContent).toBe('文字列')
    expect(again.getByText('図面がありません')).toBeTruthy()
    fireEvent.click(again.getByRole('button', { name: /ZIP\s*をダウンロード/ }))
    expect(await again.findByText(/× 対象の図面が見つかりません/)).toBeTruthy()
    fireEvent.click(again.getByRole('button', { name: /ZIP\s*をダウンロード/ }))
    expect(await again.findByText(/× x$/)).toBeTruthy()
  })

  it('ファイルを選んで読み込み、ZIP でないもの・bundle.json の無いもの・失敗を知らせる', async () => {
    const view = render(<ExportPage />)
    const input = view.container.querySelector('input[type="file"][accept=".zip,application/zip"]') as HTMLInputElement
    await view.findByRole('button', { name: /ZIP を選択して読み込む/ })
    const click = vi.spyOn(input, 'click').mockImplementation(() => undefined)
    fireEvent.click(view.getByRole('button', { name: /ZIP を選択して読み込む/ }))
    expect(click).toHaveBeenCalled()

    fireEvent.change(input, { target: { files: [] } })
    fireEvent.change(input, { target: { files: [new File(['x'], 'a.txt', { type: 'text/plain' })] } })
    expect(await view.findByText('ZIP ファイルを選択してください。')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: '閉じる' }))
    expect(view.queryByText('ZIP ファイルを選択してください。')).toBeNull()

    fireEvent.change(input, { target: { files: [new File(['no bundle'], 'b.zip', { type: 'application/x-zip-compressed' })] } })
    expect(await view.findByText(/bundle.json を含む ZIP ではありません。/)).toBeTruthy()

    const broken = new File(['x'], 'c.ZIP')
    broken.arrayBuffer = () => Promise.reject(new Error('読めない'))
    fireEvent.change(input, { target: { files: [broken] } })
    await waitFor(() => expect(view.getAllByText(/bundle.json を含む ZIP ではありません/).length).toBeGreaterThan(1))

    apiMocks.importZip.mockRejectedValueOnce(new Error('壊れた ZIP です')).mockRejectedValueOnce('x')
    fireEvent.change(input, { target: { files: [new File(['bundle.json'], 'd.zip')] } })
    expect(await view.findByText('壊れた ZIP です')).toBeTruthy()
    fireEvent.change(input, { target: { files: [new File(['bundle.json'], 'd.zip')] } })
    expect(await view.findByText('読み込みに失敗しました')).toBeTruthy()
    await waitFor(() => expect(view.getByRole('log').textContent).toContain('× x'))
  })

  it('フォルダをドロップしたら中の ZIP を探し、ドラッグ中は枠を強調する', async () => {
    const view = render(<ExportPage />)
    const dropZone = await view.findByLabelText('ZIPファイルのドラッグアンドドロップ')
    const area = dropZone.parentElement as HTMLElement
    const dataTransfer = { dropEffect: '' }
    fireEvent.dragEnter(area, { dataTransfer })
    expect(dropZone.className).toContain('border-violet-400')
    fireEvent.dragOver(area, { dataTransfer })
    expect(dataTransfer.dropEffect).toBe('copy')
    // jsdom の DragEvent は relatedTarget を持てないので MouseEvent で送る
    fireEvent(area, new MouseEvent('dragleave', { bubbles: true, relatedTarget: dropZone }))
    expect(dropZone.className).toContain('border-violet-400')
    fireEvent(area, new MouseEvent('dragleave', { bubbles: true, relatedTarget: document.body }))
    expect(dropZone.className).not.toContain('border-violet-400')

    const zip = new File(['bundle.json'], 'nested.zip')
    const fileEntry = (file: File, ok = true) => ({
      isFile: true, isDirectory: false,
      file: (success: (f: File) => void, failure: () => void) => (ok ? success(file) : failure()),
    })
    const dir = (children: unknown[][], failing = false) => {
      let calls = 0
      return {
        isFile: false, isDirectory: true,
        createReader: () => ({
          readEntries: (success: (e: unknown[]) => void, failure: () => void) => {
            if (failing) return failure()
            success(children[calls++] ?? [])
          },
        }),
      }
    }
    const items = [
      { webkitGetAsEntry: () => null },
      {},
      { webkitGetAsEntry: () => ({ isFile: false, isDirectory: false }) },
      { webkitGetAsEntry: () => dir([], true) },
      { webkitGetAsEntry: () => fileEntry(new File(['x'], 'unreadable.zip'), false) },
      { webkitGetAsEntry: () => dir([[fileEntry(new File(['x'], 'readme.txt'))], [dir([[fileEntry(zip)]])]]) },
    ]
    fireEvent.drop(area, { dataTransfer: { files: [], items } })
    await waitFor(() => expect(apiMocks.importZip).toHaveBeenCalledWith(zip))

    fireEvent.drop(area, { dataTransfer: { files: [] } })
    await waitFor(() => expect(view.getByRole('log').textContent).toContain('ZIP ファイルが見つかりません'))
  })

  it('読み込み中はドロップを受け付けない', async () => {
    let finish: (value: unknown) => void = () => undefined
    apiMocks.importZip.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const view = render(<ExportPage />)
    const dropZone = await view.findByLabelText('ZIPファイルのドラッグアンドドロップ')
    const area = dropZone.parentElement as HTMLElement
    const zip = new File(['bundle.json'], 'a.zip')
    fireEvent.drop(area, { dataTransfer: { files: [zip], items: [] } })
    await waitFor(() => expect(apiMocks.importZip).toHaveBeenCalledTimes(1))
    fireEvent.dragEnter(area, { dataTransfer: {} })
    fireEvent.dragOver(area, { dataTransfer: {} })
    expect(dropZone.className).not.toContain('border-violet-400')
    fireEvent.drop(area, { dataTransfer: { files: [zip], items: [] } })
    await act(async () => finish({ mode: 'bundle', project_ids: [1], count: 1 }))
    expect(apiMocks.importZip).toHaveBeenCalledTimes(1)
  })

  it('記号・配線の属性 JSON を書き出し、取り込んだ結果と問題を出す', async () => {
    apiMocks.listProjects.mockResolvedValue(rows)
    apiMocks.exportAttributes.mockResolvedValue({ name: 'object_attributes.json', size: 10 })
    const view = render(<ExportPage />)
    await view.findByText('図面1')
    fireEvent.click(view.getByRole('button', { name: /^属性を書き出す/ }))
    await waitFor(() => expect(apiMocks.exportAttributes).toHaveBeenCalledWith([1, 2], false))
    fireEvent.click(view.getByRole('button', { name: /入力用（空欄つき）を書き出す/ }))
    await waitFor(() => expect(apiMocks.exportAttributes).toHaveBeenCalledWith([1, 2], true))

    const input = view.getByLabelText('属性 JSON のファイル') as HTMLInputElement
    const click = vi.spyOn(input, 'click').mockImplementation(() => undefined)
    fireEvent.click(view.getByRole('button', { name: /編集した JSON を取り込む/ }))
    expect(click).toHaveBeenCalled()
    apiMocks.importAttributes.mockResolvedValueOnce({
      updated: 3, unchanged: 1, duplicates: ['A/SYM-1'], not_found: ['B/SYM-2'], type_mismatch: ['C/W-1'],
      invalid: Array.from({ length: 20 }, (_, i) => `D/SYM-${i}: 値が不正`),
    })
    fireEvent.change(input, { target: { files: [new File(['{}'], 'a.json')] } })
    const report = await view.findByText(/3 件の属性を更新しました（変更なし 1 件）/)
    expect(report.textContent).toContain('A/SYM-1: ファイルの中で重複しているため取り込みませんでした')
    expect(report.textContent).toContain('B/SYM-2: 図面または記号・配線が見つかりません')
    expect(report.textContent).toContain('C/W-1: 記号と配線の種類が合いません')
    expect(report.textContent).toContain('ほか 3 件')

    apiMocks.importAttributes.mockResolvedValueOnce({ updated: 0, unchanged: 0, duplicates: [], not_found: [], type_mismatch: [], invalid: [] })
    fireEvent.change(input, { target: { files: [new File(['{}'], 'a.json')] } })
    await waitFor(() => expect(view.getByText(/0 件の属性を更新しました/).querySelector('ul')).toBeNull())

    fireEvent.change(input, { target: { files: [] } })
    apiMocks.importAttributes.mockRejectedValueOnce(new Error('JSON を読めません')).mockRejectedValueOnce('x')
    fireEvent.change(input, { target: { files: [new File(['{'], 'a.json')] } })
    expect((await view.findByRole('alert')).textContent).toBe('JSON を読めません')
    fireEvent.change(input, { target: { files: [new File(['{'], 'a.json')] } })
    await waitFor(() => expect(view.getByRole('alert').textContent).toBe('x'))
  })

  it('図面が無ければ属性の書き出しはできない', async () => {
    const view = render(<ExportPage />)
    expect(await view.findByText(/対象: 図面がありません。/)).toBeTruthy()
    expect((view.getByRole('button', { name: /^属性を書き出す/ }) as HTMLButtonElement).disabled).toBe(true)
  })
})
