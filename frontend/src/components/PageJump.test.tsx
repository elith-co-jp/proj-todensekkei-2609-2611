// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { PageJump } from './PageJump'

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>
}

function renderJump(currentId: number, projects = [{ id: 30 }, { id: 10 }, { id: 20 }], tone: 'dark' | 'light' = 'light') {
  return render(
    <MemoryRouter initialEntries={['/start']}>
      <PageJump projects={projects} currentId={currentId} pathFor={(id) => `/projects/${id}`} tone={tone} />
      <Routes><Route path="*" element={<Where />} /></Routes>
    </MemoryRouter>,
  )
}

afterEach(cleanup)

describe('PageJump', () => {
  it('番号（全角も可）を入れて Enter で、その位置の図面へ移る', () => {
    const view = renderJump(10)
    const input = view.getByRole('textbox', { name: '図面番号を入力して移動' }) as HTMLInputElement
    expect(input.value).toBe('1')
    expect(input.title).toBe('1〜3 の番号を入力して Enter で移動')
    expect(view.getByText('/ 3')).toBeTruthy()
    fireEvent.focus(input)
    input.value = '３'
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.blur(input)
    expect(view.getByTestId('where').textContent).toBe('/projects/30')
  })

  it('範囲外・数字以外・今の番号・Escape では元の番号に戻す', () => {
    const view = renderJump(20, undefined, 'dark')
    const input = view.getByRole('textbox') as HTMLInputElement
    for (const value of ['9', 'abc', '2']) {
      input.value = value
      fireEvent.blur(input)
      expect(input.value).toBe('2')
    }
    input.value = '1'
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input.value).toBe('2')
    fireEvent.keyDown(input, { key: 'a' })
    expect(view.getByTestId('where').textContent).toBe('/start')
  })

  it('図面が一覧に無いときは番号を空にし、総数も — にする', () => {
    const view = renderJump(99, [])
    const input = view.getByRole('textbox') as HTMLInputElement
    expect(input.value).toBe('')
    expect(input.title).toBe('')
    expect(view.getByText('/ —')).toBeTruthy()
  })
})
