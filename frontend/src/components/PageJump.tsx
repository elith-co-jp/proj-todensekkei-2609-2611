import { useNavigate } from 'react-router-dom'

import { getProjectNavigation, projectIdAt } from '../utils/workspace'

const TONE_CLASS = {
  dark: {
    input:
      'border-white/10 bg-white/[0.06] text-white focus:border-cyan-400 focus:ring-cyan-400/40',
    total: 'text-slate-400',
  },
  light: {
    input: 'border-slate-200 bg-white text-slate-800 focus:border-blue-500 focus:ring-blue-100',
    total: 'text-slate-500',
  },
} as const

export function PageJump({
  projects,
  currentId,
  pathFor,
  tone,
}: {
  projects: ReadonlyArray<{ id: number }>
  currentId: number
  pathFor: (id: number) => string
  tone: keyof typeof TONE_CLASS
}) {
  const navigate = useNavigate()
  const { position, total } = getProjectNavigation(projects, currentId)
  const current = position > 0 ? String(position) : ''
  const toneClass = TONE_CLASS[tone]

  const commit = (input: HTMLInputElement) => {
    /* 全角数字（IME 入力）も受け付ける */
    const value = input.value.normalize('NFKC').trim()
    const target = /^\d+$/.test(value) && Number(value) !== position ? projectIdAt(projects, Number(value)) : null
    if (target === null) {
      input.value = current
      return
    }
    navigate(pathFor(target))
  }

  return (
    <span className="flex items-center gap-1">
      <input
        key={current}
        type="text"
        inputMode="numeric"
        enterKeyHint="go"
        autoComplete="off"
        defaultValue={current}
        placeholder="—"
        aria-label="図面番号を入力して移動"
        title={total > 0 ? `1〜${total} の番号を入力して Enter で移動` : undefined}
        className={`h-8 w-11 rounded-lg border px-1 text-center font-mono text-xs font-bold focus:outline-none focus:ring-2 ${toneClass.input}`}
        onFocus={(event) => event.currentTarget.select()}
        onKeyDown={(event) => {
          if (event.key === 'Escape') event.currentTarget.value = current
          if (event.key === 'Enter' || event.key === 'Escape') event.currentTarget.blur()
        }}
        onBlur={(event) => commit(event.currentTarget)}
      />
      <span className={`whitespace-nowrap font-mono text-[11px] font-bold ${toneClass.total}`}>/ {total || '—'}</span>
    </span>
  )
}
