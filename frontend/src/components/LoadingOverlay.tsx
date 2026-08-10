import { LogoMark } from './Logo'

/** 画面全体を覆うローディング表示。時間のかかる処理（アップロード等）の完了まで出す。 */
export function LoadingOverlay({ message, hint }: { message: string; hint?: string }) {
  return (
    <div
      className="fixed inset-0 z-[70] flex flex-col items-center justify-center gap-5 bg-white/85 px-6 text-center backdrop-blur-sm"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="relative flex h-24 w-24 items-center justify-center">
        <span className="brand-gradient absolute inset-0 animate-ping rounded-full opacity-20" />
        <LogoMark size={56} className="animate-pulse" />
      </div>
      <div className="text-sm font-bold text-slate-800">{message}</div>
      <div className="text-xs text-slate-500">{hint ?? '完了までしばらくお待ちください…'}</div>
    </div>
  )
}
