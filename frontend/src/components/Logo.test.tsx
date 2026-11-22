// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { AnalysisJobProgress } from './AnalysisJobProgress'
import { LoadingOverlay } from './LoadingOverlay'
import { Logo, LogoMark } from './Logo'

afterEach(cleanup)

describe('small components', () => {
  it('draws the logo with and without the word mark', () => {
    expect(render(<Logo />).container.textContent).toBe('TodenYOLO')
    cleanup()
    expect(render(<Logo showWordmark={false} size={20} className="x" />).container.textContent).toBe('')
    cleanup()
    const mark = render(<LogoMark />).container.firstElementChild as HTMLElement
    expect(mark.style.width).toBe('28px')
  })

  it('shows the default hint on the loading overlay', () => {
    const view = render(<LoadingOverlay message="読み込み中" />)
    expect(view.getByRole('status').textContent).toContain('完了までしばらくお待ちください')
    cleanup()
    expect(render(<LoadingOverlay message="m" hint="ヒント" />).getByRole('status').textContent).toContain('ヒント')
  })

  it('shows the progress of an analysis job without dividing by zero', () => {
    const job = {
      id: 1, status: 'running' as const, total: 0, done: 0, current: null, conf: 0.5, connector_conf: 0.6, results: [],
      failures: [], error: null, cancel_requested: false, detection_count: 0, started_at: null, finished_at: null,
    }
    const view = render(<AnalysisJobProgress job={job} onCancel={() => undefined} />)
    expect(view.getByRole('status').textContent).toContain('解析中 0 / 0 図面')
    expect(view.getByRole('status').textContent).not.toContain('いま:')
    expect(view.getByRole('status').textContent).not.toContain('失敗')
  })
})
