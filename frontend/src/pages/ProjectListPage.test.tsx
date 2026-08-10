// @vitest-environment jsdom

import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import type { ProjectRow } from '../types'
import ProjectListPage from './ProjectListPage'

const apiMocks = vi.hoisted(() => ({
  bulkDelete: vi.fn(),
  createProjects: vi.fn(),
  exportBulk: vi.fn(),
  listProjects: vi.fn(),
  stats: vi.fn(),
}))

vi.mock('../api/client', () => ({ api: apiMocks }))

function projectRow(id: number, name: string): ProjectRow {
  return {
    id,
    name,
    sheet_no: null,
    page_no: null,
    revision: null,
    status: 'draft',
    assignee: null,
    image_width: 1000,
    image_height: 800,
    symbol_count: 0,
    connection_count: 0,
    terminal_count: 0,
    updated_at: null,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.stats.mockResolvedValue({
    project_count: 0,
    symbol_count: 0,
    connection_count: 0,
    by_status: {},
    by_class: {},
  })
  apiMocks.listProjects.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ProjectListPage', () => {
  it('後発検索の結果を、遅れて返った旧検索で上書きしない', async () => {
    let resolveFirstSearch: (rows: ProjectRow[]) => void = () => {
      throw new Error('先行検索が開始されていません')
    }
    let resolveLatestSearch: (rows: ProjectRow[]) => void = () => {
      throw new Error('後発検索が開始されていません')
    }
    apiMocks.listProjects.mockImplementation((query?: string) => {
      if (query === 'A') {
        return new Promise<ProjectRow[]>((resolve) => {
          resolveFirstSearch = resolve
        })
      }
      if (query === 'AB') {
        return new Promise<ProjectRow[]>((resolve) => {
          resolveLatestSearch = resolve
        })
      }
      return Promise.resolve([])
    })

    const { findAllByText, findByPlaceholderText, queryAllByText } = render(
      <MemoryRouter>
        <ProjectListPage />
      </MemoryRouter>,
    )
    const search = await findByPlaceholderText('ID・名称・シート番号で検索')

    fireEvent.change(search, { target: { value: 'A' } })
    await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalledWith('A'), { timeout: 1000 })
    fireEvent.change(search, { target: { value: 'AB' } })
    await waitFor(() => expect(apiMocks.listProjects).toHaveBeenCalledWith('AB'), { timeout: 1000 })

    resolveLatestSearch([projectRow(2, 'ABの結果')])
    expect(await findAllByText('ABの結果')).toHaveLength(2)
    resolveFirstSearch([projectRow(1, 'Aの古い結果')])
    await new Promise((resolve) => window.setTimeout(resolve, 20))

    expect(queryAllByText('Aの古い結果')).toHaveLength(0)
    expect(queryAllByText('ABの結果')).toHaveLength(2)
  })

  it('読み込み中は背面コンテンツを操作対象から外す', async () => {
    apiMocks.listProjects.mockImplementation(() => new Promise<ProjectRow[]>(() => undefined))

    const { container, findByRole } = render(
      <MemoryRouter>
        <ProjectListPage />
      </MemoryRouter>,
    )

    expect(await findByRole('status')).toBeTruthy()
    const background = container.querySelector('[inert]')
    expect(background).toBeTruthy()
    expect(background?.getAttribute('aria-hidden')).toBe('true')
  })
})
