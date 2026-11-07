import { useEffect, useId, useState } from 'react'
import { Loader2, ScanText } from 'lucide-react'

import { api } from '../../api/client'
import type { ProjectDetail, SheetNumberCandidate } from '../../types'
import { buildMetaForm } from './model'
import type { MetaForm, SaveStatus } from './model'
import { ReviewSummaryCard } from './ReviewSummaryCard'

const CANDIDATE_REGION_LABEL: Record<SheetNumberCandidate['region'], string> = {
  title_block: '表題欄',
  margin: '下端の余白',
}

export function MetaPanel({
  project,
  visible,
  saveStatus,
  annotationSaveStatus,
  onQueueSave,
}: {
  project: ProjectDetail
  visible: boolean
  saveStatus: SaveStatus
  annotationSaveStatus: SaveStatus
  onQueueSave: (projectId: number, payload: MetaForm, immediate?: boolean) => void
}) {
  const initialForm = buildMetaForm(project)
  const [form, setForm] = useState(initialForm)
  const sheetNoId = useId()
  const [sheetCandidates, setSheetCandidates] = useState<SheetNumberCandidate[] | null>(null)
  const [sheetReading, setSheetReading] = useState(false)
  const [sheetError, setSheetError] = useState<string | null>(null)

  useEffect(() => {
    onQueueSave(project.id, form)
  }, [form, onQueueSave, project.id])

  const saveMetaNow = () => onQueueSave(project.id, form, true)

  const suggestSheetNumber = async () => {
    setSheetReading(true)
    setSheetCandidates(null)
    setSheetError(null)
    try {
      const { candidates } = await api.suggestSheetNumber(project.id)
      setSheetCandidates(candidates)
    } catch (caught) {
      setSheetError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSheetReading(false)
    }
  }

  const renderField = (label: string, k: keyof typeof form) => (
    <label className="block">
      <span className="mb-1 block text-[11px] text-slate-500">{label}</span>
      <input
        className="field"
        value={form[k]}
        onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
      />
    </label>
  )

  return (
    <div className="thin-scroll flex-1 space-y-3 overflow-auto p-4">
      <ReviewSummaryCard
        projectId={project.id}
        visible={visible}
        serverSynced={saveStatus === 'saved' && annotationSaveStatus === 'saved'}
      />
      {renderField('名称', 'name')}
      <div>
        <label htmlFor={sheetNoId} className="mb-1 block text-[11px] text-slate-500">
          シート番号
        </label>
        <div className="flex gap-2">
          <input
            id={sheetNoId}
            className="field"
            value={form.sheet_no}
            onChange={(e) => setForm((f) => ({ ...f, sheet_no: e.target.value }))}
          />
          <button
            type="button"
            className="btn flex-none px-3 text-xs"
            onClick={() => void suggestSheetNumber()}
            disabled={sheetReading}
            aria-busy={sheetReading}
            title="図面右下の表題欄と下端の余白の文字を読み取り、シート番号の候補を出します"
          >
            {sheetReading ? (
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            ) : (
              <ScanText size={14} aria-hidden="true" />
            )}
            {sheetReading ? '読取中…' : 'AIで読取'}
          </button>
        </div>
        {sheetError && (
          <p role="alert" className="mt-1.5 text-[11px] leading-relaxed text-rose-700">
            シート番号を読み取れませんでした: {sheetError}
          </p>
        )}
        {sheetCandidates?.length === 0 && (
          <p role="status" className="mt-1.5 text-[11px] text-slate-500">
            候補が見つかりませんでした。手入力してください。
          </p>
        )}
        {sheetCandidates && sheetCandidates.length > 0 && (
          <div className="mt-1.5">
            <p className="mb-1 text-[10px] text-slate-500">候補を押すとシート番号に入力されます</p>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="シート番号の候補">
              {sheetCandidates.map((candidate) => {
                const percent = Math.round(candidate.score * 100)
                const chosen = form.sheet_no === candidate.text
                return (
                  <button
                    key={candidate.text}
                    type="button"
                    className={`inline-flex min-h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-bold transition ${
                      chosen
                        ? 'border-cyan-500 bg-cyan-50 text-cyan-800'
                        : 'border-slate-200 bg-white text-slate-700 hover:border-cyan-300 hover:bg-cyan-50/60'
                    }`}
                    aria-pressed={chosen}
                    title={`${CANDIDATE_REGION_LABEL[candidate.region]}で検出（信頼度 ${percent}%）`}
                    onClick={() => setForm((f) => ({ ...f, sheet_no: candidate.text }))}
                  >
                    <span className="font-mono">{candidate.text}</span>
                    <span className="font-mono text-[10px] font-semibold text-slate-500">{percent}%</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        {renderField('頁', 'page_no')}
        {renderField('改訂', 'revision')}
      </div>
      <label className="block">
        <span className="mb-1 block text-[11px] text-slate-500">状態</span>
        <select
          className="field"
          value={form.status}
          onChange={(e) => setForm((f) => ({ ...f, status: e.target.value }))}
        >
          <option value="draft">作業中</option>
          <option value="review">レビュー待ち</option>
          <option value="done">完了</option>
        </select>
      </label>
      {renderField('担当', 'assignee')}
      <label className="block">
        <span className="mb-1 block text-[11px] text-slate-500">メモ</span>
        <textarea
          className="field h-24"
          value={form.note}
          onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
        />
      </label>
      <button
        className={`btn w-full justify-center ${saveStatus === 'error' ? 'btn-danger' : 'btn-primary'}`}
        onClick={saveMetaNow}
      >
        {saveStatus === 'saving'
          ? '図面情報を自動保存中…'
          : saveStatus === 'pending'
            ? '図面情報に変更あり'
            : saveStatus === 'error'
              ? '図面情報の保存を再試行'
              : '図面情報は自動保存済み'}
      </button>
      <div className="rounded-md bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-500">
        画像: {project.images[0]?.filename ?? '—'}
        <br />
        サイズ: {project.image_width} × {project.image_height} px
        <br />
        sha256: <span className="font-mono">{project.images[0]?.sha256.slice(0, 16) ?? '—'}…</span>
      </div>
    </div>
  )
}
