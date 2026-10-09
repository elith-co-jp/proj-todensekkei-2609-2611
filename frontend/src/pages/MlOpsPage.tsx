import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Brain,
  CheckCircle2,
  Download,
  FileJson,
  FlaskConical,
  Layers,
  Loader2,
  Scale,
  Sparkles,
  Trash2,
  Upload,
  XCircle,
  Zap,
} from 'lucide-react'

import { api } from '../api/client'
import { LoadingOverlay } from '../components/LoadingOverlay'
import type {
  InferenceImportResult,
  InferenceRunResponse,
  MlModel,
  MlStatus,
  PredictionSummary,
  ProjectPredictions,
  ProjectRow,
  TrainingRun,
} from '../types'

type Notice = { kind: 'success' | 'error'; title: string; body: string }

// 学習実行中のステータスポーリング間隔
const TRAINING_POLL_MS = 3000

const CYCLE_STEPS = [
  { icon: Zap, label: '1. 解析', desc: 'シンボル・配線・接続を検出' },
  { icon: FileJson, label: '2. 結果表示', desc: '図面で確認・構造JSONを出力' },
  { icon: Layers, label: '3. 修正', desc: 'エディタで誤検出・漏れを修正' },
  { icon: Brain, label: '4. 学習', desc: '修正済みデータで YOLO を再学習' },
  { icon: Scale, label: '5. 比較・採用', desc: '新旧モデルの精度を比較して採用可否を選択' },
]

// 時間のかかる操作で画面全体に出すローディング表示（busy キー → メッセージ）
const BUSY_OVERLAY: Record<string, { message: string; hint: string }> = {
  inference: {
    message: '図面を解析しています',
    hint: '登録図面を順に処理しています。対象が多いほど時間がかかります。',
  },
  import: {
    message: '推論結果を取り込んでいます',
    hint: 'ZIP の中身を確認して登録しています。',
  },
  model: {
    message: 'モデルを登録しています',
    hint: '重みファイルをアップロードしています。',
  },
}

// 採用判定パネルで並べる評価指標
const COMPARE_METRICS = [
  { key: 'metrics/mAP50-95(B)', label: 'mAP50-95' },
  { key: 'metrics/mAP50(B)', label: 'mAP50' },
  { key: 'metrics/precision(B)', label: 'Precision' },
  { key: 'metrics/recall(B)', label: 'Recall' },
]

function fmtBytes(n: number) {
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`
  return `${(n / 1024).toFixed(1)} KB`
}

function fmtTime(iso: string | null) {
  return iso ? iso.replace('T', ' ').slice(5, 19) : '—'
}

export default function MlOpsPage() {
  const [status, setStatus] = useState<MlStatus | null>(null)
  const [models, setModels] = useState<MlModel[]>([])
  const [projects, setProjects] = useState<ProjectRow[]>([])
  const [summaries, setSummaries] = useState<PredictionSummary[]>([])
  const [runs, setRuns] = useState<TrainingRun[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  // 推論パラメータ
  const [conf, setConf] = useState(0.25)
  const [ids, setIds] = useState<Set<number>>(new Set())
  const [inferenceResult, setInferenceResult] = useState<InferenceRunResponse | null>(null)
  const [importResult, setImportResult] = useState<InferenceImportResult | null>(null)
  const [jsonOpen, setJsonOpen] = useState<number | null>(null)
  const [jsonCache, setJsonCache] = useState<Record<number, ProjectPredictions>>({})

  // 学習パラメータ
  const [epochs, setEpochs] = useState(100)
  const [imgsz, setImgsz] = useState(1280)
  const [onlyDone, setOnlyDone] = useState(true)
  const [trainIds, setTrainIds] = useState<Set<number>>(new Set())
  const [logOpen, setLogOpen] = useState<number | null>(null)

  const modelFileRef = useRef<HTMLInputElement | null>(null)
  const zipFileRef = useRef<HTMLInputElement | null>(null)

  const reload = useCallback(async () => {
    try {
      const [s, m, p, sum, r] = await Promise.all([
        api.mlStatus(),
        api.listModels(),
        api.listProjects(),
        api.listPredictions(),
        api.listTrainingRuns(),
      ])
      setStatus(s)
      setModels(m)
      setProjects(p)
      setSummaries(sum)
      setRuns(r)
      setError(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  // 学習中は定期ポーリングで状況を追う
  const trainingRunning = status?.training_running != null || runs.some((r) => r.status === 'running')
  useEffect(() => {
    if (!trainingRunning) return
    const timer = window.setInterval(() => void reload(), TRAINING_POLL_MS)
    return () => window.clearInterval(timer)
  }, [trainingRunning, reload])

  const toggleSet = (setter: React.Dispatch<React.SetStateAction<Set<number>>>) => (id: number) =>
    setter((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  const toggleInfer = toggleSet(setIds)
  const toggleTrain = toggleSet(setTrainIds)

  const runInference = async () => {
    setBusy('inference')
    setNotice(null)
    try {
      const r = await api.runAnalysis(Array.from(ids), conf)
      setInferenceResult(r)
      setImportResult(null)
      setJsonCache({})
      setJsonOpen(null)
      setNotice({
        kind: 'success',
        title: '解析結果を保存しました',
        body: `${r.detection_count} 件のシンボル、${r.results.reduce((n, page) => n + (page.wire_count ?? 0), 0)} 本の配線を検出しました（${r.results.length} 図面）。${r.results.some((page) => page.warnings?.length) ? '一部のラベル読み取りに問題があります。解析結果を確認してください。' : ''}`,
      })
      await reload()
    } catch (e) {
      setNotice({ kind: 'error', title: '推論に失敗しました', body: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
    }
  }

  const importZip = async (f: File | undefined) => {
    if (!f) return
    setBusy('import')
    setNotice(null)
    try {
      const r = await api.importPredictionsZip(f)
      setImportResult(r)
      setInferenceResult(null)
      setJsonCache({})
      setJsonOpen(null)
      setNotice({
        kind: 'success',
        title: '推論結果を取り込みました',
        body: `${f.name} から ${r.count} 件を読み込みました。`,
      })
      await reload()
    } catch (e) {
      setNotice({ kind: 'error', title: '取り込みに失敗しました', body: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
    }
  }

  const uploadModel = async (f: File | undefined) => {
    if (!f) return
    setBusy('model')
    setNotice(null)
    try {
      const m = await api.uploadModel(f)
      setNotice({ kind: 'success', title: 'モデルを登録しました', body: `${m.name} (v${m.version})` })
      await reload()
    } catch (e) {
      setNotice({ kind: 'error', title: 'モデル登録に失敗しました', body: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
    }
  }

  const activate = async (id: number) => {
    setBusy(`activate-${id}`)
    try {
      await api.activateModel(id)
      await reload()
    } catch (e) {
      setNotice({ kind: 'error', title: '切り替えに失敗しました', body: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
    }
  }

  const removeModel = async (id: number) => {
    if (!window.confirm('このモデルを削除しますか？（推論結果は残ります）')) return
    setBusy(`delete-${id}`)
    try {
      await api.deleteModel(id)
      await reload()
    } catch (e) {
      setNotice({ kind: 'error', title: '削除に失敗しました', body: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
    }
  }

  const startTraining = async () => {
    setBusy('train')
    setNotice(null)
    try {
      const run = await api.startTraining({
        project_ids: Array.from(trainIds),
        only_done: onlyDone,
        epochs,
        imgsz,
      })
      setNotice({
        kind: 'success',
        title: `学習を開始しました（ジョブ #${run.id}）`,
        body: `${run.image_count} 枚の画像で学習しています。完了すると新旧モデルの精度比較が表示され、採用するかどうかを選べます。`,
      })
      await reload()
    } catch (e) {
      setNotice({ kind: 'error', title: '学習を開始できません', body: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
    }
  }

  const decideRun = async (runId: number, decision: 'adopt' | 'reject') => {
    setBusy(`decide-${runId}`)
    setNotice(null)
    try {
      const run = await api.decideTrainingRun(runId, decision)
      const name = run.result_model ? `${run.result_model.name} v${run.result_model.version}` : '新しいモデル'
      setNotice(
        decision === 'adopt'
          ? {
              kind: 'success',
              title: `${name} を採用しました`,
              body: '使用中のモデルに切り替えました。次回以降の推論と学習のベースに使われます。',
            }
          : {
              kind: 'success',
              title: `${name} を見送りました`,
              body: '使用中のモデルはそのまま継続します。見送ったモデルはモデル管理から削除できます。',
            },
      )
      await reload()
    } catch (e) {
      setNotice({ kind: 'error', title: '判定の登録に失敗しました', body: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
    }
  }

  const toggleJson = async (projectId: number) => {
    if (jsonOpen === projectId) {
      setJsonOpen(null)
      return
    }
    if (!jsonCache[projectId]) {
      try {
        const data = await api.getPredictions(projectId)
        setJsonCache((prev) => ({ ...prev, [projectId]: data }))
      } catch (e) {
        setNotice({ kind: 'error', title: '推論結果の取得に失敗しました', body: e instanceof Error ? e.message : String(e) })
        return
      }
    }
    setJsonOpen(projectId)
  }

  const summaryFor = (id: number) => summaries.find((s) => s.project_id === id)

  const downloadStructure = async (projectIds: number[]) => {
    try {
      await api.exportStructure(projectIds)
    } catch (caught) {
      setNotice({ kind: 'error', title: 'JSONの出力に失敗しました', body: caught instanceof Error ? caught.message : String(caught) })
    }
  }

  // 採用待ちの学習ジョブ（すべて判定パネルを出す）
  const pendingRuns = runs.filter((r) => r.status === 'success' && r.decision === 'pending' && r.result_model)
  const pendingModelIds = new Set(
    runs.filter((r) => r.decision === 'pending').map((r) => r.result_model_id),
  )

  const metricDelta = (run: TrainingRun, key: string) => {
    const current = Number(run.metrics?.[key])
    const baseline = Number(run.baseline_metrics?.[key])
    if (!Number.isFinite(current) || !Number.isFinite(baseline)) return null
    return current - baseline
  }

  const busyOverlay = busy ? BUSY_OVERLAY[busy] : undefined

  return (
    <div className="page-shell enter-up">
      {busyOverlay && <LoadingOverlay message={busyOverlay.message} hint={busyOverlay.hint} />}
      <div aria-hidden={Boolean(busyOverlay)} inert={busyOverlay ? true : undefined}>
      <header className="page-header">
        <div>
          <div className="eyebrow">AI improvement cycle</div>
          <h1 className="page-title">AI 改善サイクル</h1>
          <p className="page-description">
            推論 → 結果確認 → エディタで修正 → データ蓄積 → YOLO 学習 → 精度比較・採用判定 のサイクルをまわします
          </p>
        </div>
      </header>

      <section className="notice mb-5 border-cyan-200 bg-cyan-50 text-cyan-900">
        <Brain size={18} className="mt-1 flex-none text-cyan-700" />
        <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-5">
          {CYCLE_STEPS.map((step) => (
            <div key={step.label} className="text-[12px] leading-5">
              <div className="flex items-center gap-1.5 font-black text-slate-900">
                <step.icon size={13} className="text-cyan-700" />
                {step.label}
              </div>
              <p className="mt-0.5">{step.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {status && !status.ultralytics && (
        <section className="notice mb-5 border-amber-200 bg-amber-50 text-amber-900" role="status">
          <FlaskConical size={18} className="mt-0.5 flex-none text-amber-700" />
          <div className="min-w-0 text-[12px] leading-6">
            <div className="font-black text-slate-900">この環境では ultralytics が入っていません（外部実行モード）</div>
            <p>
              学習や推論は外部の GPU 環境で実行してください。
              ① データ受け渡しで ZIP をエクスポート → ② 外部で <code className="rounded bg-amber-100 px-1">yolo predict --save-txt --save-conf</code> を実行 →
              ③ 出力した labels フォルダを ZIP にして下の「推論結果を取り込む」で読み込みます。
              学習後は作成された <code className="rounded bg-amber-100 px-1">best.pt</code> を「モデル登録」からアップロードしてください。
              （ツール内で直接実行するには <code className="rounded bg-amber-100 px-1">pip install -r requirements-ml.txt</code> が必要です）
            </p>
          </div>
        </section>
      )}

      {error && <div className="notice mb-5 border-rose-200 bg-rose-50 text-rose-700" role="alert">{error}</div>}

      {notice && (
        <div
          className={`notice mb-5 ${
            notice.kind === 'success'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
              : 'border-rose-200 bg-rose-50 text-rose-700'
          }`}
          role={notice.kind === 'success' ? 'status' : 'alert'}
        >
          {notice.kind === 'success' ? (
            <CheckCircle2 size={18} className="mt-0.5 flex-none" />
          ) : (
            <XCircle size={18} className="mt-0.5 flex-none" />
          )}
          <div className="min-w-0 flex-1">
            <div className="text-sm font-black">{notice.title}</div>
            <div className="mt-0.5 text-[12px] leading-5">{notice.body}</div>
          </div>
          <button type="button" className="btn btn-sm" onClick={() => setNotice(null)}>
            閉じる
          </button>
        </div>
      )}

      {pendingRuns.map((pendingRun) => (
        <section key={pendingRun.id} className="card mb-5 overflow-hidden border-violet-200">
          <div className="card-head bg-violet-50/60">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-100 text-violet-700">
              <Scale size={17} />
            </span>
            <div>
              <div className="text-[9px] font-bold uppercase tracking-wider text-violet-400">Adoption review</div>
              <h2 className="text-sm font-black">学習済みモデルの採用判定（ジョブ #{pendingRun.id}）</h2>
            </div>
          </div>
          <div className="space-y-4 p-4 sm:p-5">
            <p className="text-[12px] leading-6 text-slate-600">
              新しいモデル{' '}
              <b>
                {pendingRun.result_model?.name} v{pendingRun.result_model?.version}
              </b>{' '}
              を蓄積済みデータ（{pendingRun.image_count} 枚）で評価し、学習の起点となったモデル
              （{pendingRun.baseline_label ?? '比較対象なし'}）と比較しました。
              採用すると次回以降の推論と学習のベースにこのモデルが使われます。
            </p>
            {pendingRun.metrics || pendingRun.baseline_metrics ? (
              <div className="overflow-x-auto rounded-2xl border border-slate-200">
                <table className="w-full min-w-[520px] text-xs">
                  <thead>
                    <tr className="bg-slate-50 text-left text-[10px] font-bold uppercase tracking-wider text-slate-400">
                      <th className="px-3 py-2">指標</th>
                      <th className="px-3 py-2">
                        新モデル（{pendingRun.result_model?.name} v{pendingRun.result_model?.version}）
                      </th>
                      <th className="px-3 py-2">比較対象（{pendingRun.baseline_label ?? '—'}）</th>
                      <th className="px-3 py-2">差分</th>
                    </tr>
                  </thead>
                  <tbody>
                    {COMPARE_METRICS.map(({ key, label }) => {
                      const delta = metricDelta(pendingRun, key)
                      return (
                        <tr key={key} className="border-t border-slate-100">
                          <td className="px-3 py-2 font-bold text-slate-700">{label}</td>
                          <td className="px-3 py-2 font-mono text-slate-800">{pendingRun.metrics?.[key] ?? '—'}</td>
                          <td className="px-3 py-2 font-mono text-slate-500">{pendingRun.baseline_metrics?.[key] ?? '—'}</td>
                          <td className="px-3 py-2 font-mono">
                            {delta === null ? (
                              <span className="text-slate-300">—</span>
                            ) : (
                              <span
                                className={
                                  delta > 0 ? 'font-bold text-emerald-600' : delta < 0 ? 'font-bold text-rose-600' : 'text-slate-400'
                                }
                              >
                                {delta > 0 ? '+' : ''}
                                {delta.toFixed(4)}
                              </span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="rounded-2xl border border-dashed border-slate-300 px-4 py-3 text-[11px] text-slate-500">
                評価指標を取得できませんでした。エディタで推論結果を確認して採用可否を判断してください。
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-accent"
                onClick={() => void decideRun(pendingRun.id, 'adopt')}
                disabled={busy !== null}
              >
                {busy === `decide-${pendingRun.id}` ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                このモデルを採用する
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => void decideRun(pendingRun.id, 'reject')}
                disabled={busy !== null}
              >
                見送る（使用中のモデルはそのまま）
              </button>
            </div>
          </div>
        </section>
      ))}

      <div className="grid gap-5 xl:grid-cols-2">
        {/* ---------------- 左列：推論 ---------------- */}
        <div className="space-y-5">
          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan-50 text-cyan-700">
                <Zap size={17} />
              </span>
              <div>
                <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Inference</div>
                <h2 className="text-sm font-black">解析を実行する</h2>
              </div>
              <span className="flex-1" />
              {status?.active_model && (
                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-bold text-emerald-700">
                  {status.active_model.name} v{status.active_model.version}
                </span>
              )}
            </div>
            <div className="space-y-4 p-4 sm:p-5">
              <div className="flex flex-wrap items-end gap-3">
                <label className="text-[11px] text-slate-500">
                  <span className="mb-1 block font-bold">信頼度しきい値</span>
                  <input
                    type="number"
                    className="field w-24"
                    min={0.01}
                    max={1}
                    step={0.05}
                    value={conf}
                    onChange={(e) => setConf(Number(e.target.value))}
                  />
                </label>
                <p className="flex-1 text-[11px] leading-5 text-slate-400">
                  しきい値以下の検出は捨てます。推論結果は図面ごとに保存され、エディタで確認・修正できます。
                </p>
              </div>
              <label className="block">
                <span className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                  <span>対象（未選択なら全件）— 選択 {ids.size} / {projects.length} 枚</span>
                  <span className="flex-1" />
                  <button type="button" className="btn btn-sm" onClick={() => setIds(new Set(projects.map((r) => r.id)))} disabled={projects.length === 0}>
                    全選択
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => setIds(new Set())} disabled={ids.size === 0}>
                    選択解除
                  </button>
                </span>
                <div className="thin-scroll max-h-64 overflow-auto rounded-2xl border border-slate-200 bg-slate-50/60">
                  {projects.map((r) => (
                    <label key={r.id} className="flex min-h-10 items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs last:border-0 hover:bg-white">
                      <input className="h-6 w-6 flex-none" type="checkbox" checked={ids.has(r.id)} onChange={() => toggleInfer(r.id)} />
                      <span className="font-mono text-slate-400">{r.id}</span>
                      <span className="truncate">{r.name}</span>
                      <span className="flex-1" />
                      {summaryFor(r.id) && (
                        <span className="rounded-full bg-cyan-50 px-2 py-0.5 font-mono text-[10px] text-cyan-700">
                          検出 {summaryFor(r.id)?.count}
                        </span>
                      )}
                    </label>
                  ))}
                  {projects.length === 0 && <div className="px-3 py-4 text-center text-xs text-slate-400">図面がありません</div>}
                </div>
              </label>
              <button
                className="btn btn-accent w-full justify-center"
                onClick={() => void runInference()}
                disabled={busy !== null || !status?.ultralytics || !status?.active_model || projects.length === 0}
              >
                {busy === 'inference' ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {!status?.active_model
                  ? 'モデルを先に登録してください'
                  : !status?.ultralytics
                    ? 'この環境では実行できません（外部実行）'
                    : 'AI 解析を実行'}
              </button>

              <div className="border-t border-slate-100 pt-4">
                <div className="mb-2 flex items-center gap-2 text-[11px] font-bold text-slate-500">
                  <Upload size={12} />
                  外部で実行した推論結果を取り込む
                </div>
                <p className="mb-3 text-[11px] leading-5 text-slate-400">
                  <code className="rounded bg-slate-100 px-1">yolo predict --save-txt --save-conf</code> で出力した labels の ZIP を読み込みます。
                  エクスポートした画像名（p&lt;ID&gt;_*.png）から図面へ自動で対応づけます。
                </p>
                <button className="btn w-full justify-center" onClick={() => zipFileRef.current?.click()} disabled={busy !== null}>
                  <Upload size={14} /> 推論結果 ZIP を選択
                </button>
                <input
                  ref={zipFileRef}
                  type="file"
                  accept=".zip,application/zip"
                  hidden
                  onChange={(e) => {
                    void importZip(e.target.files?.[0])
                    e.target.value = ''
                  }}
                />
              </div>
            </div>
          </div>

          {(inferenceResult || importResult) && (
            <div className="card overflow-hidden">
              <div className="card-head">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-700">
                  <FileJson size={17} />
                </span>
                <div>
                  <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Results</div>
                  <h2 className="text-sm font-black">直近の解析結果</h2>
                </div>
                {inferenceResult && <button type="button" className="btn btn-sm ml-auto" onClick={() => void downloadStructure(inferenceResult.results.map((page) => page.project_id))}><Download size={14} />構造JSON</button>}
              </div>
              <div className="p-4 sm:p-5">
                <div className="thin-scroll max-h-80 overflow-auto rounded-2xl border border-slate-200">
                  {(inferenceResult?.results ?? importResult?.results ?? []).map((r) => (
                    <div key={r.project_id}>
                      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs">
                        <span className="font-mono text-slate-400">{r.project_id}</span>
                        <Link to={`/projects/${r.project_id}/analysis`} className="truncate font-bold text-slate-700 hover:text-cyan-700">
                          {r.name}
                        </Link>
                        <span className="flex-1" />
                        <span className="font-mono text-slate-500">{r.detections} 件</span>
                        {r.structure_available ? <button type="button" className="icon-button" title="構造JSONをダウンロード" aria-label="構造JSONをダウンロード" onClick={() => void downloadStructure([r.project_id])}><Download size={15} /></button> : <button type="button" className="btn btn-sm" onClick={() => void toggleJson(r.project_id)}>
                          {jsonOpen === r.project_id ? 'JSON を閉じる' : 'JSON'}
                        </button>}
                      </div>
                      {jsonOpen === r.project_id && (
                        <pre className="thin-scroll max-h-64 overflow-auto border-b border-slate-100 bg-slate-900 p-3 font-mono text-[10px] leading-4 text-emerald-100">
                          {JSON.stringify(jsonCache[r.project_id] ?? {}, null, 2)}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
                {importResult && (importResult.unmatched_files.length > 0 || importResult.missing_project_ids.length > 0) && (
                  <div className="notice mt-3 border-amber-200 bg-amber-50 text-[11px] text-amber-800">
                    対応づけできなかったファイル: {importResult.unmatched_files.join(', ') || 'なし'} ／
                    存在しない図面 ID: {importResult.missing_project_ids.join(', ') || 'なし'}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ---------------- 右列：モデル + 学習 ---------------- */}
        <div className="space-y-5">
          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-50 text-violet-700">
                <Layers size={17} />
              </span>
              <div>
                <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Models</div>
                <h2 className="text-sm font-black">モデル管理</h2>
              </div>
              <span className="flex-1" />
              <button type="button" className="btn btn-sm" onClick={() => modelFileRef.current?.click()} disabled={busy !== null}>
                <Upload size={13} /> モデル登録
              </button>
              <input
                ref={modelFileRef}
                type="file"
                accept=".pt,.pth,.torchscript"
                hidden
                onChange={(e) => {
                  void uploadModel(e.target.files?.[0])
                  e.target.value = ''
                }}
              />
            </div>
            <div className="p-4 sm:p-5">
              <p className="mb-3 text-[11px] leading-5 text-slate-400">
                YOLO の重みファイル（best.pt 等）を登録します。「使用中」のモデルが推論と学習のベースに使われます。
              </p>
              {models.length === 0 && (
                <div className="rounded-2xl border border-dashed border-slate-300 px-4 py-6 text-center text-xs text-slate-400">
                  推論用モデルがまだありません。best.pt をアップロードするか、学習を実行してください。配布版では内蔵の初期モデルから学習を開始できます。
                </div>
              )}
              <div className="space-y-2">
                {models.map((m) => (
                  <div
                    key={m.id}
                    className={`flex items-center gap-3 rounded-2xl border px-3 py-2.5 text-xs ${
                      m.is_active ? 'border-emerald-300 bg-emerald-50/60' : 'border-slate-200'
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 font-bold text-slate-800">
                        <span className="truncate">{m.name}</span>
                        <span className="font-mono text-slate-400">v{m.version}</span>
                        {m.is_active && <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-black text-white">使用中</span>}
                        {m.source === 'trained' && <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-bold text-violet-700">学習済み</span>}
                        {m.source === 'bundled' && <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-bold text-sky-700">同梱</span>}
                        {pendingModelIds.has(m.id) && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700">採用待ち</span>}
                      </div>
                      <div className="mt-0.5 font-mono text-[10px] text-slate-400">
                        {m.file_name} ・ {fmtBytes(m.size_bytes)} ・ {fmtTime(m.created_at)}
                        {m.metrics && ` ・ mAP50-95=${m.metrics['metrics/mAP50-95(B)'] ?? '—'}`}
                      </div>
                    </div>
                    {!m.is_active && (
                      <button type="button" className="btn btn-sm" onClick={() => void activate(m.id)} disabled={busy !== null}>
                        使用する
                      </button>
                    )}
                    <a className="icon-button" href={api.modelDownloadUrl(m.id)} title="ダウンロード" download>
                      <Download size={14} />
                    </a>
                    <button type="button" className="icon-button text-rose-500" onClick={() => void removeModel(m.id)} title="削除" disabled={busy !== null}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="card overflow-hidden">
            <div className="card-head">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-50 text-amber-700">
                <Brain size={17} />
              </span>
              <div>
                <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Training</div>
                <h2 className="text-sm font-black">YOLO 学習</h2>
              </div>
              {status?.training_running && (
                <span className="ml-2 inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-bold text-amber-800">
                  <Loader2 size={11} className="animate-spin" /> 学習中
                </span>
              )}
            </div>
            <div className="space-y-4 p-4 sm:p-5">
              <div className="grid grid-cols-2 gap-3">
                <label className="text-[11px] text-slate-500">
                  <span className="mb-1 block font-bold">エポック数</span>
                  <input type="number" className="field w-full" min={1} max={1000} value={epochs} onChange={(e) => setEpochs(Number(e.target.value))} />
                </label>
                <label className="text-[11px] text-slate-500">
                  <span className="mb-1 block font-bold">画像サイズ (imgsz)</span>
                  <input type="number" className="field w-full" min={320} max={4096} step={32} value={imgsz} onChange={(e) => setImgsz(Number(e.target.value))} />
                </label>
              </div>
              <label className="flex items-center gap-2 text-[11px] text-slate-600">
                <input type="checkbox" className="h-4 w-4" checked={onlyDone} onChange={(e) => setOnlyDone(e.target.checked)} />
                完了（done）の図面だけを学習に使う
              </label>
              <label className="block">
                <span className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                  <span>学習対象を個別指定（未選択なら上の条件）</span>
                  <span className="flex-1" />
                  <button type="button" className="btn btn-sm" onClick={() => setTrainIds(new Set(projects.map((r) => r.id)))} disabled={projects.length === 0}>
                    全選択
                  </button>
                  <button type="button" className="btn btn-sm" onClick={() => setTrainIds(new Set())} disabled={trainIds.size === 0}>
                    選択解除
                  </button>
                </span>
                <div className="thin-scroll max-h-40 overflow-auto rounded-2xl border border-slate-200 bg-slate-50/60">
                  {projects.map((r) => (
                    <label key={r.id} className="flex min-h-9 items-center gap-2 border-b border-slate-100 px-3 py-1.5 text-xs last:border-0 hover:bg-white">
                      <input className="h-5 w-5 flex-none" type="checkbox" checked={trainIds.has(r.id)} onChange={() => toggleTrain(r.id)} />
                      <span className="font-mono text-slate-400">{r.id}</span>
                      <span className="truncate">{r.name}</span>
                      <span className="flex-1" />
                      <span className="font-mono text-slate-400">{r.symbol_count} シンボル</span>
                    </label>
                  ))}
                </div>
              </label>
              <button
                className="btn btn-accent w-full justify-center"
                onClick={() => void startTraining()}
                disabled={busy !== null || !status?.ultralytics || trainingRunning || projects.length === 0}
              >
                {busy === 'train' || trainingRunning ? <Loader2 size={14} className="animate-spin" /> : <Brain size={14} />}
                {trainingRunning ? '学習を実行中…' : '学習を開始'}
              </button>
            </div>

            {runs.length > 0 && (
              <div className="border-t border-slate-100 p-4 sm:p-5">
                <div className="mb-2 text-[11px] font-bold text-slate-500">学習履歴</div>
                <div className="thin-scroll max-h-72 overflow-auto rounded-2xl border border-slate-200">
                  {runs.map((r) => (
                    <div key={r.id}>
                      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs">
                        <span
                          className={`inline-flex h-2.5 w-2.5 flex-none rounded-full ${
                            r.status === 'success' ? 'bg-emerald-500' : r.status === 'failed' ? 'bg-rose-500' : 'bg-amber-400'
                          }`}
                        />
                        <span className="font-mono text-slate-400">#{r.id}</span>
                        <span className="font-bold text-slate-700">
                          {r.status === 'running' ? '実行中' : r.status === 'success' ? '完了' : '失敗'}
                        </span>
                        {r.decision === 'pending' && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700">採用待ち</span>
                        )}
                        {r.decision === 'adopted' && (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">採用</span>
                        )}
                        {r.decision === 'rejected' && (
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-500">見送り</span>
                        )}
                        <span className="font-mono text-[10px] text-slate-400">
                          {r.image_count}枚 / {r.epochs}ep / {r.imgsz}px
                        </span>
                        {r.metrics?.['metrics/mAP50-95(B)'] && (
                          <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-mono text-[10px] text-emerald-700">
                            mAP {r.metrics['metrics/mAP50-95(B)']}
                          </span>
                        )}
                        <span className="flex-1" />
                        <span className="font-mono text-[10px] text-slate-400">{fmtTime(r.started_at)}</span>
                        {r.log_tail && (
                          <button type="button" className="btn btn-sm" onClick={() => setLogOpen(logOpen === r.id ? null : r.id)}>
                            ログ
                          </button>
                        )}
                      </div>
                      {logOpen === r.id && r.log_tail && (
                        <pre className="thin-scroll max-h-48 overflow-auto border-b border-slate-100 bg-slate-900 p-3 font-mono text-[10px] leading-4 text-slate-100">
                          {r.log_tail}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
      </div>
    </div>
  )
}
