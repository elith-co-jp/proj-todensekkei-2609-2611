import { AlertTriangle, ArrowLeft, RefreshCw } from 'lucide-react'
import { isRouteErrorResponse, Link, useRouteError } from 'react-router-dom'

/** 画面が落ちたとき・URL が違うときの日本語の画面（React Router の開発者向けの画面の代わり）。 */
export default function ErrorPage({ notFound = false }: { notFound?: boolean }) {
  const error = useRouteError()
  const missing = notFound || (isRouteErrorResponse(error) && error.status === 404)
  const detail = missing ? null : error instanceof Error ? error.message : isRouteErrorResponse(error) ? error.statusText : null
  return (
    <div className="flex min-h-[60dvh] flex-col items-center justify-center gap-4 p-6 text-center" role="alert">
      <AlertTriangle size={36} className="text-amber-500" aria-hidden="true" />
      <h1 className="text-lg font-bold text-slate-900">{missing ? 'ページが見つかりません' : '画面を表示できませんでした'}</h1>
      <p className="max-w-md text-sm text-slate-600">
        {missing
          ? 'URL が正しいか確かめてください。'
          : '思わぬエラーが起きました。保存済みのデータは消えていません。再読み込みするか、図面一覧から開き直してください。'}
      </p>
      {detail && <pre className="max-w-xl whitespace-pre-wrap rounded-lg bg-slate-50 p-2 text-left text-[11px] text-slate-500">{detail}</pre>}
      <div className="flex gap-2">
        <Link to="/" className="btn"><ArrowLeft size={16} /> 図面一覧へ戻る</Link>
        {!missing && (
          <button type="button" className="btn btn-accent" onClick={() => window.location.reload()}>
            <RefreshCw size={16} /> 再読み込み
          </button>
        )}
      </div>
    </div>
  )
}
