// @vitest-environment jsdom

import { MemoryRouter } from 'react-router-dom'
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AttributeDef, ProjectDetail, ReviewSummary, SymbolClass } from '../../types'
import { AttributeEditor } from './AttributeEditor'
import { CanvasControls } from './CanvasControls'
import { EditorCanvasSvg } from './EditorCanvasSvg'
import { EditorHeader } from './EditorHeader'
import { EditorToolbar } from './EditorToolbar'
import { LayerPanel } from './LayerPanel'
import { MetaPanel } from './MetaPanel'
import { ReviewSummaryCard } from './ReviewSummaryCard'
import { createSaveQueueStore } from './saveQueue'

const api = vi.hoisted(() => ({ getReviewSummary: vi.fn(), suggestSheetNumber: vi.fn() }))
vi.mock('../../api/client', () => ({ api }))

beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

describe('AttributeEditor', () => {
  const defs: AttributeDef[] = [
    { key: 'length', label: '長さ', type: 'number', unit: 'm' },
    { key: 'maker', label: 'メーカー', type: 'text' },
  ] as AttributeDef[]

  it('数値の項目は数字にし、読めない値はそのまま残す。自由な項目を書き換える', () => {
    const onChange = vi.fn()
    const view = render(<AttributeEditor defs={defs} value={{ memo: 'a', blank: null }} onChange={onChange} idPrefix="s" />)
    fireEvent.change(view.getByLabelText('長さ（m）'), { target: { value: '1,200' } })
    expect(onChange).toHaveBeenLastCalledWith({ memo: 'a', blank: null, length: 1200 })
    fireEvent.change(view.getByLabelText('長さ（m）'), { target: { value: '約3' } })
    expect(onChange).toHaveBeenLastCalledWith({ memo: 'a', blank: null, length: '約3' })
    fireEvent.change(view.getByLabelText('メーカー'), { target: { value: '東電' } })
    expect(onChange).toHaveBeenLastCalledWith({ memo: 'a', blank: null, maker: '東電' })
    expect((view.getByLabelText('blank') as HTMLInputElement).value).toBe('')
    fireEvent.change(view.getByLabelText('memo'), { target: { value: 'b' } })
    expect(onChange).toHaveBeenLastCalledWith({ memo: 'b', blank: null })
  })
})

describe('CanvasControls', () => {
  it('画面の中心で拡大・縮小し、全体表示と操作ヒント・詳細パネルを開く', () => {
    const zoomAt = vi.fn()
    const fit = vi.fn()
    const onOpen = vi.fn()
    const onToggle = vi.fn()
    const props = { mode: 'terminal' as const, inspectorOpen: false, onOpenInspector: onOpen, zoom: 1.5, zoomAt, fit, shortcutsVisible: true, onToggleShortcuts: onToggle }
    const view = render(<CanvasControls {...props} />)
    expect(view.getByText('150%')).toBeTruthy()
    expect(view.getByText('端子')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: '縮小' }))
    expect(zoomAt).toHaveBeenLastCalledWith(1 / 1.3, window.innerWidth / 2, window.innerHeight / 2)
    fireEvent.click(view.getByRole('button', { name: '拡大' }))
    expect(zoomAt).toHaveBeenLastCalledWith(1.3, window.innerWidth / 2, window.innerHeight / 2)
    fireEvent.click(view.getByRole('button', { name: /全体表示/ }))
    expect(fit).toHaveBeenCalledWith(false)
    fireEvent.click(view.getByRole('button', { name: /詳細/ }))
    expect(onOpen).toHaveBeenCalled()
    expect(view.getByText('図面操作')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /操作ヒント/ }))
    expect(onToggle).toHaveBeenCalled()
    view.rerender(<CanvasControls {...props} inspectorOpen shortcutsVisible={false} />)
    expect(view.queryByText('図面操作')).toBeNull()
    expect(view.queryByRole('button', { name: /詳細/ })).toBeNull()
  })
})

describe('EditorCanvasSvg', () => {
  it('端点が分からない配線は描かず、図面間・AI・選択中の配線を描き分ける', () => {
    const pos = (ref: string) => (ref === 'missing' ? null : { x: 10, y: 20 })
    const connections = [
      { from_symbol_ref: 'A', from_terminal_ref: null, to_symbol_ref: 'missing', to_terminal_ref: null },
      { from_symbol_ref: 'A', from_terminal_ref: null, to_symbol_ref: 'B', to_terminal_ref: null, kind: 'sheet_ref' },
      { from_symbol_ref: 'A', from_terminal_ref: null, to_symbol_ref: 'B', to_terminal_ref: null, origin: 'inference' },
      { from_symbol_ref: 'missing', from_terminal_ref: null, to_symbol_ref: 'B', to_terminal_ref: null },
      { from_symbol_ref: 'A', from_terminal_ref: null, to_symbol_ref: 'B', to_terminal_ref: null },
    ] as Parameters<typeof EditorCanvasSvg>[0]['connections']
    const view = render(
      <EditorCanvasSvg iw={100} ih={100} zoom={2} showConnections showTerminals showLabels connections={connections}
        nodePos={pos} selectedConn={4} visibleSymbols={[]} selectedRef={null} colorOf={() => '#000'} draftRect={null} draftClassKey="relay" />,
    )
    const lines = [...view.container.querySelectorAll('line')]
    expect(lines.map((l) => l.getAttribute('stroke'))).toEqual(['#db2777', '#0891b2', '#9ACA3B'])
    expect(lines.map((l) => l.getAttribute('stroke-dasharray'))).toEqual(['3 2', '2 1.5', null])
    expect([...view.container.querySelectorAll('circle')].map((c) => c.getAttribute('fill'))).toEqual(['#7c3aed', '#0891b2', '#9ACA3B'])
  })
})

describe('EditorToolbar', () => {
  const classes = Array.from({ length: 10 }, (_, i) => ({ key: `c${i}`, label: `種別${i}` })) as SymbolClass[]

  it('矢印・Home・End でモードを巡り、種別・レイヤー・履歴・詳細パネルを操作する', () => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => { cb(0); return 1 })
    const handlers = {
      onModeChange: vi.fn(), onClassKeyChange: vi.fn(), onToggleLayers: vi.fn(), onUndo: vi.fn(), onRedo: vi.fn(), onToggleInspector: vi.fn(),
    }
    const props = {
      mode: 'select' as const, classKey: 'c0', classes, colorOf: () => '#123456', layersOpen: true, hiddenAnnotationCount: 3,
      histVersion: 1, canUndo: true, canRedo: true, inspectorOpen: true, ...handlers,
    }
    const view = render(<EditorToolbar {...props} />)
    const select = view.getByRole('button', { name: '選択（Vキー）' })
    fireEvent.keyDown(select, { key: 'ArrowRight' })
    expect(handlers.onModeChange).toHaveBeenLastCalledWith('box')
    fireEvent.keyDown(select, { key: 'ArrowLeft' })
    expect(handlers.onModeChange).toHaveBeenLastCalledWith('connect')
    fireEvent.keyDown(select, { key: 'End' })
    expect(handlers.onModeChange).toHaveBeenLastCalledWith('connect')
    fireEvent.keyDown(select, { key: 'Home' })
    expect(handlers.onModeChange).toHaveBeenLastCalledWith('select')
    expect(document.activeElement?.id).toBe('annotation-mode-select')
    fireEvent.keyDown(select, { key: 'a' })
    expect(handlers.onModeChange).toHaveBeenCalledTimes(4)
    fireEvent.click(view.getByRole('button', { name: '端子（Tキー）' }))
    expect(handlers.onModeChange).toHaveBeenLastCalledWith('terminal')

    expect(view.getByRole('option', { name: '1. 種別0（c0）' })).toBeTruthy()
    expect(view.getByRole('option', { name: '種別9（c9）' })).toBeTruthy()
    fireEvent.change(view.getByRole('combobox'), { target: { value: 'c3' } })
    expect(handlers.onClassKeyChange).toHaveBeenCalledWith('c3')
    expect(view.getByTitle('表示をオフにしたアノテーション 3 件')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /レイヤー/ }))
    fireEvent.click(view.getByRole('button', { name: '元に戻す' }))
    fireEvent.click(view.getByRole('button', { name: 'やり直す' }))
    fireEvent.click(view.getByRole('button', { name: '詳細パネルを閉じる' }))
    expect(Object.values(handlers).every((fn) => fn.mock.calls.length > 0)).toBe(true)

    view.rerender(<EditorToolbar {...props} layersOpen={false} hiddenAnnotationCount={0} inspectorOpen={false} canUndo={false} />)
    expect(view.getByRole('button', { name: '詳細パネルを開く' })).toBeTruthy()
    expect((view.getByRole('button', { name: '元に戻す' }) as HTMLButtonElement).disabled).toBe(true)
    vi.restoreAllMocks()
  })
})

describe('LayerPanel', () => {
  it('種別ごと・AI 検出の表示を切り替え、編集のキーはキャンバスへ流さない', () => {
    const onSet = vi.fn()
    const onReset = vi.fn()
    const outer = vi.fn()
    const view = render(
      <div onKeyDown={outer} onPointerDown={outer} onWheel={outer}>
        <LayerPanel connectionCount={2} terminalCount={3} labelCount={1} inferenceRefs={['S1', 'S2']} hiddenInferenceCount={1}
          classLayerRows={[
            { key: 'relay', label: 'リレー', color: '#f00', total: 3, hidden: 0, refs: ['S1'] },
            { key: 'lamp', label: 'ランプ', color: '#0f0', total: 2, hidden: 2, refs: ['S3'] },
          ]}
          showConnections showTerminals={false} showLabels allLayersVisible={false}
          onToggleConnections={vi.fn()} onToggleTerminals={vi.fn()} onToggleLabels={vi.fn()}
          onSetSymbolGroupHidden={onSet} onReset={onReset} />
      </div>,
    )
    fireEvent.click(view.getByRole('button', { name: 'AI 検出をすべて表示' }))
    expect(onSet).toHaveBeenLastCalledWith(['S1', 'S2'], false)
    fireEvent.click(view.getByRole('button', { name: 'リレーを非表示' }))
    expect(onSet).toHaveBeenLastCalledWith(['S1'], true)
    fireEvent.click(view.getByRole('button', { name: 'ランプを表示' }))
    expect(onSet).toHaveBeenLastCalledWith(['S3'], false)
    expect(view.getByText('0/2')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'すべて表示' }))
    expect(onReset).toHaveBeenCalled()

    const panel = view.container.querySelector('#annotation-layers')!
    for (const key of ['Enter', 'Delete', 'Backspace', 'ArrowUp']) fireEvent.keyDown(panel, { key })
    fireEvent.pointerDown(panel)
    fireEvent.wheel(panel)
    expect(outer).not.toHaveBeenCalled()
    fireEvent.keyDown(panel, { key: 'z' })
    expect(outer).toHaveBeenCalledOnce()
  })

  it('種別も AI 検出も無ければ見出しと行を出さない', () => {
    const view = render(
      <LayerPanel connectionCount={0} terminalCount={0} labelCount={0} inferenceRefs={[]} hiddenInferenceCount={0} classLayerRows={[]}
        showConnections={false} showTerminals showLabels={false} allLayersVisible
        onToggleConnections={vi.fn()} onToggleTerminals={vi.fn()} onToggleLabels={vi.fn()} onSetSymbolGroupHidden={vi.fn()} onReset={vi.fn()} />,
    )
    expect(view.queryByText('シンボル（種別）')).toBeNull()
    expect(view.queryByText('AI 検出')).toBeNull()
    expect(view.getByRole('button', { name: '配線を表示' })).toBeTruthy()
  })
})

const summary = (patch: Partial<ReviewSummary> = {}): ReviewSummary => ({
  project_id: 1, status: 'draft', in_next_training: false, ai_detection_count: 5, region_excluded_count: 0, imported: true,
  kept: 3, relabeled: 1, deleted: 1, manual_added: 2, ...patch,
})

describe('ReviewSummaryCard', () => {
  it('確認状況・配線・コイルの取り決めを出し、通り抜ける配線を分ける', async () => {
    let finish: () => void = () => undefined
    const onSplit = vi.fn(() => new Promise<void>((resolve) => { finish = resolve }))
    api.getReviewSummary.mockResolvedValue(summary({
      in_next_training: true, region_excluded_count: 2,
      wiring: {
        terminals: { imported: false, ai_count: 4, kept: 0, deleted: 0, manual_added: 0 },
        connections: { imported: true, ai_count: 6, kept: 5, deleted: 1, manual_added: 2 },
      },
      coils: { count: 3, issues: [
        { symbol_ref: 'C1', class_key: 'relay', label: '', kind: 'through', message: '配線が通り抜けています' },
        { symbol_ref: 'C2', class_key: 'relay', label: 'X2', kind: 'terminals', message: '端子が 1 個です' },
      ] },
    } as Partial<ReviewSummary>))
    const view = render(<ReviewSummaryCard projectId={1} visible serverSynced onSplitCoilWires={onSplit} />)
    expect(await view.findByText(/AI検出 5 → 採用 3/)).toBeTruthy()
    expect(view.getByText('領域枠 2 件はシンボル対象外として除外')).toBeTruthy()
    expect(view.getByText('AI端子 4（未取り込み）')).toBeTruthy()
    expect(view.getByText('AI配線 6 → 採用 5・削除 1・手動追加 2')).toBeTruthy()
    expect(view.getByText('コイルの配線 1/3 個が取り決めどおり')).toBeTruthy()
    expect(view.getByText('C1: 配線が通り抜けています')).toBeTruthy()
    expect(view.getByText('X2: 端子が 1 個です')).toBeTruthy()
    expect(view.getByText(/次回の学習に含まれます。削除したAI検出（1件）/)).toBeTruthy()
    const split = view.getByRole('button', { name: /通り抜ける配線を分けて端子を付ける（1 個）/ })
    fireEvent.click(split)
    expect((split as HTMLButtonElement).disabled).toBe(true)
    await act(async () => finish())
    expect((split as HTMLButtonElement).disabled).toBe(false)
  })

  it('取り込み前・完了前・問題の無いコイルを出し分け、分ける機能が無ければボタンを出さない', async () => {
    api.getReviewSummary.mockResolvedValueOnce(summary({ imported: false }))
    const view = render(<ReviewSummaryCard projectId={1} visible serverSynced />)
    expect(await view.findByText('AI 検出はまだ取り込まれていません。')).toBeTruthy()
    expect(view.getByText('AI検出 5')).toBeTruthy()

    api.getReviewSummary.mockResolvedValueOnce(summary({
      coils: { count: 2, issues: [{ symbol_ref: 'C1', class_key: 'relay', label: '', kind: 'through', message: 'x' }] },
    }))
    fireEvent.click(view.getByRole('button', { name: /更新/ }))
    expect(await view.findByText(/状態を『完了』にすると次回の学習に含まれます/)).toBeTruthy()
    expect(view.queryByRole('button', { name: /通り抜ける/ })).toBeNull()

    api.getReviewSummary.mockResolvedValueOnce(summary({ coils: { count: 2, issues: [] } }))
    fireEvent.click(view.getByRole('button', { name: /更新/ }))
    expect(await view.findByText('コイルの配線 2/2 個が取り決めどおり')).toBeTruthy()
    expect(view.getByText('コイルの配線 2/2 個が取り決めどおり').className).toContain('text-slate-800')

    api.getReviewSummary.mockResolvedValueOnce(summary({ coils: { count: 0, issues: [] } }))
    fireEvent.click(view.getByRole('button', { name: /更新/ }))
    await waitFor(() => expect(view.queryByText(/コイルの配線/)).toBeNull())
  })

  it('取得の失敗を出し、遅れて返った古い応答は捨てる。表示していない・保存前は取らない', async () => {
    const hidden = render(<ReviewSummaryCard projectId={1} visible={false} serverSynced />)
    hidden.rerender(<ReviewSummaryCard projectId={1} visible serverSynced={false} />)
    expect(api.getReviewSummary).not.toHaveBeenCalled()
    expect(hidden.getByText('読み込み中…')).toBeTruthy()
    hidden.unmount()

    api.getReviewSummary.mockRejectedValueOnce(new Error('サーバーエラー')).mockRejectedValueOnce('文字列')
    const view = render(<ReviewSummaryCard projectId={1} visible serverSynced />)
    expect((await view.findByRole('alert')).textContent).toContain('サーバーエラー')
    fireEvent.click(view.getByRole('button', { name: /更新/ }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('文字列'))

    let resolveOld: (s: ReviewSummary) => void = () => undefined
    let rejectOld: (e: unknown) => void = () => undefined
    api.getReviewSummary
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
      .mockReturnValueOnce(new Promise((_, reject) => { rejectOld = reject }))
      .mockResolvedValueOnce(summary({ ai_detection_count: 9, imported: false }))
    fireEvent.click(view.getByRole('button', { name: /更新/ }))
    view.rerender(<ReviewSummaryCard projectId={2} visible serverSynced />)
    view.rerender(<ReviewSummaryCard projectId={3} visible serverSynced />)
    expect(await view.findByText('AI検出 9')).toBeTruthy()
    await act(async () => {
      resolveOld(summary({ ai_detection_count: 1, imported: false }))
      rejectOld(new Error('古い失敗'))
    })
    expect(view.getByText('AI検出 9')).toBeTruthy()
    expect(view.queryByRole('alert')).toBeNull()
  })
})

const project = {
  id: 4, name: '図面4', sheet_no: null, page_no: null, revision: null, status: 'draft', assignee: null, note: null,
  image_width: 100, image_height: 50, images: [],
} as unknown as ProjectDetail

describe('MetaPanel', () => {
  it('入力を自動保存に回し、シート番号の候補を選べる。失敗・候補なしも出す', async () => {
    api.getReviewSummary.mockResolvedValue(summary())
    api.suggestSheetNumber
      .mockResolvedValueOnce({ candidates: [{ text: 'ER1', score: 0.91, region: 'title_block' }, { text: 'ER2', score: 0.4, region: 'margin' }] })
      .mockResolvedValueOnce({ candidates: [] })
      .mockRejectedValueOnce(new Error('読めません'))
      .mockRejectedValueOnce('文字列')
    const onQueueSave = vi.fn()
    const props = { project, visible: true, saveStatus: 'saved' as const, annotationSaveStatus: 'saved' as const, onQueueSave, onSplitCoilWires: vi.fn() }
    const view = render(<MetaPanel {...props} />)
    expect(onQueueSave).toHaveBeenCalledWith(4, expect.objectContaining({ name: '図面4', sheet_no: '' }))
    expect(view.getByText(/画像: —/)).toBeTruthy()

    fireEvent.click(view.getByRole('button', { name: /AIで読取/ }))
    const er1 = await view.findByRole('button', { name: /ER1/ })
    expect(er1.getAttribute('title')).toBe('表題欄で検出（信頼度 91%）')
    expect(view.getByRole('button', { name: /ER2/ }).getAttribute('title')).toBe('下端の余白で検出（信頼度 40%）')
    fireEvent.click(er1)
    expect(er1.getAttribute('aria-pressed')).toBe('true')
    expect(onQueueSave).toHaveBeenLastCalledWith(4, expect.objectContaining({ sheet_no: 'ER1' }))

    fireEvent.click(view.getByRole('button', { name: /AIで読取/ }))
    expect(await view.findByText('候補が見つかりませんでした。手入力してください。')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: /AIで読取/ }))
    expect((await view.findByRole('alert')).textContent).toContain('読めません')
    fireEvent.click(view.getByRole('button', { name: /AIで読取/ }))
    await waitFor(() => expect(view.getByRole('alert').textContent).toContain('文字列'))

    const [, sheet] = view.getAllByRole('textbox')
    fireEvent.change(sheet, { target: { value: 'ER9' } })
    fireEvent.change(view.getByRole('combobox'), { target: { value: 'done' } })
    fireEvent.change(view.container.querySelector('textarea')!, { target: { value: 'メモ' } })
    fireEvent.change(view.getAllByRole('textbox')[0], { target: { value: '新しい名前' } })
    expect(onQueueSave).toHaveBeenLastCalledWith(4, expect.objectContaining({ name: '新しい名前', sheet_no: 'ER9', status: 'done', note: 'メモ' }))
    fireEvent.click(view.getByRole('button', { name: '図面情報は自動保存済み' }))
    expect(onQueueSave).toHaveBeenLastCalledWith(4, expect.anything(), true)

    for (const [status, label] of [['saving', '図面情報を自動保存中…'], ['pending', '図面情報に変更あり'], ['error', '図面情報の保存を再試行']] as const) {
      view.rerender(<MetaPanel {...props} saveStatus={status} />)
      expect(view.getByRole('button', { name: label })).toBeTruthy()
    }
    view.rerender(<MetaPanel {...props} project={{ ...project, images: [{ filename: 'a.png', sha256: '0123456789abcdef0123' }] } as unknown as ProjectDetail} />)
    expect(view.getByText(/画像: a\.png/)).toBeTruthy()
    expect(view.container.textContent).toContain('sha256: 0123456789abcdef…')
  })
})

describe('createSaveQueueStore', () => {
  it('送信関数は差し替えるまで何もしない', async () => {
    const store = createSaveQueueStore<number>()
    await expect(store.flush()).resolves.toBeUndefined()
    expect(store.inFlight).toBe(false)
  })
})

describe('EditorHeader', () => {
  it('シート番号が無ければ未設定と出し、前後の図面が無ければ移動のリンクを出さない', () => {
    const view = render(
      <MemoryRouter>
        <EditorHeader project={project} projectId={4} projects={[]} navigation={{ position: 0, total: 0, previousId: null, nextId: null }}
          symbolCount={1} connectionCount={2} aiApplying={false} onApplyPredictions={vi.fn()} exporting onExport={vi.fn()}
          saveStatus="saved" saveStatusLabel={{ short: '保存済', long: '保存済み' }} onSave={vi.fn()} />
      </MemoryRouter>,
    )
    expect(view.container.textContent).toContain('シート番号未設定')
    expect(view.queryByRole('link', { name: '前の図面' })).toBeNull()
    expect(view.getByRole('button', { name: '書き出し中' })).toBeTruthy()
  })
})
