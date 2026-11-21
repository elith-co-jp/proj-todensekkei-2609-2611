import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, FolderPlus, Layers, Save, Sparkles } from 'lucide-react'

import { api } from '../../api/client'
import type { DrawingSet, DrawingSetCoverage, DrawingSetSuggestion } from '../../types'

type Props = {
  selected: number[]
  onSelect: (ids: number[]) => void
}

const sameIds = (a: number[], b: number[]) => {
  const set = new Set(a)
  return a.length === b.length && b.every((v) => set.has(v))
}

/**
 * 図面セット（DD-0041）: 行先札でつながる図面の組に名前を付けて残し、選ぶだけで同じ組で回路を組めるようにする。
 * セットを選ぶと、行先札が指すのにセットに無いシートを出し、登録済みなら追加できる。
 */
export default function DrawingSetBar({ selected, onSelect }: Props) {
  const [sets, setSets] = useState<DrawingSet[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [coverage, setCoverage] = useState<DrawingSetCoverage | null>(null)
  const [suggestions, setSuggestions] = useState<DrawingSetSuggestion[] | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e))

  useEffect(() => {
    api.listDrawingSets().then(setSets).catch(fail)
  }, [])

  const loadCoverage = useCallback((id: number) => {
    api.drawingSetCoverage(id).then(setCoverage).catch(fail)
  }, [])

  const active = sets.find((s) => s.id === activeId) ?? null
  const changed = active !== null && !sameIds(active.project_ids, selected)

  const choose = (id: number | null) => {
    setActiveId(id)
    setCoverage(null)
    setError(null)
    const set = sets.find((s) => s.id === id)
    if (!set) return
    onSelect(set.project_ids)
    loadCoverage(set.id)
  }

  const save = async () => {
    setBusy(true)
    try {
      const saved = active && changed
        ? await api.updateDrawingSet(active.id, { project_ids: selected })
        : await api.createDrawingSet(name.trim(), selected)
      setSets((prev) => [...prev.filter((s) => s.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name)))
      setActiveId(saved.id)
      setName('')
      setError(null)
      loadCoverage(saved.id)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const suggest = async () => {
    try {
      setSuggestions((await api.suggestDrawingSets()).filter((g) => g.project_ids.length > 1))
    } catch (e) {
      fail(e)
    }
  }

  const missing = coverage?.missing_sheets ?? []
  const addable = missing.flatMap((m) => m.candidates.map((c) => c.project_id))

  return (
    <div className="mb-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3" role="group" aria-label="図面セットの操作">
      <div className="flex flex-wrap items-center gap-2">
        <Layers size={14} className="text-slate-500" />
        <label className="text-xs font-bold text-slate-700" htmlFor="drawing-set-select">図面セット</label>
        <select
          id="drawing-set-select"
          className="field h-8 min-h-8 w-48 py-0 text-xs"
          value={activeId ?? ''}
          onChange={(e) => choose(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">（選ばない）</option>
          {sets.map((s) => (
            <option key={s.id} value={s.id}>{s.name}（{s.project_ids.length} 枚）</option>
          ))}
        </select>
        {active && changed && (
          <button type="button" className="btn btn-sm" onClick={() => void save()} disabled={busy || selected.length === 0}>
            <Save size={12} /> 今の選択でセットを更新
          </button>
        )}
        {!active && selected.length > 0 && (
          <>
            <input
              className="field h-8 min-h-8 w-36 py-0 text-xs"
              placeholder="セットの名前"
              aria-label="セットの名前"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button type="button" className="btn btn-sm" onClick={() => void save()} disabled={busy || !name.trim()}>
              <FolderPlus size={12} /> 選択をセットとして保存
            </button>
          </>
        )}
        <button type="button" className="btn btn-sm ml-auto" onClick={() => void suggest()} title="行先札でつながる図面のまとまりを探します">
          <Sparkles size={12} /> 行先札から候補
        </button>
      </div>

      {suggestions && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
          {suggestions.length === 0 && <span className="text-slate-500">行先札でつながる図面の組は見つかりませんでした。</span>}
          {suggestions.map((g) => (
            <button
              key={g.project_ids.join(',')}
              type="button"
              className="rounded-lg border border-slate-200 bg-white px-2 py-1 hover:border-blue-300"
              onClick={() => {
                choose(null)
                onSelect(g.project_ids)
                setName(g.name)
                setSuggestions(null)
              }}
              title={g.sheets.join(', ')}
            >
              {g.name} ほか {g.project_ids.length} 枚
              {g.missing_sheets.length > 0 && <span className="ml-1 text-amber-700">（未登録 {g.missing_sheets.length}）</span>}
            </button>
          ))}
        </div>
      )}

      {coverage && (
        <div className="mt-2 text-[11px] leading-5" role="status">
          {coverage.complete ? (
            <span className="flex items-center gap-1 text-emerald-700">
              <CheckCircle2 size={12} /> 行先札が指すシートはすべてセットに入っています。
            </span>
          ) : (
            <div className="space-y-1 text-amber-800">
              {missing.map((m) => (
                <div key={m.sheet} className="flex flex-wrap items-center gap-1">
                  <AlertTriangle size={12} />
                  <span>
                    {m.sheet} がセットにありません（{m.referenced_by.map((r) => r.label || r.symbol_ref).slice(0, 3).join('、')} が参照）。
                    {m.candidates.length === 0 && ' この図面はまだ登録されていません。'}
                  </span>
                </div>
              ))}
              {coverage.pages_without_sheet.map((p) => (
                <div key={p.project_id} className="flex items-center gap-1">
                  <AlertTriangle size={12} /> {p.name} はシート番号が分からないため、行先札をつなげません。
                </div>
              ))}
              {addable.length > 0 && (
                <button type="button" className="btn btn-sm" onClick={() => onSelect([...new Set([...selected, ...addable])])}>
                  登録済みの {addable.length} 枚を選択に追加
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {error && <p className="mt-2 text-[11px] text-red-700">{error}</p>}
    </div>
  )
}
