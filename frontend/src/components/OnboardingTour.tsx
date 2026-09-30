import { useEffect, useRef, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, CloudCog, FileUp, ScanSearch, X, ZoomIn } from 'lucide-react'

import { LogoMark } from './Logo'

const STEPS = [
  {
    title: 'PDFをページ単位で登録',
    body: '図面一覧の「PDF / 図面を登録」からPDFを選ぶと、全ページが自動で画像化されます。',
    note: '各ページは個別の作業行として追加されます。',
    icon: FileUp,
  },
  {
    title: '4つのモードで入力',
    body: '選択・シンボル・端子・配線を切り替え、図面上でアノテーションします。',
    note: '詳しい操作は「アノテーション手順」でいつでも確認できます。',
    icon: ScanSearch,
  },
  {
    title: '編集内容は自動保存',
    body: '入力や移動が止まるとすぐに保存されます。保存状態は画面右上で確認できます。',
    note: 'Ctrl+S / Cmd+S ですぐに保存を確定することもできます。',
    icon: CloudCog,
  },
  {
    title: '拡大・縮小は図面だけ',
    body: '図面上のホイール、またはキャンバス左下の − / 全体 / ＋ で図面表示を調整します。',
    note: 'ブラウザ標準の拡大縮小はそのまま利用できます。',
    icon: ZoomIn,
  },
] as const

export function OnboardingTour({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState(0)
  const dialogRef = useRef<HTMLElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!open) return
    setStep(0)
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.requestAnimationFrame(() => closeButtonRef.current?.focus())
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
      previouslyFocused?.focus()
    }
  }, [open, onClose])

  if (!open) return null

  const current = STEPS[step]
  const Icon = current.icon
  const isLast = step === STEPS.length - 1

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4 backdrop-blur-sm">
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        className="w-full max-w-xl overflow-hidden rounded-2xl border border-white/20 bg-white shadow-2xl"
      >
        <div className="brand-gradient h-1 w-full" />
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div className="flex items-center gap-3">
            <LogoMark size={34} />
            <div>
              <div className="text-[10px] font-bold tracking-[0.18em] text-koa-500">QUICK TOUR</div>
              <h2 id="tour-title" className="mt-0.5 text-base font-bold text-slate-900">
                TodenYOLOへようこそ
              </h2>
            </div>
          </div>
          <button ref={closeButtonRef} className="btn btn-sm" onClick={onClose} aria-label="ツアーを閉じる">
            <X size={15} />
          </button>
        </div>

        <div className="p-6">
          <div className="mb-5 flex items-center gap-2" aria-label={`${step + 1} / ${STEPS.length}`}>
            {STEPS.map((item, index) => (
              <div
                key={item.title}
                className={`h-1.5 flex-1 rounded-full transition-colors ${
                  index <= step ? 'bg-koa-green-500' : 'bg-slate-200'
                }`}
              />
            ))}
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 text-center">
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-koa-600 text-white shadow-lg shadow-koa-200">
              <Icon size={30} />
            </div>
            <div className="mt-5 text-xs font-bold text-koa-600">
              STEP {String(step + 1).padStart(2, '0')}
            </div>
            <h3 className="mt-1 text-xl font-bold text-slate-900">{current.title}</h3>
            <p className="mx-auto mt-3 max-w-md text-sm leading-7 text-slate-600">{current.body}</p>
            <div className="mx-auto mt-4 flex max-w-md items-start gap-2 rounded-lg bg-white px-4 py-3 text-left text-xs leading-relaxed text-slate-600 shadow-sm">
              <Check className="mt-0.5 flex-none text-koa-green-600" size={15} />
              {current.note}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 border-t border-slate-200 bg-slate-50 px-5 py-4">
          <button className="btn" onClick={onClose}>後で見る</button>
          <div className="flex-1 text-center text-xs text-slate-400">
            {step + 1} / {STEPS.length}
          </div>
          {step > 0 && (
            <button className="btn" onClick={() => setStep((value) => value - 1)}>
              <ChevronLeft size={15} /> 戻る
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={() => {
              if (isLast) onClose()
              else setStep((value) => value + 1)
            }}
          >
            {isLast ? 'ツアーを完了' : '次へ'}
            {!isLast && <ChevronRight size={15} />}
          </button>
        </div>
      </section>
    </div>
  )
}
