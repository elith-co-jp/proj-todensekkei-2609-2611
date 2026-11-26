import { useCallback, useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { AlertTriangle, Trash2 } from 'lucide-react'

import { useModalFocus } from '../hooks/useModalFocus'

/**
 * 削除の確認（DD-0052）。何が一緒に消えて何が残るかを並べ、取り消せないことを伝える。
 * blockers があるときは削除ボタンを押せない（実行中のジョブが使っているなど）。
 */
export function ConfirmDeleteDialog({
  title,
  lead,
  removes,
  keeps = [],
  blockers = [],
  loading = false,
  busy = false,
  error = null,
  confirmLabel = '削除する',
  children,
  onConfirm,
  onCancel,
}: {
  title: string
  lead?: ReactNode
  removes: string[]
  keeps?: string[]
  blockers?: string[]
  loading?: boolean
  busy?: boolean
  error?: string | null
  confirmLabel?: string
  children?: ReactNode
  onConfirm: () => void
  onCancel: () => void
}) {
  const ref = useRef<HTMLElement>(null)
  // useModalFocus は onClose が変わるたびに付け直してフォーカスを戻すので、最新の値は ref で読む
  const latest = useRef({ busy, onCancel })
  useEffect(() => {
    latest.current = { busy, onCancel }
  })
  const close = useCallback(() => {
    if (!latest.current.busy) latest.current.onCancel()
  }, [])
  useModalFocus({ open: true, containerRef: ref, onClose: close })
  const blocked = blockers.length > 0
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-sm">
      <section
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-delete-title"
        tabIndex={-1}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-3xl border border-white/60 bg-white p-6 shadow-2xl"
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-rose-50 text-rose-600">
          <Trash2 size={22} />
        </div>
        <h2 id="confirm-delete-title" className="mt-4 text-lg font-black text-slate-950">{title}</h2>
        {lead && <div className="mt-2 text-sm leading-6 text-slate-600">{lead}</div>}
        {children}
        {loading ? (
          <p className="mt-4 text-sm text-slate-500">消えるデータを数えています…</p>
        ) : (
          <>
            {removes.length > 0 && (
              <div className="mt-4">
                <h3 className="text-xs font-bold text-rose-700">一緒に消えるもの</h3>
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-slate-700" aria-label="一緒に消えるもの">
                  {removes.map((r) => <li key={r}>{r}</li>)}
                </ul>
              </div>
            )}
            {keeps.length > 0 && (
              <div className="mt-3">
                <h3 className="text-xs font-bold text-slate-500">残るもの</h3>
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-slate-500" aria-label="残るもの">
                  {keeps.map((k) => <li key={k}>{k}</li>)}
                </ul>
              </div>
            )}
          </>
        )}
        {blocked && (
          <div className="mt-4 flex gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800" role="alert">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            <div>{blockers.map((b) => <p key={b}>{b}</p>)}</div>
          </div>
        )}
        <p className="mt-4 text-xs text-slate-500">この操作は取り消せません。</p>
        {error && (
          <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700" role="alert">
            {error}
          </div>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" data-autofocus className="btn" onClick={onCancel} disabled={busy}>キャンセル</button>
          <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={busy || loading || blocked}>
            <Trash2 size={15} /> {busy ? '削除中…' : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  )
}
