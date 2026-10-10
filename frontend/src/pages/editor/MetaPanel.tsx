import { useEffect, useState } from 'react'

import type { ProjectDetail } from '../../types'
import { buildMetaForm } from './model'
import type { MetaForm, SaveStatus } from './model'

export function MetaPanel({
  project,
  saveStatus,
  onQueueSave,
}: {
  project: ProjectDetail
  saveStatus: SaveStatus
  onQueueSave: (projectId: number, payload: MetaForm, immediate?: boolean) => void
}) {
  const initialForm = buildMetaForm(project)
  const [form, setForm] = useState(initialForm)

  useEffect(() => {
    onQueueSave(project.id, form)
  }, [form, onQueueSave, project.id])

  const saveMetaNow = () => onQueueSave(project.id, form, true)

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
      {renderField('名称', 'name')}
      {renderField('シート番号', 'sheet_no')}
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
