import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, FileSpreadsheet, Loader2, MapPin, Search } from 'lucide-react'

import { api } from '../../api/client'
import type { DeviceEntry, DeviceIndex, DeviceLocation, WireNumberEntry } from '../../types'

/** 図面の上で場所を示す（ページを切り替え、記号を中心に拡大して枠を点滅させる） */
export type LocateTarget = Pick<DeviceLocation, 'project_id' | 'cx' | 'cy' | 'w' | 'h'> & { label: string }

type View = 'devices' | 'wires' | 'notes'
const VIEW_LABEL: Record<View, string> = { devices: '機器', wires: '電線番号', notes: '要確認' }
const ROLE_TONE: Record<DeviceEntry['role'], string> = {
  relay: 'bg-rose-100 text-rose-700',
  input: 'bg-sky-100 text-sky-700',
  load: 'bg-slate-100 text-slate-600',
}
/** 一度に描く件数（数百枚の図面セットでも画面が重くならないように） */
const MAX_ROWS = 200

function editorLink(projectId: number, symbolRef: string) {
  return `/projects/${projectId}?symbol=${encodeURIComponent(symbolRef)}`
}

/** 全角英数を半角に、大文字にして空白を除く（「３３ＨＢ」でも「33hb」でも見つかるように） */
export function normalizeQuery(text: string) {
  return text
    .replace(/[！-～]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
    .replace(/\s+/g, '')
    .toUpperCase()
}

function deviceText(d: DeviceEntry) {
  return [d.device, ...d.variants, ...d.pages, ...[...d.coils, ...d.contacts, ...d.loads].map((x) => `${x.label} ${x.symbol_ref}`)].join(' ')
}

function wireText(w: WireNumberEntry) {
  return [w.wire_no, ...w.pages, ...w.ends.map((e) => e.label)].join(' ')
}

function Place({
  loc,
  prefix,
  title,
  onLocate,
}: {
  loc: DeviceLocation
  prefix?: string
  title: string
  onLocate: (target: LocateTarget) => void
}) {
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-700 hover:border-cyan-400 hover:bg-cyan-50"
      onClick={() => onLocate({ project_id: loc.project_id, cx: loc.cx, cy: loc.cy, w: loc.w, h: loc.h, label: title })}
      title={`${title}（${loc.symbol_ref}${loc.label ? `・${loc.label}` : ''}）を図面で表示`}
    >
      {prefix && <span className="font-mono font-black text-slate-500">{prefix}</span>}
      <span className="font-bold">{loc.page}</span>
      <span className="text-slate-500">{loc.area}</span>
      {loc.origin === 'ai' && <span className="rounded bg-amber-100 px-1 text-[9px] font-bold text-amber-700" title="AI の候補（人が未確認）">AI</span>}
    </button>
  )
}

function DeviceCard({ d, onLocate }: { d: DeviceEntry; onLocate: (target: LocateTarget) => void }) {
  const holders = d.role === 'load' ? d.loads : d.coils
  return (
    <li className={`rounded-xl border px-2.5 py-2 ${d.notes.length ? 'border-amber-200 bg-amber-50/40' : 'border-slate-200 bg-white'}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="min-w-0 truncate text-xs font-black text-slate-800">{d.device}</span>
        <span className={`rounded px-1.5 text-[10px] font-bold ${ROLE_TONE[d.role]}`}>{d.role_label}</span>
        {d.role !== 'load' && (
          <span className="font-mono text-[10px] text-slate-500" title="選んだ図面で使っている接点の数">
            a {d.a_count} · b {d.b_count}
          </span>
        )}
        {d.timer && (
          <span className="rounded bg-violet-100 px-1.5 text-[10px] font-bold text-violet-700">
            {d.timer.mode_label} {d.timer.delay_s} 秒
          </span>
        )}
      </div>
      {holders.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <span className="w-9 text-[10px] font-bold text-slate-500">{d.role === 'load' ? '本体' : 'コイル'}</span>
          {holders.map((c) => (
            <Place key={`${c.project_id}:${c.symbol_ref}`} loc={c} title={`${d.device} の${d.role === 'load' ? '本体' : 'コイル'}`} onLocate={onLocate} />
          ))}
        </div>
      )}
      {d.contacts.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <span className="w-9 text-[10px] font-bold text-slate-500">接点</span>
          {d.contacts.map((c) => (
            <Place key={`${c.project_id}:${c.symbol_ref}`} loc={c} prefix={c.kind} title={`${d.device} の ${c.kind_label}`} onLocate={onLocate} />
          ))}
        </div>
      )}
      {d.notes.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-[11px] leading-5 text-amber-800">
          {d.notes.map((n) => (
            <li key={n.kind} className="flex gap-1">
              <AlertTriangle size={11} className="mt-1 flex-none" /> {n.message}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

function WireCard({ w, onLocate }: { w: WireNumberEntry; onLocate: (target: LocateTarget) => void }) {
  return (
    <li className={`rounded-xl border px-2.5 py-2 ${w.note ? 'border-amber-200 bg-amber-50/40' : 'border-slate-200 bg-white'}`}>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-xs font-black text-slate-800">{w.wire_no}</span>
        <span className="text-[10px] text-slate-500">{w.connections} 本 · {w.pages.join('、')}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        {w.ends.map((e) => (
          <button
            key={`${e.project_id}:${e.symbol_ref}`}
            type="button"
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] text-slate-700 hover:border-cyan-400 hover:bg-cyan-50"
            onClick={() => onLocate({ project_id: e.project_id, cx: e.cx, cy: e.cy, w: e.w, h: e.h, label: `電線番号 ${w.wire_no}: ${e.label || e.symbol_ref}` })}
            title={`${e.class_label} ${e.label || e.symbol_ref} を図面で表示`}
          >
            <span className="font-bold">{e.label || e.symbol_ref}</span>
            <span className="text-slate-500">{e.page} {e.area}</span>
          </button>
        ))}
      </div>
      {w.note && (
        <p className="mt-1 flex gap-1 text-[11px] leading-5 text-amber-800">
          <AlertTriangle size={11} className="mt-1 flex-none" /> {w.note}
        </p>
      )}
    </li>
  )
}

/**
 * 機器索引（DD-0044）: 器具番号・電線番号で探し、コイルと接点がどのページのどこにあるかを一覧にする。
 * 場所を押すと図面がそこへ移動する。Excel（機器一覧・接点の場所・電線番号・要確認）にも書き出せる。
 */
export default function DevicePanel({ projectIds, onLocate }: { projectIds: number[]; onLocate: (target: LocateTarget) => void }) {
  const [index, setIndex] = useState<DeviceIndex | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [view, setView] = useState<View>('devices')
  const [downloading, setDownloading] = useState(false)
  const key = projectIds.join(',')

  useEffect(() => {
    let alive = true
    setIndex(null)
    api
      .deviceIndex(projectIds)
      .then((r) => alive && (setIndex(r), setError(null)))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const q = normalizeQuery(query)
  const devices = useMemo(() => {
    if (!index) return []
    const list = view === 'notes' ? index.devices.filter((d) => d.notes.length > 0) : index.devices
    return q ? list.filter((d) => normalizeQuery(deviceText(d)).includes(q)) : list
  }, [index, view, q])
  const wires = useMemo(() => {
    if (!index) return []
    const list = view === 'notes' ? index.wire_numbers.filter((w) => w.note) : index.wire_numbers
    return q ? list.filter((w) => normalizeQuery(wireText(w)).includes(q)) : list
  }, [index, view, q])
  const unlabeled = view === 'notes' && index ? index.unlabeled_contacts.filter((c) => !q || normalizeQuery(`${c.page} ${c.symbol_ref}`).includes(q)) : []

  const download = async () => {
    setDownloading(true)
    try {
      await api.downloadDeviceIndex(projectIds)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDownloading(false)
    }
  }

  if (error && !index) return <div className="notice border-rose-200 bg-rose-50 text-rose-700" role="alert">{error}</div>
  if (!index) {
    return (
      <div className="flex items-center gap-2 p-4 text-xs text-slate-500">
        <Loader2 size={14} className="animate-spin" /> 機器を集めています…
      </div>
    )
  }

  const counts: Record<View, number> = {
    devices: index.devices.length,
    wires: index.wire_numbers.length,
    notes: index.counts.notes,
  }
  const showDevices = view !== 'wires'
  const showWires = view !== 'devices'
  const empty = (showDevices ? devices.length : 0) + (showWires ? wires.length : 0) + unlabeled.length === 0

  return (
    <div className="space-y-3">
      <section className="card p-3" aria-label="機器を探す">
        <div className="flex items-center gap-2">
          <label className="relative min-w-0 flex-1">
            <span className="sr-only">器具番号・電線番号・ページで探す</span>
            <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              className="field h-9 min-h-9 w-full py-0 pl-7 text-xs"
              placeholder="器具番号・電線番号・ページで探す"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <button type="button" className="btn btn-sm" onClick={() => void download()} disabled={downloading} title="機器一覧・接点の場所・電線番号・要確認の 4 シート">
            {downloading ? <Loader2 size={12} className="animate-spin" /> : <FileSpreadsheet size={12} />} Excel
          </button>
        </div>
        <div className="mt-2 grid grid-cols-3 gap-1" role="tablist" aria-label="一覧の種類">
          {(Object.keys(VIEW_LABEL) as View[]).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              className={`rounded-lg px-2 py-1 text-[11px] font-bold ${
                view === v ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
              onClick={() => setView(v)}
            >
              {VIEW_LABEL[v]} {counts[v]}
            </button>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          リレー {index.counts.relays} · 外部入力 {index.counts.inputs} · 負荷 {index.counts.loads} · 接点 {index.counts.contacts}
        </p>
        {error && <p className="mt-1 text-[11px] text-rose-700" role="alert">{error}</p>}
      </section>

      {empty && <p className="px-1 text-[11px] text-slate-400">{q ? '見つかりません' : 'ありません'}</p>}

      {showDevices && devices.length > 0 && (
        <ul className="space-y-1.5" aria-label="機器の一覧">
          {devices.slice(0, MAX_ROWS).map((d) => (
            <DeviceCard key={d.device} d={d} onLocate={onLocate} />
          ))}
          {devices.length > MAX_ROWS && <li className="px-1 text-[11px] text-slate-500">ほか {devices.length - MAX_ROWS} 件。検索で絞り込んでください</li>}
        </ul>
      )}

      {unlabeled.length > 0 && (
        <section className="card p-3" aria-label="器具番号の無い接点">
          <div className="mb-1 text-xs font-black">器具番号の無い接点（{unlabeled.length}）</div>
          <p className="mb-1.5 text-[11px] text-slate-500">どのリレーの接点か分からないため、外部入力として扱っています。</p>
          <div className="flex flex-wrap gap-1">
            {unlabeled.map((c) => (
              <span key={`${c.project_id}:${c.symbol_ref}`} className="inline-flex items-center gap-1">
                <Place loc={c} prefix={c.kind} title={`器具番号の無い ${c.kind_label}`} onLocate={onLocate} />
                <Link className="text-[10px] font-bold text-cyan-700 underline" to={editorLink(c.project_id, c.symbol_ref)}>
                  編集
                </Link>
              </span>
            ))}
          </div>
        </section>
      )}

      {showWires && wires.length > 0 && (
        <ul className="space-y-1.5" aria-label="電線番号の一覧">
          {wires.slice(0, MAX_ROWS).map((w) => (
            <WireCard key={w.wire_no} w={w} onLocate={onLocate} />
          ))}
          {wires.length > MAX_ROWS && <li className="px-1 text-[11px] text-slate-500">ほか {wires.length - MAX_ROWS} 件。検索で絞り込んでください</li>}
        </ul>
      )}

      <p className="flex items-center gap-1 px-1 text-[10px] text-slate-400">
        <MapPin size={10} /> 場所を押すと図面がそこへ移動します。位置は図面を 3×3 に分けた目安です。
      </p>
    </div>
  )
}
