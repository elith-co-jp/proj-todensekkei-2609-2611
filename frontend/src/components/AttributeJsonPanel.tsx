import { useRef, useState } from 'react'
import { Braces, Download, Upload } from 'lucide-react'

import { api } from '../api/client'
import type { AttributeImportReport } from '../types'

/** 記号・配線の属性を、オブジェクト ID と 1 対 1 の JSON で書き出し・取り込む（DD-0010）。 */
export function AttributeJsonPanel({ projectIds }: { projectIds: number[] }) {
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [report, setReport] = useState<AttributeImportReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (task: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await task()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const problems = report
    ? [
        ...report.duplicates.map((id) => `${id}: ファイルの中で重複しているため取り込みませんでした`),
        ...report.not_found.map((id) => `${id}: 図面または記号・配線が見つかりません`),
        ...report.type_mismatch.map((id) => `${id}: 記号と配線の種類が合いません`),
        ...report.invalid,
      ]
    : []

  return (
    <section className="card mb-5 overflow-hidden" aria-label="属性 JSON">
      <div className="card-head">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-sky-50 text-sky-700">
          <Braces size={17} />
        </span>
        <div>
          <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Object attributes</div>
          <h2 className="text-sm font-black">記号・配線の属性（JSON）</h2>
        </div>
      </div>
      <div className="space-y-3 p-4 sm:p-5">
        <p className="text-[12px] leading-6 text-slate-600">
          記号と配線の 1 つ 1 つを、オブジェクト ID（例: ER21216/SYM-0001、ER21216/W-0003）と属性（メーカー・型式・長さなど）の組で並べた JSON です。
          入力用は、まだ値の無い標準の項目も空欄（null）で並べます。編集して取り込むと、載っているオブジェクトの属性をその内容で置き換えます。
          {projectIds.length ? ` 対象: 選んだ ${projectIds.length} 枚。` : ' 対象: 図面がありません。'}
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn" disabled={busy || !projectIds.length}
            onClick={() => void run(() => api.exportAttributes(projectIds, false))}>
            <Download size={14} /> 属性を書き出す
          </button>
          <button type="button" className="btn" disabled={busy || !projectIds.length}
            onClick={() => void run(() => api.exportAttributes(projectIds, true))}>
            <Download size={14} /> 入力用（空欄つき）を書き出す
          </button>
          <button type="button" className="btn btn-accent" disabled={busy} onClick={() => fileRef.current?.click()}>
            <Upload size={14} /> 編集した JSON を取り込む
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            aria-label="属性 JSON のファイル"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) void run(async () => setReport(await api.importAttributes(file)))
            }}
          />
        </div>
        {error && <p className="text-[12px] text-rose-700" role="alert">{error}</p>}
        {report && (
          <div className="rounded-xl bg-slate-50 p-3 text-[12px] leading-6 text-slate-700" role="status">
            {report.updated} 件の属性を更新しました（変更なし {report.unchanged} 件）。
            {problems.length > 0 && (
              <ul className="mt-1 list-disc pl-5 text-amber-800">
                {problems.slice(0, 20).map((p) => <li key={p}>{p}</li>)}
                {problems.length > 20 && <li>ほか {problems.length - 20} 件</li>}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
