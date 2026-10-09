// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'

import AnalysisPage from './AnalysisPage'

const api = vi.hoisted(() => ({
  getProject: vi.fn(), getPredictions: vi.fn(), getStructure: vi.fn(), listProjects: vi.fn(),
  imageUrl: vi.fn((id) => `/api/projects/${id}/image`), runAnalysis: vi.fn(), exportStructure: vi.fn(),
}))
vi.mock('../api/client', () => ({ api }))

const result = {
  status: 'completed', source: { project_id: 1, image_sha256: 'test' },
  image_size: { width: 1000, height: 800 },
  wires: [{ id: 'wire1', polyline: [[100, 200], [600, 200], [600, 500]] }],
  symbols: [], from_to: { connection_count: 1, connections: [] }, warnings: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  api.getProject.mockImplementation(async (id) => ({
    id, name: `図面${id}`, image_width: 1000, image_height: 800,
    symbols: [{ ref: 'MANUAL-DO-NOT-USE', cx: 0.9, cy: 0.9, w: 0.5, h: 0.5 }],
    connections: [{ from_symbol_ref: 'manual', to_symbol_ref: 'manual2' }],
  }))
  api.getPredictions.mockResolvedValue({ count: 1, detections: [{
    class_key: 'connector', class_label: 'コネクタ', cx: 0.1, cy: 0.25, w: 0.04, h: 0.06, confidence: 0.97,
  }] })
  api.getStructure.mockResolvedValue({ project_id: 1, result })
  api.listProjects.mockResolvedValue([{ id: 1 }, { id: 2 }])
  api.runAnalysis.mockResolvedValue({})
  api.exportStructure.mockResolvedValue({ name: 'structure.json' })
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function openPage() {
  const router = createMemoryRouter([
    { path: '/projects/:id/analysis', element: <AnalysisPage /> },
    { path: '/', element: <div>一覧</div> },
  ], { initialEntries: ['/projects/1/analysis'] })
  return { ...render(<RouterProvider router={router} />), router }
}

describe('AnalysisPage', () => {
  it('keeps the original visible while toggling each prediction layer independently', async () => {
    const view = openPage()
    await view.findByRole('heading', { name: '解析結果' })
    expect(view.getByRole('img', { name: '図面1' })).toBeTruthy()
    expect(view.getByTestId('symbol-layer').querySelectorAll('rect')).toHaveLength(1)
    expect(view.getByTestId('symbol-layer').querySelector('rect')?.getAttribute('x')).toBe('80')
    expect(view.getByTestId('wire-layer').querySelector('polyline')?.getAttribute('points')).toBe('100,200 600,200 600,500')
    expect(view.queryByText('MANUAL-DO-NOT-USE')).toBeNull()
    expect(view.queryByText('97%')).toBeNull()
    fireEvent.click(view.getByRole('checkbox', { name: /^シンボル/ }))
    expect(view.queryByTestId('symbol-layer')).toBeNull()
    expect(view.getByTestId('wire-layer')).toBeTruthy()
    fireEvent.click(view.getByRole('checkbox', { name: /^配線/ }))
    expect(view.queryByTestId('wire-layer')).toBeNull()
    expect(view.getByRole('img', { name: '図面1' })).toBeTruthy()
    fireEvent.click(view.getByRole('checkbox', { name: /^シンボル/ }))
    expect(view.getByTestId('symbol-layer')).toBeTruthy()
    expect(view.queryByTestId('wire-layer')).toBeNull()
  })

  it('changes only overlay opacity and downloads the structured result', async () => {
    const view = openPage()
    await view.findByRole('heading', { name: '解析結果' })
    fireEvent.change(view.getByRole('slider', { name: '配線の濃さ' }), { target: { value: '70' } })
    expect(view.getByTestId('wire-layer').getAttribute('opacity')).toBe('0.7')
    expect(view.getByRole('img').style.opacity).toBe('')
    fireEvent.click(view.getByRole('button', { name: '構造JSON' }))
    await waitFor(() => expect(api.exportStructure).toHaveBeenCalledWith([1]))
  })

  it('shows symbol-only legacy results without implying wires were analyzed', async () => {
    api.getStructure.mockResolvedValue({ project_id: 1, result: null })
    const view = openPage()
    await view.findByText('シンボルのみ解析済み')
    expect((view.getByRole('checkbox', { name: /^配線/ }) as HTMLInputElement).disabled).toBe(true)
    expect((view.getByRole('button', { name: '構造JSON' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(view.getByRole('button', { name: '解析を実行' }))
    await waitFor(() => expect(api.runAnalysis).toHaveBeenCalledWith([1]))
  })

  it('keeps saved predictions when reanalysis fails', async () => {
    api.runAnalysis.mockRejectedValue(new Error('解析に失敗しました'))
    const view = openPage()
    await view.findByRole('heading', { name: '解析結果' })
    fireEvent.click(view.getByRole('button', { name: '再解析' }))
    await view.findByRole('alert')
    expect(view.getByTestId('wire-layer')).toBeTruthy()
    expect(view.getByTestId('symbol-layer')).toBeTruthy()
  })
})
