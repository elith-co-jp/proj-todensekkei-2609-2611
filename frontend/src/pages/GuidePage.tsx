import { Cable, CheckCircle2, CircleDot, CloudCog, FileUp, MousePointer2, Sparkles, Square } from 'lucide-react'

const SECTIONS = [
  {
    no: '01',
    title: 'PDFを登録してページを選ぶ',
    body: [
      '「図面ワークスペース」→「PDF / 図面を登録」からPDFを選びます。複数ファイルもまとめて選択できます。',
      'PDFは自動で1ページずつ画像化され、ページ番号付きの作業行として一覧に追加されます。',
      '作業するページを開き、編集画面上部の前後ボタンで連続してページを移動できます。',
    ],
  },
  {
    no: '02',
    title: 'シンボルを矩形で囲む',
    body: [
      'ツールバーで「シンボル描画」（B）を選び、図面上をドラッグして矩形を作ります。',
      '描画前にクラスを選びます。数字キー1〜9でもクラスを切り替えられます。',
      '「選択・移動」（V）では、矩形の移動と四隅ハンドルでのリサイズができます。詳細パネルは右上のボタンで開閉できます。',
    ],
  },
  {
    no: '03',
    title: '端子を置く（必要な機器のみ）',
    body: [
      '「端子を置く」（T）を選び、シンボルの内側をクリックすると端子が追加されます。',
      '端子名は右パネルのシンボル欄で編集します。',
      '端子を置かない場合は、配線をシンボル単位で登録できます。',
    ],
  },
  {
    no: '04',
    title: '配線（from-to）を登録する',
    body: [
      '「配線 (from-to)」（C）を選び、始点→終点の順にクリックします。',
      '端子の丸を選ぶと端子単位、矩形の内側を選ぶとシンボル単位で接続されます。',
      '右パネルの「配線」で電線番号・種別・シート間参照先を入力します。Escで始点を取り消せます。',
    ],
  },
  {
    no: '05',
    title: '自動保存を確認して出力する',
    body: [
      '編集が止まってから約0.4秒後に自動保存されます。右上が「自動保存済み」になれば完了です。',
      'Ctrl+S（MacはCmd+S）を押すと、待たずに即時保存できます。',
      '「データ受け渡し」から、YOLOデータと配線情報を含むZIPを出力できます。',
    ],
  },
]

const KEYS: [string, string][] = [
  ['V', '選択・移動モード'],
  ['B', 'シンボル描画モード'],
  ['T', '端子を置くモード'],
  ['C', '配線 (from-to) モード'],
  ['1〜9', 'クラスの切り替え'],
  ['Delete / Backspace', '選択中の要素を削除'],
  ['Esc', '選択解除・操作の取り消し'],
  ['Ctrl+Z', '元に戻す'],
  ['Ctrl+Shift+Z', 'やり直す'],
  ['Ctrl+S / Cmd+S', '自動保存を即時実行'],
  ['図面上のホイール', '図面だけを拡大・縮小'],
  ['Alt+ドラッグ', '図面の表示位置を移動'],
]

function ScreenFrame({ caption, children }: { caption: string; children: React.ReactNode }) {
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
    <ScreenFrame caption="操作画面イメージ：PDFを登録すると、ページごとの行が作成されます。">
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
    <ScreenFrame caption="操作画面イメージ：説明用の図形のみで、実際の図面は使用していません。">
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
            匿名の操作例（実図面なし）
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
    <ScreenFrame caption="自動保存の表示と、図面専用の拡大・縮小ボタンを確認します。">
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
  return (
    <div className="page-shell enter-up">
      <header className="page-header">
        <div>
          <div className="eyebrow">Field guide</div>
          <h1 className="page-title">アノテーション操作ガイド</h1>
          <p className="page-description">匿名の操作画面イメージで、ページ登録から自動保存まで順番に説明します。</p>
        </div>
      </header>

      <div className="notice mb-6 border-cyan-200 bg-cyan-50 text-cyan-800">
        <Sparkles size={18} className="mt-0.5 flex-none" />
        <span>この手順ページに実際の図面は含まれていません。表示している図形・名称・番号はすべて操作説明用です。</span>
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <UploadVisual />
        <EditorVisual />
        <SaveAndZoomVisual />
      </div>

      <div className="mt-7 grid gap-5 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-4">
          {SECTIONS.map((section) => (
            <section key={section.no} className="card overflow-hidden">
              <div className="flex items-center gap-3 border-b border-slate-200 px-4 py-3">
                <span className="flex h-9 w-9 flex-none items-center justify-center rounded-xl bg-slate-900 text-xs font-black text-cyan-300 shadow-lg shadow-slate-900/15">
                  {section.no}
                </span>
                <h2 className="text-sm font-bold">{section.title}</h2>
              </div>
              <ol className="space-y-2 p-4">
                {section.body.map((body, index) => (
                  <li key={body} className="flex gap-2 text-[13px] leading-relaxed text-slate-700">
                    <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-slate-100 text-[10px] font-bold text-slate-500">{index + 1}</span>
                    {body}
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>

        <div className="space-y-5">
          <div className="card overflow-hidden">
            <div className="card-head"><h2 className="text-sm font-bold">キーボード操作</h2></div>
            <table className="w-full">
              <tbody>
                {KEYS.map(([key, value]) => (
                  <tr key={key}><td className="td w-44"><span className="kbd">{key}</span></td><td className="td text-slate-600">{value}</td></tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="card-head"><h2 className="text-sm font-bold">データの受け渡し</h2></div>
            <div className="space-y-2 p-4 text-[12px] leading-relaxed text-slate-700">
              <p>担当分を選んでZIPへエクスポートし、取りまとめ環境でインポートすると、シンボル・端子・配線・図面情報を復元できます。</p>
              <p className="rounded-md bg-amber-50 px-3 py-2 text-amber-800">同じZIPを複数回取り込むと図面が重複します。インポート前に一覧を確認してください。</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
