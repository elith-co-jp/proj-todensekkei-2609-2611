// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ExportPage from './ExportPage'

const apiMocks = vi.hoisted(() => ({
  exportBulk: vi.fn(),
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
})
