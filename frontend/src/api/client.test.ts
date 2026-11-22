// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './client'

type Call = [string, RequestInit | undefined]

const fetchMock = vi.fn()

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
}

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async () => jsonResponse({ ok: true }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const file = new File(['x'], 'a.pdf')

// [関数名, 引数, URL, メソッド, 本文（JSON のとき）]
const JSON_CALLS: [keyof typeof api, unknown[], string, string | undefined, unknown?][] = [
  ['health', [], '/api/health', undefined],
  ['desktopHeartbeat', [], '/api/desktop/heartbeat', 'POST'],
  ['shutdownDesktop', [], '/api/desktop/shutdown', 'POST'],
  ['listClasses', [], '/api/classes', undefined],
  ['listProjects', [], '/api/projects', undefined],
  ['listProjects', ['ER 1/2'], '/api/projects?q=ER%201%2F2', undefined],
  ['getProject', [3], '/api/projects/3', undefined],
  ['saveAnnotations', [3, { symbols: [] }], '/api/projects/3/annotations', 'PUT', { symbols: [] }],
  ['updateMeta', [3, { status: 'done' }], '/api/projects/3/meta', 'PUT', { status: 'done' }],
  ['deleteProject', [3], '/api/projects/3', 'DELETE'],
  ['suggestSheetNumber', [3], '/api/projects/3/sheet-number/suggest', 'POST'],
  ['bulkDelete', [[1, 2]], '/api/projects/bulk-delete', 'POST', { ids: [1, 2] }],
  ['stats', [], '/api/stats', undefined],
  ['mlStatus', [], '/api/ml/status', undefined],
  ['listModels', [], '/api/ml/models', undefined],
  ['activateModel', [4], '/api/ml/models/4/activate', 'POST'],
  ['deleteModel', [4], '/api/ml/models/4', 'DELETE'],
  ['startAnalysisJob', [[1]], '/api/ml/analysis/jobs', 'POST', { project_ids: [1], conf: 0.5, connector_conf: 0.6 }],
  ['startAnalysisJob', [[1], 0.3, null], '/api/ml/analysis/jobs', 'POST', { project_ids: [1], conf: 0.3, connector_conf: null }],
  ['getAnalysisJob', [7], '/api/ml/analysis/jobs/7', undefined],
  ['listAnalysisJobs', [], '/api/ml/analysis/jobs', undefined],
  ['cancelAnalysisJob', [7], '/api/ml/analysis/jobs/7/cancel', 'POST'],
  ['listDrawingSets', [], '/api/drawing-sets', undefined],
  ['createDrawingSet', ['A', [1]], '/api/drawing-sets', 'POST', { name: 'A', project_ids: [1] }],
  ['updateDrawingSet', [2, { note: 'n' }], '/api/drawing-sets/2', 'PUT', { note: 'n' }],
  ['drawingSetCoverage', [2], '/api/drawing-sets/2/coverage', undefined],
  ['suggestDrawingSets', [], '/api/drawing-sets/suggest', undefined],
  ['getStructure', [5], '/api/ml/projects/5/structure', undefined],
  ['listPredictions', [], '/api/ml/predictions', undefined],
  ['getPredictions', [5], '/api/ml/projects/5/predictions', undefined],
  ['getReviewSummary', [5], '/api/ml/projects/5/review-summary', undefined],
  ['aiImport', [5, { symbols: [] }], '/api/ml/projects/5/ai-import', 'POST', { symbols: [] }],
  ['startTraining', [{ epochs: 3 }], '/api/ml/training/run', 'POST', { epochs: 3 }],
  ['listTrainingRuns', [], '/api/ml/training/runs', undefined],
  ['getTrainingRun', [8], '/api/ml/training/runs/8', undefined],
  ['decideTrainingRun', [8, 'adopt'], '/api/ml/training/runs/8/decision', 'POST', { decision: 'adopt' }],
  ['trainingClassStats', [], '/api/ml/training/class-stats', undefined],
  ['trainingHardDrawings', [], '/api/ml/training/hard-drawings', undefined],
  ['buildCircuit', [[1]], '/api/circuit/build', 'POST', { project_ids: [1] }],
  ['simulateCircuit', [[1], { PB: true }, {}], '/api/circuit/simulate', 'POST', { project_ids: [1], inputs: { PB: true }, energized: {} }],
  ['circuitWhatIf', [[1], {}, {}, { open: [] }], '/api/circuit/what-if', 'POST', { project_ids: [1], inputs: {}, energized: {}, change: { open: [] } }],
  ['circuitTimeline', [[1], [], {}, 9], '/api/circuit/timeline', 'POST', { project_ids: [1], events: [], delays: {}, until: 9 }],
  ['circuitImpact', [[1], 'R1'], '/api/circuit/impact', 'POST', { project_ids: [1], device: 'R1' }],
  ['circuitEditTools', [], '/api/circuit/edit/tools', undefined],
  ['circuitEditPreview', [[1], []], '/api/circuit/edit/preview', 'POST', { project_ids: [1], ops: [] }],
  ['circuitEditApply', [[1], [], 't', null, 'h'], '/api/circuit/edit/apply', 'POST',
    { project_ids: [1], ops: [], title: 't', reason: null, author: 'user', base_hash: 'h' }],
  ['circuitEditHistory', [[1, 2]], '/api/circuit/edit/history?project_ids=1,2', undefined],
  ['circuitEditUndo', [6], '/api/circuit/edit/6/undo', 'POST', { force: false }],
  ['circuitEditUndo', [6, true], '/api/circuit/edit/6/undo', 'POST', { force: true }],
  ['deviceIndex', [[1]], '/api/circuit/devices', 'POST', { project_ids: [1] }],
  ['traceTargets', [[1]], '/api/circuit/trace/targets', 'POST', { project_ids: [1] }],
  ['traceBackward', [[1], 'R1', { PB: null }], '/api/circuit/trace', 'POST',
    { project_ids: [1], mode: 'backward', target: 'R1', states: { PB: null }, energized: {} }],
  ['traceForward', [[1], 'PB'], '/api/circuit/trace', 'POST', { project_ids: [1], mode: 'forward', device: 'PB', states: {} }],
  ['traceForward', [[1], 'PB', { PB: true }], '/api/circuit/trace', 'POST', { project_ids: [1], mode: 'forward', device: 'PB', states: { PB: true } }],
  ['checkConsistency', [[1]], '/api/circuit/consistency', 'POST', { project_ids: [1] }],
  ['applyConsistencyFixes', [[1], ['x']], '/api/circuit/consistency/apply', 'POST', { project_ids: [1], issue_ids: ['x'] }],
  ['splitCoilWires', [{ symbols: [], connections: [] }, 10, 20], '/api/circuit/split-coil-wires', 'POST',
    { symbols: [], connections: [], image_width: 10, image_height: 20 }],
  ['attributeDefinitions', [], '/api/attributes/definitions', undefined],
]

describe('api client', () => {
  it.each(JSON_CALLS)('%s(%j) → %s', async (name, args, url, method, body) => {
    await (api[name] as (...a: unknown[]) => Promise<unknown>)(...args)
    const [calledUrl, init] = fetchMock.mock.calls[0] as Call
    expect(calledUrl).toBe(url)
    expect(init?.method).toBe(method)
    if (body !== undefined) expect(JSON.parse(String(init?.body))).toEqual(body)
  })

  it.each([
    ['createProjects', [[file, file]], '/api/projects', 'files'],
    ['importZip', [file], '/api/import', 'archive'],
    ['uploadModel', [file], '/api/ml/models', 'file'],
    ['importPredictionsZip', [file], '/api/ml/inference/import', 'archive'],
    ['importAttributes', [file], '/api/attributes/import', 'file'],
  ] as const)('%s sends a multipart form', async (name, args, url, field) => {
    await (api[name] as (...a: unknown[]) => Promise<unknown>)(...args)
    const [calledUrl, init] = fetchMock.mock.calls[0] as Call
    expect(calledUrl).toBe(url)
    expect(init?.method).toBe('POST')
    expect((init?.body as FormData).getAll(field).length).toBe(Array.isArray(args[0]) ? 2 : 1)
  })

  it('builds plain URLs for images and model downloads', () => {
    expect(api.imageUrl(3)).toBe('/api/projects/3/image')
    expect(api.thumbnailUrl(3)).toBe('/api/projects/3/thumbnail')
    expect(api.modelDownloadUrl(4)).toBe('/api/ml/models/4/download')
  })

  it('returns undefined for 204 and deletes drawing sets', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await expect(api.deleteDrawingSet(2)).resolves.toBeUndefined()
    expect(fetchMock.mock.calls[0]).toEqual(['/api/drawing-sets/2', { method: 'DELETE' }])
  })

  it.each([
    [{ detail: '日本語の理由' }, '日本語の理由'],
    [{ detail: { message: '入れ子の理由' } }, '入れ子の理由'],
    [{ detail: [{ loc: ['body'], msg: 'bad' }] }, '[{"loc":["body"],"msg":"bad"}]'],
    [{}, '通信に失敗しました (400)'],
  ])('turns error bodies into messages (%j)', async (body, message) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(body, 400))
    await expect(api.stats()).rejects.toThrow(message)
  })

  it('keeps the status when the error body is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response('<html>', { status: 502 }))
    await expect(api.stats()).rejects.toThrow('通信に失敗しました (502)')
  })

  describe('downloads', () => {
    let clicked: { href: string; download: string }[]
    beforeEach(() => {
      clicked = []
      vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        clicked.push({ href: this.href, download: this.download })
      })
    })

    it.each([
      ['exportProject', [3], '/api/projects/3/export', 'seqanno_project_3.zip'],
      ['exportBulk', [{ ids: [1] }], '/api/export', 'seqanno_export.zip'],
      ['exportStructure', [[1]], '/api/ml/analysis/export', 'structure.json'],
      ['downloadNetlist', [[1], {}], '/api/circuit/netlist', 'circuit.cir'],
      ['downloadCircuitCode', [[1]], '/api/circuit/code', 'circuit_code.py'],
      ['downloadDeviceIndex', [[1, 2]], '/api/circuit/devices.xlsx?project_ids=1,2', 'device_index.xlsx'],
      ['downloadAnnotationTable', [[1]], '/api/exchange/table?project_ids=1&drawing_set=', 'annotation_table.xlsx'],
      ['downloadAnnotationTable', [[1], 'A 1'], '/api/exchange/table?project_ids=1&drawing_set=A%201', 'annotation_table.xlsx'],
      ['downloadCvat', [[1]], '/api/exchange/cvat?project_ids=1', 'annotations.xml'],
      ['exportAttributes', [[1], true], '/api/attributes/export', 'object_attributes_template.json'],
      ['exportAttributes', [[1], false], '/api/attributes/export', 'object_attributes.json'],
    ] as const)('%s saves the file with the fallback name', async (name, args, url, fallback) => {
      vi.useFakeTimers()
      fetchMock.mockResolvedValueOnce(new Response('abc', { status: 200 }))
      const out = await (api[name] as (...a: unknown[]) => Promise<{ name: string; size: number }>)(...args)
      expect(fetchMock.mock.calls[0][0]).toBe(url)
      expect(out).toEqual({ name: fallback, size: 3 })
      expect(clicked).toEqual([{ href: 'blob:x', download: fallback }])
      vi.advanceTimersByTime(4000)
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x')
    })

    it('uses the file name from Content-Disposition', async () => {
      fetchMock.mockResolvedValueOnce(new Response('abcd', { status: 200, headers: { 'Content-Disposition': 'attachment; filename="seqanno_export_20261122.zip"' } }))
      await expect(api.exportProject(1)).resolves.toEqual({ name: "seqanno_export_20261122.zip", size: 4 })
    })

    it.each([
      [jsonResponse({ detail: '対象の図面が見つかりません' }, 404), '対象の図面が見つかりません'],
      [jsonResponse({}, 409), '出力に失敗しました (409)'],
      [new Response('x', { status: 500 }), '出力に失敗しました (500)'],
    ])('reports download errors', async (response, message) => {
      fetchMock.mockResolvedValueOnce(response)
      await expect(api.exportBulk({ ids: [] })).rejects.toThrow(message)
      expect(clicked).toEqual([])
    })
  })
})
