import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'

import type { AttributeDef, Attributes, AttributeValue } from '../../types'

function parse(def: AttributeDef | undefined, raw: string): AttributeValue {
  if (raw.trim() === '') return null
  if (def?.type === 'number') {
    const n = Number(raw.replace(/,/g, ''))
    return Number.isFinite(n) ? n : raw
  }
  return raw
}

/** 記号・配線の属性。標準の項目（メーカー・長さなど）と、自由に足した項目を編集する（DD-0010）。 */
export function AttributeEditor({
  defs,
  value,
  onChange,
  idPrefix,
}: {
  defs: AttributeDef[]
  value: Attributes | undefined
  onChange: (next: Attributes) => void
  idPrefix: string
}) {
  const attrs = value ?? {}
  const [newKey, setNewKey] = useState('')
  const known = new Set(defs.map((d) => d.key))
  const custom = Object.keys(attrs).filter((k) => !known.has(k))
  const set = (key: string, v: AttributeValue) => {
    const next = { ...attrs }
    if (v === null) delete next[key]
    else next[key] = v
    onChange(next)
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-2" aria-label="属性">
      <div className="mb-1.5 text-[10px] font-bold text-slate-500">属性</div>
      <div className="grid grid-cols-2 gap-1.5">
        {defs.map((d) => (
          <label key={d.key} className="block min-w-0" htmlFor={`${idPrefix}-${d.key}`}>
            <span className="mb-0.5 block truncate text-[10px] text-slate-500">
              {d.label}
              {d.unit ? `（${d.unit}）` : ''}
            </span>
            <input
              id={`${idPrefix}-${d.key}`}
              className="field w-full py-0.5 text-xs"
              inputMode={d.type === 'number' ? 'decimal' : undefined}
              value={attrs[d.key] == null ? '' : String(attrs[d.key])}
              onChange={(e) => set(d.key, parse(d, e.target.value))}
            />
          </label>
        ))}
      </div>
      {custom.map((key) => (
        <div key={key} className="mt-1.5 flex items-center gap-1.5">
          <span className="w-24 truncate font-mono text-[10px] text-slate-500" title={key}>{key}</span>
          <input
            aria-label={key}
            className="field flex-1 py-0.5 text-xs"
            value={attrs[key] == null ? '' : String(attrs[key])}
            onChange={(e) => onChange({ ...attrs, [key]: e.target.value })}
          />
          <button type="button" className="btn btn-sm btn-danger" onClick={() => set(key, null)} aria-label={`${key}を削除`}>
            <Trash2 size={11} />
          </button>
        </div>
      ))}
      <div className="mt-1.5 flex items-center gap-1.5">
        <input
          className="field flex-1 py-0.5 text-xs"
          placeholder="項目を追加（例: 納入日）"
          aria-label="追加する項目名"
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
        />
        <button
          type="button"
          className="btn btn-sm"
          disabled={!newKey.trim() || newKey.trim() in attrs}
          onClick={() => {
            onChange({ ...attrs, [newKey.trim()]: '' })
            setNewKey('')
          }}
        >
          <Plus size={11} /> 追加
        </button>
      </div>
    </div>
  )
}
