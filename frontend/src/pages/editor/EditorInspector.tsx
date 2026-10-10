import { Eye, EyeOff, PanelRightClose, Trash2 } from 'lucide-react'

import type { Connection, ProjectDetail, SymbolBox, SymbolClass } from '../../types'
import { INSPECTOR_TABS } from './model'
import type { InspectorTab, MetaForm, SaveStatus } from './model'
import { MetaPanel } from './MetaPanel'

export function EditorInspector({
  inspectorRef,
  desktopInspector,
  onClose,
  tab,
  onTabChange,
  symbols,
  connections,
  classes,
  colorOf,
  labelOf,
  hiddenSymbols,
  selectedRef,
  onSelectSymbol,
  onSetSymbolHidden,
  onDeleteSymbol,
  pushHistory,
  setSymbols,
  setConnections,
  selectedConn,
  onSelectConnection,
  project,
  metaSaveStatus,
  onQueueMetaSave,
}: {
  inspectorRef: React.RefObject<HTMLElement | null>
  desktopInspector: boolean
  onClose: () => void
  tab: InspectorTab
  onTabChange: (tab: InspectorTab) => void
  symbols: SymbolBox[]
  connections: Connection[]
  classes: SymbolClass[]
  colorOf: (key: string) => string
  labelOf: (key: string) => string
  hiddenSymbols: ReadonlySet<string>
  selectedRef: string | null
  onSelectSymbol: (ref: string) => void
  onSetSymbolHidden: (ref: string, hidden: boolean) => void
  onDeleteSymbol: (ref: string) => void
  pushHistory: () => void
  setSymbols: React.Dispatch<React.SetStateAction<SymbolBox[]>>
  setConnections: React.Dispatch<React.SetStateAction<Connection[]>>
  selectedConn: number | null
  onSelectConnection: (index: number) => void
  project: ProjectDetail
  metaSaveStatus: SaveStatus
  onQueueMetaSave: (projectId: number, payload: MetaForm, immediate?: boolean) => void
}) {
  return (
    <>
      <button
        type="button"
        className="absolute inset-0 z-20 bg-slate-950/55 backdrop-blur-[2px] lg:hidden"
        onClick={onClose}
        aria-label="詳細パネルを閉じる"
      />
      <aside
        ref={inspectorRef}
        id="annotation-inspector"
        className="absolute bottom-3 right-3 top-3 z-30 flex w-[min(420px,calc(100%-1.5rem))] flex-none flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-2xl shadow-slate-950/25 lg:static lg:z-auto lg:w-[390px] lg:rounded-none lg:border-y-0 lg:border-r-0 lg:shadow-xl"
        aria-label="アノテーション詳細"
        role={desktopInspector ? undefined : 'dialog'}
        aria-modal={desktopInspector ? undefined : true}
        tabIndex={-1}
      >
        <div className="flex min-h-12 items-center gap-3 border-b border-slate-100 px-4">
          <div className="min-w-0 flex-1">
            <div className="text-[9px] font-bold uppercase tracking-[0.16em] text-cyan-700">Inspector</div>
            <div className="truncate text-xs font-bold text-slate-800">選択内容と図面情報</div>
          </div>
          <button
            type="button"
            data-autofocus={!desktopInspector ? true : undefined}
            className="icon-button h-9 w-9 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
            onClick={onClose}
            aria-label="詳細パネルを閉じる"
          >
            <PanelRightClose size={18} />
          </button>
        </div>
        <div className="flex flex-none border-b border-slate-200" role="tablist" aria-label="詳細表示">
          {INSPECTOR_TABS.map(([key, baseLabel]) => {
            const label = key === 'symbols' ? `${baseLabel} (${symbols.length})` : key === 'connections' ? `${baseLabel} (${connections.length})` : baseLabel
            return (
              <button
                key={key}
                onClick={() => onTabChange(key)}
                onKeyDown={(event) => {
                  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
                  event.preventDefault()
                  const currentIndex = INSPECTOR_TABS.findIndex(([itemKey]) => itemKey === key)
                  const nextIndex =
                    event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? INSPECTOR_TABS.length - 1
                        : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + INSPECTOR_TABS.length) % INSPECTOR_TABS.length
                  const nextKey = INSPECTOR_TABS[nextIndex][0]
                  onTabChange(nextKey)
                  window.requestAnimationFrame(() => document.getElementById(`inspector-tab-${nextKey}`)?.focus())
                }}
                id={`inspector-tab-${key}`}
                role="tab"
                aria-selected={tab === key}
                aria-controls={`inspector-panel-${key}`}
                tabIndex={tab === key ? 0 : -1}
                className={`flex-1 border-b-2 py-2.5 text-xs font-bold transition ${
                  tab === key
                    ? 'border-cyan-500 text-slate-900'
                    : 'border-transparent text-slate-400 hover:text-slate-600'
                }`}
              >
                {label}
              </button>
            )
          })}
        </div>

        {tab === 'symbols' && (
          <div
            id="inspector-panel-symbols"
            className="thin-scroll flex-1 overflow-auto"
            role="tabpanel"
            aria-labelledby="inspector-tab-symbols"
          >
            {symbols.length === 0 && (
              <p className="p-6 text-center text-xs leading-relaxed text-slate-400">
                <span className="kbd">B</span> でシンボル描画モードにして
                <br />
                図面上をドラッグしてください
              </p>
            )}
            {symbols.map((s) => {
              const symbolHidden = hiddenSymbols.has(s.ref)
              return (
                <div
                  key={s.ref}
                  className={`border-b border-slate-100 px-3 py-2.5 ${
                    selectedRef === s.ref ? 'bg-koa-50 shadow-[inset_3px_0_0_#0055a4]' : 'hover:bg-slate-50'
                  } ${symbolHidden ? 'opacity-55' : ''}`}
                >
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="flex min-h-8 min-w-0 flex-1 items-center gap-2 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
                      onClick={() => {
                        if (symbolHidden) onSetSymbolHidden(s.ref, false)
                        onSelectSymbol(s.ref)
                      }}
                      aria-expanded={selectedRef === s.ref}
                    >
                      <span className="h-3 w-3 flex-none rounded-sm" style={{ background: colorOf(s.class_key) }} />
                      <span className="font-mono text-xs font-bold">{s.ref}</span>
                      {s.origin === 'inference' && (
                        <span className="flex-none rounded-full bg-cyan-100 px-1.5 py-0.5 text-[9px] font-black text-cyan-700">
                          AI{s.confidence != null ? ` ${Math.round(s.confidence * 100)}%` : ''}
                        </span>
                      )}
                      <span className="truncate text-[11px] text-slate-500">{labelOf(s.class_key)}</span>
                    </button>
                    <button
                      type="button"
                      className="icon-button h-8 w-8 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                      onClick={() => onSetSymbolHidden(s.ref, !symbolHidden)}
                      aria-pressed={symbolHidden}
                      aria-label={symbolHidden ? `${s.ref}を表示` : `${s.ref}を非表示`}
                      title={symbolHidden ? `${s.ref}を表示` : `${s.ref}を非表示`}
                    >
                      {symbolHidden ? <EyeOff size={13} /> : <Eye size={13} />}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      onClick={() => onDeleteSymbol(s.ref)}
                      aria-label={`${s.ref}を削除`}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                  {selectedRef === s.ref && (
                    <div className="mt-2 space-y-2">
                      <div className="grid grid-cols-2 gap-2">
                        <label className="block min-w-0">
                          <span className="mb-1 block text-[10px] font-bold text-slate-500">種別</span>
                          <select
                            className="field w-full py-1 text-xs"
                            value={s.class_key}
                            onChange={(e) => {
                              pushHistory()
                              const v = e.target.value
                              setSymbols((prev) =>
                                prev.map((x) => (x.ref === s.ref ? { ...x, class_key: v } : x)),
                              )
                            }}
                          >
                            {classes.map((c) => (
                              <option key={c.key} value={c.key}>
                                {c.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="block min-w-0">
                          <span className="mb-1 block text-[10px] font-bold text-slate-500">ラベル</span>
                          <input
                            className="field w-full py-1 text-xs"
                            placeholder="33HB 等"
                            value={s.label ?? ''}
                            onChange={(e) => {
                              const v = e.target.value
                              setSymbols((prev) =>
                                prev.map((x) => (x.ref === s.ref ? { ...x, label: v || null } : x)),
                              )
                            }}
                          />
                        </label>
                      </div>
                      <div className="font-mono text-[10px] text-slate-400">
                        cx {s.cx.toFixed(4)} / cy {s.cy.toFixed(4)} / w {s.w.toFixed(4)} / h {s.h.toFixed(4)}
                      </div>
                      <div>
                        <div className="mb-1 text-[10px] font-bold text-slate-500">
                          端子（<span className="kbd">T</span> モードで図面をクリックして追加）
                        </div>
                        {s.terminals.length === 0 && (
                          <div className="text-[11px] text-slate-400">未登録（配線はシンボル単位で登録されます）</div>
                        )}
                        {s.terminals.map((t) => (
                          <div key={t.ref} className="mb-1 flex items-center gap-1.5">
                            <span className="font-mono text-[10px] text-slate-400">{t.ref.split('-').pop()}</span>
                            <input
                              className="field py-0.5 text-xs"
                              value={t.name}
                              onChange={(e) => {
                                const v = e.target.value
                                setSymbols((prev) =>
                                  prev.map((x) =>
                                    x.ref === s.ref
                                      ? {
                                          ...x,
                                          terminals: x.terminals.map((y) =>
                                            y.ref === t.ref ? { ...y, name: v } : y,
                                          ),
                                        }
                                      : x,
                                  ),
                                )
                              }}
                            />
                            <button
                              className="btn btn-sm btn-danger"
                              onClick={() => {
                                pushHistory()
                                setSymbols((prev) =>
                                  prev.map((x) =>
                                    x.ref === s.ref
                                      ? { ...x, terminals: x.terminals.filter((y) => y.ref !== t.ref) }
                                      : x,
                                  ),
                                )
                                setConnections((prev) =>
                                  prev.filter(
                                    (c) => c.from_terminal_ref !== t.ref && c.to_terminal_ref !== t.ref,
                                  ),
                                )
                              }}
                            >
                              <Trash2 size={11} />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {tab === 'connections' && (
          <div
            id="inspector-panel-connections"
            className="thin-scroll flex-1 overflow-auto"
            role="tabpanel"
            aria-labelledby="inspector-tab-connections"
          >
            {connections.length === 0 && (
              <p className="p-6 text-center text-xs leading-relaxed text-slate-400">
                <span className="kbd">C</span> で配線モードにして
                <br />
                始点シンボル → 終点シンボル の順にクリック
              </p>
            )}
            {connections.map((c, i) => (
              <div
                key={i}
                className={`border-b border-slate-100 px-3 py-2.5 ${
                  selectedConn === i ? 'bg-violet-50 shadow-[inset_3px_0_0_#7c3aed]' : 'hover:bg-slate-50'
                }`}
              >
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="flex min-h-8 min-w-0 flex-1 items-center gap-2 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                    onClick={() => onSelectConnection(i)}
                    aria-expanded={selectedConn === i}
                  >
                    <span className="font-mono text-[11px] font-bold">
                      {c.from_symbol_ref}
                      {c.from_terminal_ref ? `:${c.from_terminal_ref.split('-').pop()}` : ''}
                    </span>
                    <span className="text-slate-400">→</span>
                    <span className="font-mono text-[11px] font-bold">
                      {c.to_symbol_ref}
                      {c.to_terminal_ref ? `:${c.to_terminal_ref.split('-').pop()}` : ''}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-danger"
                    onClick={() => {
                      pushHistory()
                      setConnections((prev) => prev.filter((_, j) => j !== i))
                    }}
                    aria-label={`${c.from_symbol_ref}から${c.to_symbol_ref}への配線を削除`}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
                <div className="mt-1.5 flex gap-1.5">
                  <input
                    className="field py-0.5 text-xs"
                    placeholder="電線番号"
                    value={c.wire_no ?? ''}
                    onChange={(e) => {
                      const v = e.target.value
                      setConnections((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, wire_no: v || null } : x)),
                      )
                    }}
                  />
                  <select
                    className="field w-28 py-0.5 text-xs"
                    value={c.kind}
                    onChange={(e) => {
                      const v = e.target.value as Connection['kind']
                      setConnections((prev) => prev.map((x, j) => (j === i ? { ...x, kind: v } : x)))
                    }}
                  >
                    <option value="wire">配線</option>
                    <option value="sheet_ref">シート間参照</option>
                  </select>
                </div>
                {c.kind === 'sheet_ref' && (
                  <input
                    className="field mt-1.5 py-0.5 text-xs"
                    placeholder="参照先（(610-2F-9) 等）"
                    value={c.external_ref ?? ''}
                    onChange={(e) => {
                      const v = e.target.value
                      setConnections((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, external_ref: v || null } : x)),
                      )
                    }}
                  />
                )}
              </div>
            ))}
          </div>
        )}

        <div
          id="inspector-panel-meta"
          className="flex min-h-0 flex-1"
          role="tabpanel"
          aria-labelledby="inspector-tab-meta"
          hidden={tab !== 'meta'}
        >
          <MetaPanel
            key={project.id}
            project={project}
            saveStatus={metaSaveStatus}
            onQueueSave={onQueueMetaSave}
          />
        </div>
      </aside>
    </>
  )
}
