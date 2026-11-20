import { useEffect, useState } from 'react'
import { AlertTriangle, Check, FlaskConical, Loader2, Undo2 } from 'lucide-react'

import { api, BASE } from '../../api/client'
import type { CircuitChangeSet, CircuitEditOp, CircuitEditPreview } from '../../types'

const EXAMPLE = `[
  {"op": "insert_in_series", "project_id": 1,
   "from": {"symbol_ref": "（接点の記号）", "terminal": "2"},
   "to": {"symbol_ref": "（コイルの記号）", "terminal": "1"},
   "component": {"class_key": "contact_b", "label": "（相手のリレー）"}}
]`

const onOff = (v: boolean | null) => (v == null ? '—' : v ? 'ON' : 'OFF')

type Props = { projectIds: number[]; onApplied?: () => void }

/**
 * 図面への変更を、決まった形の操作（JSON）で試し・適用し・取り消す（DD-0034）。
 * LLM が出す変更案（ツールの入力）をそのまま貼って確かめる場所でもある。
 */
export default function EditPanel({ projectIds, onApplied }: Props) {
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [reason, setReason] = useState('')
  const [preview, setPreview] = useState<CircuitEditPreview | null>(null)
  const [tried, setTried] = useState<string | null>(null)
  const [history, setHistory] = useState<CircuitChangeSet[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<number | null>(null)

  const loadHistory = async () => {
    try {
      setHistory((await api.circuitEditHistory(projectIds)).change_sets)
    } catch {
      /* 履歴は無くても試せる */
    }
  }
  useEffect(() => {
    void loadHistory()
  }, [projectIds.join(',')])

  const parse = (): CircuitEditOp[] | null => {
    try {
      const v = JSON.parse(text)
      const ops = Array.isArray(v) ? v : Array.isArray(v?.ops) ? v.ops : null
      if (!ops) throw new Error('操作の配列（または {"ops": [...]}）を入れてください')
      return ops as CircuitEditOp[]
    } catch (e) {
      setError(`JSON を読めません: ${e instanceof Error ? e.message : String(e)}`)
      return null
    }
  }

  const act = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const tryOps = () => {
    const ops = parse()
    if (!ops) return
    void act(async () => {
      setPreview(await api.circuitEditPreview(projectIds, ops))
      setTried(text)
    })
  }

  const apply = () => {
    const ops = parse()
    if (!ops || !preview?.ok) return
    void act(async () => {
      await api.circuitEditApply(projectIds, ops, title || '図面の変更', reason || null, preview.base_hash)
      setPreview(null)
      setTried(null)
      await loadHistory()
      onApplied?.()
    })
  }

  const undo = (id: number, force = false) =>
    void act(async () => {
      try {
        await api.circuitEditUndo(id, force)
        setConflict(null)
      } catch (e) {
        setConflict(id)
        throw e
      }
      await loadHistory()
      onApplied?.()
    })

  const canApply = !!preview?.ok && tried === text && !busy

  return (
    <section className="card mt-4 p-4" aria-label="図面の変更">
      <div className="mb-1 text-sm font-black">図面の変更</div>
      <p className="mb-2 text-[11px] leading-5 text-slate-500"
        title="部品の追加・削除、名前や種類の変更、配線のつなぎ替え、直列への差し込みを JSON の操作で表します。試行では図面を変えずに差分・動作の違い・波及範囲・検査結果を出します">
        操作（JSON）を書いて試行 → 適用。適用後は履歴から元に戻せます。
        <a className="ml-1 underline" href={`${BASE}/api/circuit/edit/tools`} target="_blank" rel="noreferrer">操作の定義（JSON Schema）</a>
      </p>
      <textarea aria-label="操作（JSON）" value={text} rows={7} spellCheck={false}
        placeholder={EXAMPLE}
        className="w-full rounded-lg border border-slate-200 p-2 font-mono text-[11px]"
        onChange={(e) => setText(e.target.value)} />
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
        <button type="button" className="btn btn-sm" disabled={busy || !text.trim()} onClick={tryOps}>
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <FlaskConical className="h-3 w-3" />} 試行
        </button>
        <input aria-label="件名" placeholder="件名" value={title} onChange={(e) => setTitle(e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1" />
        <input aria-label="理由" placeholder="理由（任意）" value={reason} onChange={(e) => setReason(e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1" />
        <button type="button" className="btn btn-sm btn-accent" disabled={!canApply} onClick={apply}>
          <Check className="h-3 w-3" /> 適用
        </button>
      </div>
      {error && (
        <div className="mt-2 flex items-start gap-1 text-[11px] text-rose-700" role="alert">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> <span>{error}</span>
          {conflict != null && (
            <button type="button" className="ml-2 underline" onClick={() => undo(conflict, true)}>それでも戻す</button>
          )}
        </div>
      )}
      {preview && <PreviewView preview={preview} />}
      {history.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs font-bold">履歴</div>
          <ul className="space-y-1 text-[11px]" aria-label="変更の履歴">
            {history.map((h) => (
              <li key={h.id} className="flex items-center gap-2">
                <span className="font-mono text-slate-400">#{h.id}</span>
                <span className="min-w-0 flex-1 truncate">{h.title}{h.reason ? `（${h.reason}）` : ''}</span>
                <span className="text-slate-400">{h.author} · 操作 {h.ops.length}</span>
                {h.status === 'applied' ? (
                  <button type="button" className="btn btn-sm" disabled={busy} onClick={() => undo(h.id)}>
                    <Undo2 className="h-3 w-3" /> 元に戻す
                  </button>
                ) : <span className="text-slate-400">戻し済み</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

function PreviewView({ preview }: { preview: CircuitEditPreview }) {
  if (!preview.ok) {
    return (
      <ul className="mt-2 space-y-0.5 text-[11px] text-rose-700" aria-label="操作の誤り">
        {(preview.errors ?? []).map((e, i) => <li key={i}>操作 {e.index + 1}（{e.op ?? '?'}）: {e.message}</li>)}
      </ul>
    )
  }
  const c = preview.circuit
  const v = preview.validation
  const changed = (preview.behaviour ?? []).filter((b) => b.coils.length || b.loads.length || b.warnings.length)
  const circuitLines = c ? [
    c.relays_added.length && `リレーが増える: ${c.relays_added.join('、')}`,
    c.relays_removed.length && `リレーが無くなる: ${c.relays_removed.join('、')}`,
    c.inputs_added.length && `入力が増える: ${c.inputs_added.join('、')}`,
    c.inputs_removed.length && `入力が無くなる: ${c.inputs_removed.join('、')}`,
    c.timers_changed.length && `タイマーが変わる: ${c.timers_changed.join('、')}`,
  ].filter(Boolean) as string[] : []
  return (
    <div className="mt-3 space-y-2 text-[11px]" aria-label="変更の結果">
      {(preview.notes ?? []).length > 0 && (
        <ul className="list-disc pl-4 text-slate-600">{preview.notes!.map((n, i) => <li key={i}>{n}</li>)}</ul>
      )}
      {circuitLines.length > 0 && <ul className="pl-1">{circuitLines.map((x) => <li key={x}>{x}</li>)}</ul>}
      <div>
        <span className="font-bold">動作の違い</span>（入力の組み合わせ {preview.scenarios ?? 0} 通りで比べた）:{' '}
        {changed.length === 0 ? '違いはありません' : `${changed.length} 通りで違う`}
      </div>
      {changed.length > 0 && (
        <table className="w-full text-left" aria-label="動作の違い">
          <thead><tr className="text-slate-400"><th>入力</th><th>コイル</th><th>負荷</th></tr></thead>
          <tbody>
            {changed.slice(0, 12).map((b, i) => (
              <tr key={i} className="align-top">
                <td className="pr-2">{b.inputs}</td>
                <td className="pr-2">{b.coils.map((x) => `${x.device} ${onOff(x.before)}→${onOff(x.after)}`).join('、')}</td>
                <td>{b.loads.map((x) => `${x.label ?? x.symbol_ref} ${onOff(x.before)}→${onOff(x.after)}`).join('、')}
                  {b.warnings.length > 0 && <span className="text-amber-700"> ⚠ {b.warnings.join(' / ')}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(preview.impact ?? []).length > 0 && (
        <div>
          <span className="font-bold">波及範囲</span>:{' '}
          {preview.impact!.map((x) => `${x.device} → ${[...x.devices, ...x.loads].join('、') || 'なし'}`).join(' / ')}
        </div>
      )}
      {v && (
        <div>
          <span className="font-bold">検査</span>: 新しい指摘 {v.issues_added.length} 件（うち誤り {v.errors}）、解消 {v.issues_resolved.length} 件、
          回路の注意 {v.diagnostics_added.length} 件
          <ul className="pl-3">
            {v.issues_added.map((x, i) => <li key={`a${i}`} className="text-rose-700">＋ {x.title}: {x.message}</li>)}
            {v.issues_resolved.map((x, i) => <li key={`r${i}`} className="text-emerald-700">✓ {x.title}</li>)}
            {v.diagnostics_added.map((x, i) => <li key={`d${i}`} className="text-amber-700">⚠ {x.message ?? x.kind}</li>)}
          </ul>
        </div>
      )}
      {preview.netlist_diff && (
        <details>
          <summary className="cursor-pointer font-bold">つながりの差分（正規化したネットリスト）</summary>
          <pre className="mt-1 max-h-60 overflow-auto rounded-lg bg-slate-50 p-2 font-mono text-[10px]">{preview.netlist_diff}</pre>
        </details>
      )}
    </div>
  )
}
