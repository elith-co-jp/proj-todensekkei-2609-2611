import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Cable,
  CheckCircle2,
  CircleDot,
  CloudCog,
  FileUp,
  Keyboard,
  MousePointer2,
  Sparkles,
  Square,
  Tags,
} from 'lucide-react'

import { api } from '../api/client'
import type { SymbolClass } from '../types'

const WORKFLOW = [
  {
    no: '01',
    title: 'PDFを登録してページを選ぶ',
    icon: FileUp,
    steps: [
      '「図面ワークスペース」→「PDF / 図面を登録」からPDFを選びます。複数ファイルもまとめて選択できます。',
      'PDFは自動で1ページずつ画像化され、ページ番号付きの作業行として一覧に追加されます。',
      '作業するページを開き、編集画面上部の前後ボタンで連続してページを移動できます。',
    ],
  },
  {
    no: '02',
    title: 'シンボルを矩形で囲む',
    icon: Square,
    steps: [
      'ツールバーで「シンボル描画」（B）を選び、図面上をドラッグして矩形を作ります。',
      '描画前にシンボル種別を選びます。数字キー1〜9でも種別を切り替えられます。',
      '「選択・移動」（V）では、矩形の移動と四隅ハンドルでのリサイズができます。詳細パネルは右上のボタンで開閉できます。',
    ],
  },
  {
    no: '03',
    title: '端子を置く（必要な機器のみ）',
    icon: CircleDot,
    steps: [
      '「端子を置く」（T）を選び、シンボルの内側をクリックすると端子が追加されます。',
      '端子名は右パネルのシンボル欄で編集します。',
      '端子を置かない場合は、配線をシンボル単位で登録できます。',
    ],
  },
  {
    no: '04',
    title: '配線（from-to）を登録する',
    icon: Cable,
    steps: [
      '「配線 (from-to)」（C）を選び、始点→終点の順にクリックします。',
      '端子を選ぶと端子単位、矩形の内側を選ぶとシンボル単位で接続されます。',
      '右パネルの「配線」で電線番号・種別・シート間参照先を入力します。Escで始点を取り消せます。',
    ],
  },
  {
    no: '05',
    title: '自動保存を確認して出力する',
    icon: CheckCircle2,
    steps: [
      '編集が止まってから約0.4秒後に自動保存されます。右上が「自動保存済み」になれば完了です。',
      'Ctrl+S（MacはCmd+S）を押すと、待たずに即時保存できます。',
      '「データ受け渡し」から、シンボル・端子・配線情報を含むZIPを出力できます。',
    ],
  },
]

const SHORTCUTS: [string, string][] = [
  ['V', '選択・移動モードに切り替え'],
  ['B', 'シンボル描画モードに切り替え'],
  ['T', '端子を置くモードに切り替え'],
  ['C', '配線 from-to モードに切り替え'],
  ['1〜9', 'シンボル種別を番号で切り替え'],
  ['Delete / Backspace', '選択中のシンボル・端子・配線を削除'],
  ['Esc', '選択解除、または配線の始点指定を取り消し'],
  ['Ctrl+S / Cmd+S', '変更内容をすぐに保存'],
  ['Ctrl+Z', '直前の操作を元に戻す'],
  ['Ctrl+Shift+Z', '取り消した操作をやり直す'],
  ['図面上のホイール', '図面表示を拡大・縮小'],
  ['Alt+ドラッグ', '図面の表示位置を移動'],
]

function ScreenFrame({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <figure className="overflow-hidden rounded-xl border border-slate-300 bg-white shadow-sm">
      <div className="flex items-center gap-1.5 border-b border-slate-200 bg-slate-100 px-3 py-2">
        <span className="h-2.5 w-2.5 rounded-full bg-rose-300" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-300" />
        <div className="mx-auto h-5 w-1/2 rounded bg-white" />
      </div>
      {children}
      <figcaption className="border-t border-slate-200 bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-500">
        {caption}
      </figcaption>
    </figure>
  )
}

function UploadVisual() {
  return (
    <ScreenFrame caption="PDFを登録すると、ページごとの作業行が作成されます。">
      <div className="p-4">
        <div className="flex items-center gap-3">
          <div>
            <div className="text-sm font-bold">図面一覧</div>
            <div className="mt-0.5 text-[9px] text-slate-400">PDFはページ単位で自動分割</div>
          </div>
          <div className="flex-1" />
          <div className="flex items-center gap-1.5 rounded-md bg-koa-500 px-3 py-2 text-[10px] font-bold text-white">
            <FileUp size={13} /> PDF / 図面を登録
          </div>
        </div>
        <div className="mt-4 overflow-hidden rounded-lg border border-slate-200">
          <div className="grid grid-cols-[48px_1fr_52px_60px] bg-slate-800 px-3 py-2 text-[9px] font-bold text-white">
            <span>ID</span><span>名称</span><span>頁</span><span />
          </div>
          {[1, 2, 3].map((page) => (
            <div key={page} className="grid grid-cols-[48px_1fr_52px_60px] items-center border-t border-slate-100 px-3 py-2 text-[9px]">
              <span>{page}</span><span className="font-semibold text-koa-600">登録PDF - {String(page).padStart(3, '0')}</span><span>{page}</span>
              <span className="rounded border border-slate-200 px-2 py-1 text-center font-bold">編集 →</span>
            </div>
          ))}
        </div>
      </div>
    </ScreenFrame>
  )
}

function EditorVisual() {
  return (
    <ScreenFrame caption="編集画面では、シンボル・端子・配線を同じ図面上で登録します。">
      <div className="border-b border-slate-200 p-2">
        <div className="flex flex-wrap items-center gap-1.5 text-[9px]">
          <span className="flex items-center gap-1 rounded-full border px-2 py-1"><MousePointer2 size={10} /> 選択・移動 V</span>
          <span className="flex items-center gap-1 rounded-full bg-koa-500 px-2 py-1 font-bold text-white"><Square size={10} /> シンボル描画 B</span>
          <span className="flex items-center gap-1 rounded-full border px-2 py-1"><CircleDot size={10} /> 端子 T</span>
          <span className="flex items-center gap-1 rounded-full border px-2 py-1"><Cable size={10} /> 配線 C</span>
          <span className="flex-1" />
          <span className="rounded bg-koa-green-100 px-2 py-1 font-bold text-koa-green-700">自動保存済み</span>
        </div>
      </div>
      <div className="grid grid-cols-[1fr_120px]">
        <div
          className="relative h-48 overflow-hidden bg-slate-100"
          style={{
            backgroundImage: 'radial-gradient(#cbd5e1 1px, transparent 1px)',
            backgroundSize: '14px 14px',
          }}
        >
          <div className="absolute left-[18%] top-[28%] h-14 w-24 rounded border-2 border-koa-500 bg-koa-100/70">
            <span className="absolute -top-4 left-0 text-[9px] font-bold text-koa-600">SYM-0001</span>
            <span className="absolute -right-1 -top-1 h-2 w-2 bg-white ring-1 ring-koa-500" />
            <span className="absolute -bottom-1 -left-1 h-2 w-2 bg-white ring-1 ring-koa-500" />
          </div>
          <div className="absolute right-[18%] top-[45%] h-12 w-20 rounded border-2 border-violet-500 bg-violet-100/70">
            <span className="absolute -top-4 left-0 text-[9px] font-bold text-violet-600">SYM-0002</span>
          </div>
          <svg className="absolute inset-0 h-full w-full" aria-hidden="true">
            <line x1="42%" y1="43%" x2="70%" y2="54%" stroke="#7c3aed" strokeWidth="2" />
          </svg>
          <div className="absolute bottom-2 left-2 rounded bg-white/90 px-2 py-1 text-[8px] text-slate-500">
            説明用の図形
          </div>
        </div>
        <div className="border-l border-slate-200 bg-white p-2 text-[9px]">
          <div className="border-b border-koa-green-500 pb-2 text-center font-bold">シンボル (2)</div>
          <div className="mt-2 rounded bg-koa-50 p-2 font-mono">SYM-0001</div>
          <div className="mt-1 rounded bg-slate-50 p-2 font-mono">SYM-0002</div>
        </div>
      </div>
    </ScreenFrame>
  )
}

function SaveAndZoomVisual() {
  return (
    <ScreenFrame caption="自動保存の状態と、図面表示の拡大・縮小を確認します。">
      <div className="space-y-3 p-4">
        <div className="flex items-center gap-3 rounded-lg border border-koa-green-200 bg-koa-green-50 p-3">
          <CloudCog className="text-koa-green-700" size={20} />
          <div><div className="text-xs font-bold text-koa-green-800">自動保存済み</div><div className="text-[9px] text-koa-green-700">編集停止後、すぐにサーバーへ反映</div></div>
          <CheckCircle2 className="ml-auto text-koa-green-600" size={18} />
        </div>
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 p-3 text-[10px]">
          <span className="font-bold text-slate-600">図面表示</span>
          <span className="rounded border px-2 py-1">−</span><span className="rounded border px-2 py-1">全体</span><span className="rounded border px-2 py-1">＋</span>
          <span className="font-mono text-slate-500">85%</span>
          <span className="ml-auto rounded bg-slate-100 px-2 py-1 text-slate-500">システムUIは固定</span>
        </div>
      </div>
    </ScreenFrame>
  )
}

export default function GuidePage() {
  const [classes, setClasses] = useState<SymbolClass[]>([])
  const [classError, setClassError] = useState<string | null>(null)
  const sortedClasses = useMemo(
    () => [...classes].sort((a, b) => a.yolo_index - b.yolo_index),
    [classes],
  )

  useEffect(() => {
    let active = true
    api
      .listClasses()
      .then((nextClasses) => {
        if (!active) return
        setClasses(nextClasses)
        setClassError(null)
      })
      .catch((caught) => {
        if (!active) return
        setClassError(caught instanceof Error ? caught.message : String(caught))
      })
    return () => {
      active = false
    }
  }, [])

  return (
    <div className="page-shell enter-up">
      <header className="page-header">
        <div>
          <div className="eyebrow">Field guide</div>
          <h1 className="page-title">操作ガイド</h1>
          <p className="page-description">
            PDF登録からZIP出力まで、アノテーション作業の流れを順番に確認できます。
          </p>
        </div>
      </header>

      <div className="notice mb-6 border-cyan-200 bg-cyan-50 text-cyan-800">
        <Sparkles size={18} className="mt-0.5 flex-none" />
        <span>このページに実際の図面は含まれていません。表示している図形・名称・番号はすべて操作説明用です。</span>
      </div>

      <section className="mb-5" aria-label="画面イメージ">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Screen images</div>
            <h2 className="text-sm font-black text-slate-950">画面イメージ</h2>
          </div>
        </div>
        <div className="grid gap-4 xl:grid-cols-3">
          <UploadVisual />
          <EditorVisual />
          <SaveAndZoomVisual />
        </div>
      </section>

      <section className="card overflow-hidden">
        <div className="card-head">
          <div>
            <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Workflow</div>
            <h2 className="text-base font-black text-slate-950">作業の流れ</h2>
          </div>
        </div>

        <div className="divide-y divide-slate-100">
          {WORKFLOW.map(({ no, title, icon: Icon, steps }) => (
            <article key={no} className="grid gap-3 px-4 py-4 sm:grid-cols-[3.5rem_2.5rem_1fr] sm:items-start sm:px-5">
              <div className="flex items-center gap-3 sm:block">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-slate-950 text-xs font-black text-cyan-300 shadow-lg shadow-slate-900/10">
                  {no}
                </span>
                <span className="text-sm font-black text-slate-900 sm:hidden">{title}</span>
              </div>
              <span className="hidden h-10 w-10 items-center justify-center rounded-xl bg-cyan-50 text-cyan-700 sm:inline-flex">
                <Icon size={18} />
              </span>
              <div className="min-w-0">
                <h3 className="hidden text-sm font-black text-slate-900 sm:block">{title}</h3>
                <ol className="mt-2 space-y-1.5">
                  {steps.map((step, index) => (
                    <li key={step} className="flex gap-2 text-[13px] leading-6 text-slate-700">
                      <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-slate-100 text-[10px] font-bold text-slate-500">
                        {index + 1}
                      </span>
                      <span>{step}</span>
                    </li>
                  ))}
                </ol>
              </div>
            </article>
          ))}
        </div>
      </section>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section className="card overflow-hidden">
          <div className="card-head">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-100 text-slate-700">
              <Keyboard size={17} />
            </span>
            <div>
              <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Shortcut keys</div>
              <h2 className="text-sm font-black">ショートカットキー</h2>
            </div>
          </div>
          <div className="grid sm:grid-cols-2">
            {SHORTCUTS.map(([key, value]) => (
              <div key={key} className="flex gap-3 border-b border-slate-100 px-4 py-2.5 last:border-0 sm:odd:border-r">
                <span className="flex-none"><span className="kbd">{key}</span></span>
                <span className="text-[12px] leading-5 text-slate-600">{value}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="card overflow-hidden">
          <div className="card-head">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-50 text-amber-700">
              <Tags size={17} />
            </span>
            <div>
              <div className="text-[9px] font-bold uppercase tracking-wider text-slate-400">Symbol types</div>
              <h2 className="text-sm font-black">シンボル種別一覧</h2>
            </div>
          </div>
          <div className="p-4 sm:p-5">
            {classError ? (
              <p className="rounded-md bg-rose-50 px-3 py-2 text-[12px] leading-5 text-rose-700">
                シンボル種別を読み込めませんでした。
              </p>
            ) : sortedClasses.length === 0 ? (
              <p className="text-[12px] leading-5 text-slate-500">シンボル種別を読み込んでいます。</p>
            ) : (
              <div className="grid gap-1.5 sm:grid-cols-2">
                {sortedClasses.map((c) => {
                  const typeNo = c.yolo_index + 1
                  const hasShortcut = typeNo >= 1 && typeNo <= 9
                  return (
                    <span
                      key={c.key}
                      className="inline-flex min-h-8 items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-bold text-slate-700"
                    >
                      <span
                        className={`flex h-5 min-w-5 items-center justify-center rounded-md px-1 font-mono text-[10px] ${
                          hasShortcut ? 'bg-slate-950 text-cyan-300' : 'bg-slate-200 text-slate-600'
                        }`}
                      >
                        {typeNo}
                      </span>
                      <span className="h-2.5 w-2.5 rounded-sm" style={{ background: c.color }} />
                      <span>{c.label}</span>
                    </span>
                  )
                })}
              </div>
            )}
            <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
              番号は編集画面上部のシンボル種別の並びです。1〜9はショートカットキーでも切り替えできます。
              10番以降はシンボル種別のプルダウンから選択してください。
            </p>
          </div>
        </section>
      </div>
    </div>
  )
}
