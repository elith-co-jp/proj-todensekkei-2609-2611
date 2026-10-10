import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useBlocker, useParams } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  CircleDot,
} from 'lucide-react'

import { api } from '../api/client'
import { LoadingOverlay } from '../components/LoadingOverlay'
import { useModalFocus } from '../hooks/useModalFocus'
import type { Connection, ProjectDetail, ProjectRow, SymbolBox, SymbolClass } from '../types'
import {
  clearPersistedProjectSave,
  createProjectScopedSave,
  enqueueProjectSave,
  getProjectNavigation,
  hasVolatileProjectSave,
  isCurrentProject,
  loadPersistedProjectSave,
  persistProjectSave,
  takeNextProjectSave,
} from '../utils/workspace'

import {
  buildAnnotationPayload,
  buildMetaForm,
  clamp01,
  fitInside,
  INFERENCE_CENTER_TOLERANCE,
  INFERENCE_SIZE_TOLERANCE,
  isAnnotationPayload,
  isMetaForm,
  MIN_BOX,
  nextSymbolRef,
  SAVE_STATUS_LABELS,
} from './editor/model'
import { CanvasControls } from './editor/CanvasControls'
import { EditorCanvasSvg } from './editor/EditorCanvasSvg'
import { EditorHeader } from './editor/EditorHeader'
import { EditorInspector } from './editor/EditorInspector'
import { EditorToolbar } from './editor/EditorToolbar'
import { LayerPanel } from './editor/LayerPanel'
import { createSaveQueueStore } from './editor/saveQueue'
import { useEditorShortcuts } from './editor/useEditorShortcuts'
import type {
  AnnotationPayload,
  Corner,
  Drag,
  InspectorTab,
  MetaForm,
  Mode,
  SaveStatus,
  Snapshot,
} from './editor/model'

export default function EditorPage() {
  const { id } = useParams()
  const projectId = Number(id)

  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [projectRows, setProjectRows] = useState<ProjectRow[]>([])
  const [classes, setClasses] = useState<SymbolClass[]>([])
  const [symbols, setSymbols] = useState<SymbolBox[]>([])
  const [connections, setConnections] = useState<Connection[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draftStorageBlocked, setDraftStorageBlocked] = useState(false)

  const [mode, setMode] = useState<Mode>('box')
  const [classKey, setClassKey] = useState<string>('')
  const [selectedRef, setSelectedRef] = useState<string | null>(null)
  const [selectedConn, setSelectedConn] = useState<number | null>(null)
  const [pending, setPending] = useState<{ symbolRef: string; terminalRef: string | null } | null>(null)
  const [tab, setTab] = useState<InspectorTab>('symbols')

  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [drag, setDrag] = useState<Drag>({ kind: 'none' })
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('saved')
  const [metaSaveStatus, setMetaSaveStatus] = useState<SaveStatus>('saved')
  const [exporting, setExporting] = useState(false)
  const [aiApplying, setAiApplying] = useState(false)
  const [shortcutsVisible, setShortcutsVisible] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 1024px)').matches,
  )
  const [desktopInspector, setDesktopInspector] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 1024px)').matches,
  )

  /* レイヤー表示: 非表示シンボルは ref 単位で持ち、クラス/AI検出行は一括操作として扱う */
  const [hiddenSymbols, setHiddenSymbols] = useState<ReadonlySet<string>>(() => new Set())
  const [showTerminals, setShowTerminals] = useState(true)
  const [showConnections, setShowConnections] = useState(true)
  const [layersOpen, setLayersOpen] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 1024px)').matches,
  )

  const undoStack = useRef<Snapshot[]>([])
  const redoStack = useRef<Snapshot[]>([])
  const [histVersion, setHistVersion] = useState(0)
  const canvasRef = useRef<HTMLDivElement | null>(null)
  const inspectorRef = useRef<HTMLElement | null>(null)
  const autosaveTimerRef = useRef<number | null>(null)
  const activePointerIdRef = useRef<number | null>(null)
  const currentProjectIdRef = useRef(projectId)
  /* アノテーション / 図面情報の保存キュー（同型の状態一式をストアで持つ） */
  const annotationSaves = useRef(createSaveQueueStore<AnnotationPayload>()).current
  const metaSaves = useRef(createSaveQueueStore<MetaForm>()).current
  const closeInspector = useCallback(() => setInspectorOpen(false), [])
  currentProjectIdRef.current = projectId
  const navigationBlocker = useBlocker(draftStorageBlocked)

  const refreshDraftStorageBlock = useCallback((targetProjectId: number) => {
    if (targetProjectId !== currentProjectIdRef.current) return
    setDraftStorageBlocked(
      hasVolatileProjectSave('annotations', targetProjectId) || hasVolatileProjectSave('meta', targetProjectId),
    )
  }, [])

  useEffect(() => {
    refreshDraftStorageBlock(projectId)
  }, [projectId, refreshDraftStorageBlock])

  useEffect(() => {
    if (navigationBlocker.state === 'blocked' && !draftStorageBlocked) navigationBlocker.proceed()
  }, [draftStorageBlocked, navigationBlocker])

  useEffect(() => {
    if (!draftStorageBlocked) return
    const preventUnload = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', preventUnload)
    return () => window.removeEventListener('beforeunload', preventUnload)
  }, [draftStorageBlocked])

  useModalFocus({
    open: inspectorOpen && !desktopInspector,
    containerRef: inspectorRef,
    onClose: closeInspector,
  })

  metaSaves.flush = async () => {
    if (metaSaves.inFlight) return
    const queued = takeNextProjectSave(metaSaves.queued)
    if (!queued) return
    if (queued.key === metaSaves.lastSavedKeys.get(queued.projectId)) {
      metaSaves.failed.delete(queued.projectId)
      clearPersistedProjectSave('meta', queued.projectId)
      refreshDraftStorageBlock(queued.projectId)
      if (metaSaves.queued.size > 0) void metaSaves.flush()
      else if (queued.projectId === currentProjectIdRef.current) setMetaSaveStatus('saved')
      return
    }

    metaSaves.inFlight = true
    metaSaves.active = queued
    if (queued.projectId === currentProjectIdRef.current) {
      setMetaSaveStatus('saving')
      setError(null)
    }
    let succeeded = false
    try {
      await api.updateMeta(queued.projectId, queued.payload)
      metaSaves.lastSavedKeys.set(queued.projectId, queued.key)
      metaSaves.lastSavedPayloads.set(queued.projectId, queued.payload)
      metaSaves.generation.set(
        queued.projectId,
        (metaSaves.generation.get(queued.projectId) ?? 0) + 1,
      )
      metaSaves.failed.delete(queued.projectId)
      if (!metaSaves.queued.has(queued.projectId)) {
        clearPersistedProjectSave('meta', queued.projectId)
        refreshDraftStorageBlock(queued.projectId)
      }
      if (queued.projectId === currentProjectIdRef.current) {
        setError(null)
      }
      succeeded = true
    } catch (caught) {
      const hasNewer = metaSaves.queued.has(queued.projectId)
      if (!hasNewer) metaSaves.failed.set(queued.projectId, queued)
      if (queued.projectId === currentProjectIdRef.current) {
        setMetaSaveStatus('error')
        setError(caught instanceof Error ? caught.message : String(caught))
      } else {
        setNotice(`ページ ${queued.projectId} の図面情報を保存できませんでした`)
      }
    } finally {
      metaSaves.inFlight = false
      metaSaves.active = null
      if (metaSaves.queued.size > 0) {
        void metaSaves.flush()
      } else if (succeeded && queued.projectId === currentProjectIdRef.current) {
        setMetaSaveStatus('saved')
      }
    }
  }

  const queueMetaSave = useCallback((targetProjectId: number, payload: MetaForm, immediate = false) => {
    const save = createProjectScopedSave(targetProjectId, payload)
    const queuedSave = metaSaves.queued.get(targetProjectId)
    const activeSave =
      metaSaves.active?.projectId === targetProjectId ? metaSaves.active : null
    const failedSave = metaSaves.failed.get(targetProjectId)
    const latestOutstandingSave = queuedSave ?? activeSave ?? failedSave
    if (save.key === metaSaves.lastSavedKeys.get(targetProjectId) && !activeSave) {
      metaSaves.queued.delete(targetProjectId)
      metaSaves.failed.delete(targetProjectId)
      const timer = metaSaves.timers.get(targetProjectId)
      if (timer !== undefined) window.clearTimeout(timer)
      metaSaves.timers.delete(targetProjectId)
      clearPersistedProjectSave('meta', targetProjectId)
      refreshDraftStorageBlock(targetProjectId)
      if (targetProjectId === currentProjectIdRef.current) {
        setMetaSaveStatus('saved')
        setProject((current) =>
          current?.id === targetProjectId ? { ...current, ...payload } : current,
        )
      }
      return
    }
    if (save.key === latestOutstandingSave?.key && !(immediate && latestOutstandingSave === failedSave)) {
      if (targetProjectId === currentProjectIdRef.current) {
        setMetaSaveStatus(queuedSave ? 'pending' : activeSave ? 'saving' : 'error')
      }
      if (immediate && queuedSave) {
        const timer = metaSaves.timers.get(targetProjectId)
        if (timer !== undefined) window.clearTimeout(timer)
        metaSaves.timers.delete(targetProjectId)
        void metaSaves.flush()
      }
      return
    }
    metaSaves.failed.delete(targetProjectId)
    enqueueProjectSave(metaSaves.queued, save)
    const storedInSession = persistProjectSave('meta', save)
    if (!storedInSession && targetProjectId === currentProjectIdRef.current) {
      setDraftStorageBlocked(true)
    } else if (storedInSession) {
      refreshDraftStorageBlock(targetProjectId)
    }
    if (targetProjectId === currentProjectIdRef.current) {
      setMetaSaveStatus('pending')
      setError(null)
      setProject((current) =>
        current?.id === targetProjectId ? { ...current, ...payload } : current,
      )
    }
    const timer = metaSaves.timers.get(targetProjectId)
    if (timer !== undefined) window.clearTimeout(timer)
    metaSaves.timers.delete(targetProjectId)
    if (immediate) {
      void metaSaves.flush()
      return
    }
    const nextTimer = window.setTimeout(() => {
      metaSaves.timers.delete(targetProjectId)
      void metaSaves.flush()
    }, 400)
    metaSaves.timers.set(targetProjectId, nextTimer)
  }, [refreshDraftStorageBlock])

  const iw = project?.image_width || 1
  const ih = project?.image_height || 1
  const colorOf = useMemo(() => {
    const m = new Map(classes.map((c) => [c.key, c.color]))
    return (key: string) => m.get(key) ?? '#0055a4'
  }, [classes])
  const labelOf = useMemo(() => {
    const m = new Map(classes.map((c) => [c.key, c.label]))
    return (key: string) => m.get(key) ?? key
  }, [classes])
  const symbolByRef = useMemo(() => new Map(symbols.map((s) => [s.ref, s])), [symbols])
  const navigation = useMemo(
    () => getProjectNavigation(projectRows, projectId),
    [projectId, projectRows],
  )
  const visibleSymbols = useMemo(
    () => symbols.filter((s) => !hiddenSymbols.has(s.ref)),
    [symbols, hiddenSymbols],
  )

  /* 作業モードに入ったら対象レイヤーが見える状態に戻す（見えないまま置かせない）。
     モード内での明示的な非表示はそのままにし、モードへの遷移時だけ再表示する */
  useEffect(() => {
    if (mode === 'terminal') setShowTerminals(true)
    if (mode === 'connect') setShowConnections(true)
  }, [mode])

  /* Undo/Redo や外部更新で消えたシンボルの非表示フラグは残さない
     （ref は欠番を再利用するため、残すと新しく描いた枠が見えない・選べない状態になる） */
  useEffect(() => {
    setHiddenSymbols((prev) => {
      if (prev.size === 0) return prev
      const refs = new Set(symbols.map((s) => s.ref))
      const next = new Set<string>()
      prev.forEach((ref) => {
        if (refs.has(ref)) next.add(ref)
      })
      return next.size === prev.size ? prev : next
    })
  }, [symbols])

  /* ------------------------------------------------------------------ 読み込み */
  useEffect(() => {
    let alive = true
    const annotationGenerationAtRequest = annotationSaves.generation.get(projectId) ?? 0
    const metaGenerationAtRequest = metaSaves.generation.get(projectId) ?? 0
    ;(async () => {
      try {
        setError(null)
        setProject(null)
        const [cls, detail, list] = await Promise.all([
          api.listClasses(),
          api.getProject(projectId),
          api.listProjects(),
        ])
        if (!alive) return
        setClasses(cls)
        setProjectRows(list)
        setClassKey((prev) => prev || cls[0]?.key || '')
        const serverMetaKey = JSON.stringify(buildMetaForm(detail))
        const knownMetaKey = metaSaves.lastSavedKeys.get(detail.id)
        const metaSavedDuringRequest =
          (metaSaves.generation.get(detail.id) ?? 0) > metaGenerationAtRequest
        const confirmedMetaPayload =
          metaSavedDuringRequest && knownMetaKey && knownMetaKey !== serverMetaKey
            ? metaSaves.lastSavedPayloads.get(detail.id) ?? null
            : null
        if (!confirmedMetaPayload) {
          metaSaves.lastSavedKeys.set(detail.id, serverMetaKey)
          metaSaves.lastSavedPayloads.delete(detail.id)
        }
        const persistedMetaSave = loadPersistedProjectSave('meta', detail.id, isMetaForm)
        if (persistedMetaSave?.key === serverMetaKey) {
          clearPersistedProjectSave('meta', detail.id)
        } else if (
          persistedMetaSave &&
          !metaSaves.queued.has(detail.id) &&
          metaSaves.active?.projectId !== detail.id &&
          !metaSaves.failed.has(detail.id)
        ) {
          metaSaves.failed.set(detail.id, persistedMetaSave)
        }
        const pendingMetaSave = metaSaves.queued.get(detail.id)
        const activeMetaSave =
          metaSaves.active?.projectId === detail.id ? metaSaves.active : null
        const failedMetaSave = metaSaves.failed.get(detail.id)
        const localMetaPayload =
          pendingMetaSave?.payload ??
          activeMetaSave?.payload ??
          failedMetaSave?.payload ??
          confirmedMetaPayload
        setProject(localMetaPayload ? { ...detail, ...localMetaPayload } : detail)

        const serverAnnotationPayload = buildAnnotationPayload(detail.symbols, detail.connections)
        const serverAnnotationKey = JSON.stringify(serverAnnotationPayload)
        const knownAnnotationKey = annotationSaves.lastSavedKeys.get(detail.id)
        const annotationSavedDuringRequest =
          (annotationSaves.generation.get(detail.id) ?? 0) > annotationGenerationAtRequest
        const confirmedAnnotationPayload =
          annotationSavedDuringRequest && knownAnnotationKey && knownAnnotationKey !== serverAnnotationKey
            ? annotationSaves.lastSavedPayloads.get(detail.id) ?? null
            : null
        if (!confirmedAnnotationPayload) {
          annotationSaves.lastSavedKeys.set(detail.id, serverAnnotationKey)
          annotationSaves.lastSavedPayloads.delete(detail.id)
        }
        const persistedAnnotationSave = loadPersistedProjectSave(
          'annotations',
          detail.id,
          isAnnotationPayload,
        )
        if (persistedAnnotationSave?.key === serverAnnotationKey) {
          clearPersistedProjectSave('annotations', detail.id)
        } else if (
          persistedAnnotationSave &&
          !annotationSaves.queued.has(detail.id) &&
          annotationSaves.active?.projectId !== detail.id &&
          !annotationSaves.failed.has(detail.id)
        ) {
          annotationSaves.failed.set(detail.id, persistedAnnotationSave)
        }
        const pendingSave = annotationSaves.queued.get(detail.id)
        const activeSave = annotationSaves.active?.projectId === detail.id ? annotationSaves.active : null
        const failedSave = annotationSaves.failed.get(detail.id)
        const localAnnotationPayload =
          pendingSave?.payload ?? activeSave?.payload ?? failedSave?.payload ?? confirmedAnnotationPayload
        setSymbols(localAnnotationPayload?.symbols ?? detail.symbols)
        setConnections(localAnnotationPayload?.connections ?? detail.connections)
        undoStack.current = []
        redoStack.current = []
        setSelectedRef(null)
        setSelectedConn(null)
        setPending(null)
        setHiddenSymbols(new Set())
        setSaveStatus(pendingSave ? 'pending' : activeSave ? 'saving' : failedSave ? 'error' : 'saved')
        setMetaSaveStatus(
          pendingMetaSave ? 'pending' : activeMetaSave ? 'saving' : failedMetaSave ? 'error' : 'saved',
        )
        refreshDraftStorageBlock(detail.id)
        if (pendingSave) void annotationSaves.flush()
        if (pendingMetaSave) void metaSaves.flush()
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      alive = false
      if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current)
      if (annotationSaves.queued.size > 0) void annotationSaves.flush()
    }
  }, [projectId, refreshDraftStorageBlock])

  useEffect(
    () => () => {
      for (const timer of metaSaves.timers.values()) window.clearTimeout(timer)
      metaSaves.timers.clear()
      if (metaSaves.queued.size > 0) void metaSaves.flush()
    },
    [],
  )

  useEffect(() => {
    const media = window.matchMedia('(min-width: 1024px)')
    const update = (event: MediaQueryListEvent) => setDesktopInspector(event.matches)
    setDesktopInspector(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  /* ------------------------------------------------------------------ 履歴 */
  const pushHistory = useCallback(() => {
    undoStack.current.push({
      symbols: JSON.parse(JSON.stringify(symbols)),
      connections: JSON.parse(JSON.stringify(connections)),
    })
    if (undoStack.current.length > 100) undoStack.current.shift()
    redoStack.current = []
    setHistVersion((v) => v + 1)
  }, [symbols, connections])

  const undo = useCallback(() => {
    const prev = undoStack.current.pop()
    if (!prev) return
    redoStack.current.push({ symbols, connections })
    setSymbols(prev.symbols)
    setConnections(prev.connections)
    setSelectedRef(null)
    setHistVersion((v) => v + 1)
  }, [symbols, connections])

  const redo = useCallback(() => {
    const next = redoStack.current.pop()
    if (!next) return
    undoStack.current.push({ symbols, connections })
    setSymbols(next.symbols)
    setConnections(next.connections)
    setHistVersion((v) => v + 1)
  }, [symbols, connections])

  /* ------------------------------------------------------------------ 座標変換 */
  const toNorm = useCallback(
    (clientX: number, clientY: number) => {
      const rect = canvasRef.current?.getBoundingClientRect()
      if (!rect) return { nx: 0, ny: 0 }
      const sx = (clientX - rect.left - pan.x) / zoom
      const sy = (clientY - rect.top - pan.y) / zoom
      return { nx: clamp01(sx / iw), ny: clamp01(sy / ih) }
    },
    [pan, zoom, iw, ih],
  )

  const fit = useCallback((preferReadableScale = false) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect || !project) return
    const availableWidth = Math.max(rect.width - 40, 1)
    const availableHeight = Math.max(rect.height - 40, 1)
    const minimumZoom = preferReadableScale && rect.width < 640 ? 0.34 : 0.05
    const z = Math.max(minimumZoom, Math.min(availableWidth / iw, availableHeight / ih))
    setZoom(z)
    setPan({ x: (rect.width - iw * z) / 2, y: (rect.height - ih * z) / 2 })
  }, [project, iw, ih])

  useEffect(() => {
    if (project) requestAnimationFrame(() => fit(true))
  }, [project, fit, inspectorOpen])

  const zoomAt = useCallback(
    (factor: number, cx: number, cy: number) => {
      const rect = canvasRef.current?.getBoundingClientRect()
      if (!rect) return
      const px = (cx - rect.left - pan.x) / zoom
      const py = (cy - rect.top - pan.y) / zoom
      const z = Math.max(0.05, Math.min(12, zoom * factor))
      setZoom(z)
      setPan({ x: cx - rect.left - px * z, y: cy - rect.top - py * z })
    },
    [pan, zoom],
  )

  /* ------------------------------------------------------------------ 編集操作 */
  const addSymbol = (x1: number, y1: number, x2: number, y2: number) => {
    const raw = fitInside((x1 + x2) / 2, (y1 + y2) / 2, Math.abs(x2 - x1), Math.abs(y2 - y1))
    if (raw.w < MIN_BOX || raw.h < MIN_BOX) return
    pushHistory()
    const ref = nextSymbolRef(symbols)
    setSymbols((prev) => [
      ...prev,
      { ref, class_key: classKey, label: null, ...raw, note: null, terminals: [] },
    ])
    setSelectedRef(ref)
  }

  const deleteSymbol = (ref: string) => {
    pushHistory()
    setSymbols((prev) => prev.filter((s) => s.ref !== ref))
    setConnections((prev) => prev.filter((c) => c.from_symbol_ref !== ref && c.to_symbol_ref !== ref))
    setSelectedRef((cur) => (cur === ref ? null : cur))
    setHiddenSymbols((prev) => {
      if (!prev.has(ref)) return prev
      const next = new Set(prev)
      next.delete(ref)
      return next
    })
  }

  /* AI 推論結果を編集対象のシンボルとして取り込む（破線表示・人が修正して蓄積）*/
  const applyPredictions = async () => {
    if (aiApplying) return
    setAiApplying(true)
    try {
      const data = await api.getPredictions(projectId)
      if (data.count === 0) {
        setNotice('この図面の推論結果がありません。「AI 改善サイクル」で推論を実行してください')
        return
      }
      const classKeys = new Set(classes.map((c) => c.key))
      const next = [...symbols]
      let added = 0
      let skipped = 0
      for (const d of data.detections) {
        if (!classKeys.has(d.class_key)) continue
        const dup = next.some(
          (s) =>
            s.class_key === d.class_key &&
            Math.abs(s.cx - d.cx) < INFERENCE_CENTER_TOLERANCE &&
            Math.abs(s.cy - d.cy) < INFERENCE_CENTER_TOLERANCE &&
            Math.abs(s.w - d.w) < INFERENCE_SIZE_TOLERANCE &&
            Math.abs(s.h - d.h) < INFERENCE_SIZE_TOLERANCE,
        )
        if (dup) {
          skipped += 1
          continue
        }
        next.push({
          ref: nextSymbolRef(next),
          class_key: d.class_key,
          label: null,
          cx: d.cx,
          cy: d.cy,
          w: d.w,
          h: d.h,
          note: null,
          origin: 'inference',
          confidence: d.confidence,
          terminals: [],
        })
        added += 1
      }
      if (added === 0) {
        setNotice(skipped > 0 ? '推論結果はすべて取り込み済みです' : '取り込める検出がありませんでした')
        return
      }
      pushHistory()
      setSymbols(next)
      setNotice(
        `AI 検出を ${added} 件取り込みました（破線枠）。内容を確認・修正して保存してください` +
          (skipped > 0 ? `／${skipped} 件は既存と重複したためスキップ` : ''),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setAiApplying(false)
    }
  }

  const addTerminal = (ref: string, nx: number, ny: number) => {
    pushHistory()
    setSymbols((prev) =>
      prev.map((s) => {
        if (s.ref !== ref) return s
        const n = s.terminals.length + 1
        return {
          ...s,
          terminals: [...s.terminals, { ref: `${s.ref}-T${n}`, name: String(n), tx: nx, ty: ny }],
        }
      }),
    )
  }

  const addConnection = (
    from: { symbolRef: string; terminalRef: string | null },
    to: { symbolRef: string; terminalRef: string | null },
  ) => {
    if (from.symbolRef === to.symbolRef && from.terminalRef === to.terminalRef) return
    const dup = connections.some(
      (c) =>
        c.from_symbol_ref === from.symbolRef &&
        c.to_symbol_ref === to.symbolRef &&
        c.from_terminal_ref === from.terminalRef &&
        c.to_terminal_ref === to.terminalRef,
    )
    if (dup) {
      setNotice('同じ配線が既に登録されています')
      return
    }
    pushHistory()
    setConnections((prev) => [
      ...prev,
      {
        from_symbol_ref: from.symbolRef,
        from_terminal_ref: from.terminalRef,
        to_symbol_ref: to.symbolRef,
        to_terminal_ref: to.terminalRef,
        wire_no: null,
        net_id: null,
        kind: 'wire',
        external_ref: null,
        note: null,
      },
    ])
    setTab('connections')
  }

  const hitSymbol = (nx: number, ny: number): SymbolBox | null => {
    for (let i = visibleSymbols.length - 1; i >= 0; i--) {
      const s = visibleSymbols[i]
      if (
        nx >= s.cx - s.w / 2 &&
        nx <= s.cx + s.w / 2 &&
        ny >= s.cy - s.h / 2 &&
        ny <= s.cy + s.h / 2
      )
        return s
    }
    return null
  }

  const hitTerminal = (nx: number, ny: number): { symbol: SymbolBox; terminalRef: string } | null => {
    if (!showTerminals) return null
    const r = 8 / zoom / Math.max(iw, ih)
    for (let i = visibleSymbols.length - 1; i >= 0; i--) {
      const s = visibleSymbols[i]
      for (const t of s.terminals) {
        if (Math.hypot((t.tx - nx) * iw, (t.ty - ny) * ih) <= Math.max(6, r * iw))
          return { symbol: s, terminalRef: t.ref }
      }
    }
    return null
  }

  const hitCorner = (nx: number, ny: number): { ref: string; corner: Corner } | null => {
    const rx = 9 / zoom / iw
    const ry = 9 / zoom / ih
    for (let i = visibleSymbols.length - 1; i >= 0; i--) {
      const s = visibleSymbols[i]
      const l = s.cx - s.w / 2
      const r = s.cx + s.w / 2
      const t = s.cy - s.h / 2
      const b = s.cy + s.h / 2
      const corners: [Corner, number, number][] = [
        ['nw', l, t],
        ['ne', r, t],
        ['sw', l, b],
        ['se', r, b],
      ]
      for (const [corner, x, y] of corners) {
        if (Math.abs(nx - x) <= rx && Math.abs(ny - y) <= ry) return { ref: s.ref, corner }
      }
    }
    return null
  }

  /* 非表示にしたシンボルは選択・始点保留からも外す（見えない枠を編集状態にしない） */
  const setSymbolHidden = (ref: string, hidden: boolean) => {
    setHiddenSymbols((prev) => {
      const next = new Set(prev)
      if (hidden) next.add(ref)
      else next.delete(ref)
      return next
    })
    if (hidden) {
      setSelectedRef((cur) => (cur === ref ? null : cur))
      setPending((cur) => (cur?.symbolRef === ref ? null : cur))
    }
  }

  const setSymbolGroupHidden = (refs: string[], hidden: boolean) => {
    const targets = new Set(refs)
    setHiddenSymbols((prev) => {
      const next = new Set(prev)
      if (hidden) refs.forEach((ref) => next.add(ref))
      else refs.forEach((ref) => next.delete(ref))
      return next
    })
    if (hidden) {
      setSelectedRef((cur) => (cur && targets.has(cur) ? null : cur))
      setPending((cur) => (cur && targets.has(cur.symbolRef) ? null : cur))
    }
  }

  const resetLayers = () => {
    setHiddenSymbols(new Set())
    setSelectedRef(null)
    setShowTerminals(true)
    setShowConnections(true)
  }

  /* ------------------------------------------------------------------ ポインター（マウス／ペン／タッチ） */
  const onPointerDown = (e: React.PointerEvent) => {
    if (!project) return
    if (activePointerIdRef.current !== null) return
    const eventTarget = e.target instanceof Element ? e.target : null
    if (eventTarget?.closest('button, a, input, select, textarea, [role="tab"]')) return
    const { nx, ny } = toNorm(e.clientX, e.clientY)

    const startDrag = (nextDrag: Drag) => {
      e.preventDefault()
      activePointerIdRef.current = e.pointerId
      e.currentTarget.setPointerCapture(e.pointerId)
      setDrag(nextDrag)
    }

    if (e.button === 1 || e.altKey) {
      startDrag({ kind: 'pan', sx: e.clientX, sy: e.clientY, ox: pan.x, oy: pan.y })
      return
    }
    if (e.button !== 0) return
    e.preventDefault()

    if (mode === 'connect') {
      const t = hitTerminal(nx, ny)
      const s = t?.symbol ?? hitSymbol(nx, ny)
      if (!s) {
        setPending(null)
        return
      }
      const node = { symbolRef: s.ref, terminalRef: t?.terminalRef ?? null }
      if (!pending) setPending(node)
      else {
        addConnection(pending, node)
        setPending(null)
      }
      return
    }

    if (mode === 'terminal') {
      const s = hitSymbol(nx, ny)
      if (s) {
        addTerminal(s.ref, nx, ny)
        setSelectedRef(s.ref)
      }
      return
    }

    if (mode === 'box') {
      startDrag({ kind: 'draw', x1: nx, y1: ny, x2: nx, y2: ny })
      return
    }

    // select
    const corner = hitCorner(nx, ny)
    if (corner) {
      pushHistory()
      setSelectedRef(corner.ref)
      startDrag({ kind: 'resize', ref: corner.ref, corner: corner.corner })
      return
    }
    const s = hitSymbol(nx, ny)
    if (s) {
      pushHistory()
      setSelectedRef(s.ref)
      startDrag({ kind: 'move', ref: s.ref, dx: nx - s.cx, dy: ny - s.cy })
      return
    }
    setSelectedRef(null)
    startDrag({ kind: 'pan', sx: e.clientX, sy: e.clientY, ox: pan.x, oy: pan.y })
  }

  const cancelActivePointer = useCallback(() => {
    const pointerId = activePointerIdRef.current
    if (pointerId !== null && canvasRef.current?.hasPointerCapture(pointerId)) {
      canvasRef.current.releasePointerCapture(pointerId)
    }
    activePointerIdRef.current = null
    setDrag({ kind: 'none' })
  }, [])

  useEffect(() => {
    if (drag.kind === 'none') return
    const move = (e: PointerEvent) => {
      if (e.pointerId !== activePointerIdRef.current) return
      if (drag.kind === 'pan') {
        setPan({ x: drag.ox + (e.clientX - drag.sx), y: drag.oy + (e.clientY - drag.sy) })
        return
      }
      const { nx, ny } = toNorm(e.clientX, e.clientY)
      if (drag.kind === 'draw') {
        setDrag({ ...drag, x2: nx, y2: ny })
      } else if (drag.kind === 'move') {
        setSymbols((prev) =>
          prev.map((s) =>
            s.ref === drag.ref ? { ...s, ...fitInside(nx - drag.dx, ny - drag.dy, s.w, s.h) } : s,
          ),
        )
      } else if (drag.kind === 'resize') {
        setSymbols((prev) =>
          prev.map((s) => {
            if (s.ref !== drag.ref) return s
            let l = s.cx - s.w / 2
            let r = s.cx + s.w / 2
            let t = s.cy - s.h / 2
            let b = s.cy + s.h / 2
            if (drag.corner === 'nw') {
              l = nx
              t = ny
            } else if (drag.corner === 'ne') {
              r = nx
              t = ny
            } else if (drag.corner === 'sw') {
              l = nx
              b = ny
            } else {
              r = nx
              b = ny
            }
            const w = Math.abs(r - l)
            const h = Math.abs(b - t)
            if (w < MIN_BOX || h < MIN_BOX) return s
            return { ...s, ...fitInside((l + r) / 2, (t + b) / 2, w, h) }
          }),
        )
      }
    }
    const up = (e: PointerEvent) => {
      if (e.pointerId !== activePointerIdRef.current) return
      if (drag.kind === 'draw') addSymbol(drag.x1, drag.y1, drag.x2, drag.y2)
      cancelActivePointer()
    }
    const cancel = (e: PointerEvent) => {
      if (e.pointerId !== activePointerIdRef.current) return
      cancelActivePointer()
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel)
    }
  }, [addSymbol, cancelActivePointer, drag, fitInside, iw, toNorm])

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY)
  }

  /* ------------------------------------------------------------------ 自動保存 */
  const annotationPayload = useMemo(
    () => buildAnnotationPayload(symbols, connections),
    [symbols, connections],
  )

  annotationSaves.flush = async () => {
    if (annotationSaves.inFlight) return
    const queued = takeNextProjectSave(annotationSaves.queued)
    if (!queued) return
    if (queued.key === annotationSaves.lastSavedKeys.get(queued.projectId)) {
      annotationSaves.failed.delete(queued.projectId)
      clearPersistedProjectSave('annotations', queued.projectId)
      refreshDraftStorageBlock(queued.projectId)
      if (annotationSaves.queued.size > 0) void annotationSaves.flush()
      else if (queued.projectId === currentProjectIdRef.current) setSaveStatus('saved')
      return
    }

    annotationSaves.inFlight = true
    annotationSaves.active = queued
    if (queued.projectId === currentProjectIdRef.current) {
      setSaveStatus('saving')
      setError(null)
    }
    let succeeded = false
    try {
      const res = await api.saveAnnotations(queued.projectId, queued.payload)
      annotationSaves.lastSavedKeys.set(queued.projectId, queued.key)
      annotationSaves.lastSavedPayloads.set(queued.projectId, queued.payload)
      annotationSaves.generation.set(
        queued.projectId,
        (annotationSaves.generation.get(queued.projectId) ?? 0) + 1,
      )
      annotationSaves.failed.delete(queued.projectId)
      if (!annotationSaves.queued.has(queued.projectId)) {
        clearPersistedProjectSave('annotations', queued.projectId)
        refreshDraftStorageBlock(queued.projectId)
      }
      if (queued.projectId === currentProjectIdRef.current) setError(null)
      succeeded = true
      if (res.skipped.length) setNotice(`${res.skipped.length} 件の配線を保存できませんでした`)
    } catch (e) {
      const hasNewerSameProject = annotationSaves.queued.has(queued.projectId)
      if (!hasNewerSameProject) annotationSaves.failed.set(queued.projectId, queued)
      if (queued.projectId === currentProjectIdRef.current) {
        setSaveStatus('error')
        setError(e instanceof Error ? e.message : String(e))
      } else {
        setNotice(`ページ ${queued.projectId} の保存に失敗しました。保存ボタンで再試行できます`)
      }
    } finally {
      annotationSaves.inFlight = false
      annotationSaves.active = null
      const shouldContinue = annotationSaves.queued.size > 0
      if (shouldContinue) {
        if (annotationSaves.queued.has(currentProjectIdRef.current)) setSaveStatus('pending')
        void annotationSaves.flush()
      } else if (succeeded && queued.projectId === currentProjectIdRef.current) {
        setSaveStatus('saved')
      }
    }
  }

  useEffect(() => {
    if (!isCurrentProject(project, projectId)) return
    const key = JSON.stringify(annotationPayload)
    const queuedSave = annotationSaves.queued.get(projectId)
    const activeSave = annotationSaves.active?.projectId === projectId ? annotationSaves.active : null
    const failedSave = annotationSaves.failed.get(projectId)
    const latestOutstandingSave = queuedSave ?? activeSave ?? failedSave
    if (key === annotationSaves.lastSavedKeys.get(projectId) && !activeSave) {
      annotationSaves.queued.delete(projectId)
      annotationSaves.failed.delete(projectId)
      clearPersistedProjectSave('annotations', projectId)
      refreshDraftStorageBlock(projectId)
      setSaveStatus('saved')
      if (autosaveTimerRef.current !== null) {
        window.clearTimeout(autosaveTimerRef.current)
        autosaveTimerRef.current = null
      }
      return
    }
    if (key === latestOutstandingSave?.key) return
    annotationSaves.failed.delete(projectId)
    const save = createProjectScopedSave(projectId, annotationPayload)
    enqueueProjectSave(annotationSaves.queued, save)
    if (!persistProjectSave('annotations', save)) setDraftStorageBlocked(true)
    else refreshDraftStorageBlock(projectId)
    setSaveStatus('pending')
    if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current)
    autosaveTimerRef.current = window.setTimeout(() => {
      autosaveTimerRef.current = null
      void annotationSaves.flush()
    }, 400)
    return () => {
      if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current)
    }
  }, [annotationPayload, project, projectId, refreshDraftStorageBlock])

  const saveNow = useCallback(async () => {
    if (!isCurrentProject(project, projectId)) return
    if (autosaveTimerRef.current !== null) {
      window.clearTimeout(autosaveTimerRef.current)
      autosaveTimerRef.current = null
    }
    const key = JSON.stringify(annotationPayload)
    const queuedSave = annotationSaves.queued.get(projectId)
    const activeSave = annotationSaves.active?.projectId === projectId ? annotationSaves.active : null
    const failedSave = annotationSaves.failed.get(projectId)
    const latestOutstandingSave = queuedSave ?? activeSave ?? failedSave
    if (
      key !== latestOutstandingSave?.key &&
      (key !== annotationSaves.lastSavedKeys.get(projectId) || Boolean(latestOutstandingSave))
    ) {
      const save = createProjectScopedSave(projectId, annotationPayload)
      annotationSaves.failed.delete(projectId)
      enqueueProjectSave(annotationSaves.queued, save)
      if (!persistProjectSave('annotations', save)) setDraftStorageBlocked(true)
      else refreshDraftStorageBlock(projectId)
    } else if (latestOutstandingSave === failedSave && failedSave) {
      annotationSaves.failed.delete(projectId)
      enqueueProjectSave(annotationSaves.queued, failedSave)
    }
    await annotationSaves.flush()
  }, [annotationPayload, project, projectId, refreshDraftStorageBlock])

  const exportCurrentProject = async () => {
    if (!project || exporting) return
    setExporting(true)
    setError(null)
    try {
      await api.exportProject(project.id)
      setNotice('この図面のデータを書き出しました')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setExporting(false)
    }
  }

  const { onCanvasKeyDown } = useEditorShortcuts({
    mode,
    classes,
    symbols,
    selectedRef,
    selectedConn,
    pending,
    setSymbols,
    setConnections,
    setMode,
    setClassKey,
    setSelectedRef,
    setSelectedConn,
    setPending,
    saveNow,
    pushHistory,
    undo,
    redo,
    deleteSymbol,
    addSymbol,
    addTerminal,
    addConnection,
    cancelActivePointer,
  })

  /* ------------------------------------------------------------------ 描画 */
  const nodePos = (symbolRef: string, terminalRef: string | null) => {
    const s = symbolByRef.get(symbolRef)
    if (!s) return null
    if (terminalRef) {
      const t = s.terminals.find((x) => x.ref === terminalRef)
      if (t) return { x: t.tx * iw, y: t.ty * ih }
    }
    return { x: s.cx * iw, y: s.cy * ih }
  }

  if (error && !project) {
    return (
      <div className="p-8">
        <div className="card border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{error}</div>
        <Link to="/" className="btn mt-4">
          <ArrowLeft size={14} /> 一覧に戻る
        </Link>
      </div>
    )
  }
  if (!project) return <div className="p-8 text-sm text-slate-500">読み込み中…</div>

  const saveStatusLabel = SAVE_STATUS_LABELS[saveStatus]
  const visibleError = draftStorageBlocked
    ? 'ブラウザの一時保存領域を利用できません。サーバー保存が完了するまで、この画面から移動できません。'
    : error

  /* レイヤーパネルの行データ（クラスはマスタの並び順＋未登録キーは末尾） */
  const terminalCount = symbols.reduce((n, s) => n + s.terminals.length, 0)
  const inferenceRefs = symbols.filter((s) => s.origin === 'inference').map((s) => s.ref)
  const hiddenInferenceCount = inferenceRefs.filter((ref) => hiddenSymbols.has(ref)).length
  const classOrder = classes.map((c) => c.key)
  const classLayerRows = [...new Set(symbols.map((s) => s.class_key))]
    .sort(
      (a, b) =>
        (classOrder.indexOf(a) < 0 ? classOrder.length : classOrder.indexOf(a)) -
        (classOrder.indexOf(b) < 0 ? classOrder.length : classOrder.indexOf(b)),
    )
    .map((key) => {
      const members = symbols.filter((s) => s.class_key === key)
      const hidden = members.reduce((n, s) => n + (hiddenSymbols.has(s.ref) ? 1 : 0), 0)
      return {
        key,
        label: labelOf(key),
        color: colorOf(key),
        total: members.length,
        hidden,
        refs: members.map((s) => s.ref),
      }
    })
  const allLayersVisible = hiddenSymbols.size === 0 && showTerminals && showConnections
  /* バッジは「表示をオフにした要素の数」（隠したシンボル + 端子/配線レイヤー） */
  const hiddenAnnotationCount =
    hiddenSymbols.size + (showTerminals ? 0 : 1) + (showConnections ? 0 : 1)

  const draftRect =
    drag.kind === 'draw'
      ? {
          x: Math.min(drag.x1, drag.x2) * iw,
          y: Math.min(drag.y1, drag.y2) * ih,
          w: Math.abs(drag.x2 - drag.x1) * iw,
          h: Math.abs(drag.y2 - drag.y1) * ih,
        }
      : null

  return (
    <div className="flex h-dvh min-w-[320px] flex-col overflow-hidden bg-slate-950 text-slate-100">
      {exporting && (
        <LoadingOverlay
          message="この図面を出力しています"
          hint="ZIP を作成してダウンロードしています。"
        />
      )}
      {/* ---------------- ワークスペースヘッダー ---------------- */}
      <EditorHeader
        project={project}
        projectId={projectId}
        navigation={navigation}
        symbolCount={symbols.length}
        connectionCount={connections.length}
        aiApplying={aiApplying}
        onApplyPredictions={() => void applyPredictions()}
        exporting={exporting}
        onExport={() => void exportCurrentProject()}
        saveStatus={saveStatus}
        saveStatusLabel={saveStatusLabel}
        onSave={() => void saveNow()}
      />

      {/* ---------------- 編集ツールバー ---------------- */}
      <EditorToolbar
        mode={mode}
        onModeChange={(next) => {
          setMode(next)
          setPending(null)
        }}
        classKey={classKey}
        onClassKeyChange={setClassKey}
        classes={classes}
        colorOf={colorOf}
        layersOpen={layersOpen}
        onToggleLayers={() => setLayersOpen((value) => !value)}
        hiddenAnnotationCount={hiddenAnnotationCount}
        histVersion={histVersion}
        canUndo={undoStack.current.length > 0}
        canRedo={redoStack.current.length > 0}
        onUndo={undo}
        onRedo={redo}
        inspectorOpen={inspectorOpen}
        onToggleInspector={() => setInspectorOpen((value) => !value)}
      />

      {(pending || notice || visibleError) && (
        <div
          className={`flex flex-none items-center gap-3 border-b px-4 py-2 text-xs ${
            visibleError
              ? 'border-rose-300/20 bg-rose-300/10 text-rose-100'
              : 'border-amber-300/20 bg-amber-300/10 text-amber-100'
          }`}
          role={visibleError ? 'alert' : 'status'}
        >
          {visibleError ? (
            <AlertCircle size={14} className="flex-none text-rose-300" />
          ) : (
            <CircleDot size={14} className="flex-none text-amber-300" />
          )}
          <span className="min-w-0 flex-1 truncate">
            {visibleError
              ? visibleError
              : pending
              ? `始点: ${pending.symbolRef}${pending.terminalRef ? `:${pending.terminalRef.split('-').pop()}` : ''} → 終点をクリック（Escで取消）`
              : notice}
          </span>
          {draftStorageBlocked && (
            <button
              type="button"
              className="min-h-9 flex-none rounded-lg border border-rose-200/30 bg-rose-100/10 px-3 font-bold text-white hover:bg-rose-100/20"
              onClick={() => {
                void saveNow()
                void metaSaves.flush()
              }}
            >
              保存を再試行
            </button>
          )}
        </div>
      )}

      <div className="relative flex min-h-0 flex-1">
        {/* ---------------- キャンバス ---------------- */}
        <div
          ref={canvasRef}
          data-diagram-viewport
          onPointerDown={onPointerDown}
          onKeyDown={onCanvasKeyDown}
          onWheel={onWheel}
          tabIndex={0}
          role="region"
          aria-label="図面アノテーションキャンバス"
          aria-describedby="annotation-keyboard-help"
          className="surface-grid relative min-h-0 min-w-0 touch-none flex-1 overflow-hidden"
          aria-hidden={inspectorOpen && !desktopInspector}
          inert={inspectorOpen && !desktopInspector ? true : undefined}
          style={{ cursor: mode === 'box' ? 'crosshair' : drag.kind === 'pan' ? 'grabbing' : 'default' }}
        >
          <p id="annotation-keyboard-help" className="sr-only">
            シンボル描画モードではEnterキーで中央に矩形を作成できます。選択した矩形は矢印キーで移動し、
            Shiftと矢印キーでサイズを変更できます。端子モードでは選択中の矩形にEnterキーで端子を追加し、
            配線モードでは始点と終点の矩形を順に選んでEnterキーで確定します。Altキーを併用すると細かく調整できます。
          </p>
          <CanvasControls
            mode={mode}
            inspectorOpen={inspectorOpen}
            onOpenInspector={() => setInspectorOpen(true)}
            zoom={zoom}
            zoomAt={zoomAt}
            fit={fit}
            shortcutsVisible={shortcutsVisible}
            onToggleShortcuts={() => setShortcutsVisible((value) => !value)}
          />
          {layersOpen && (
            <LayerPanel
              connectionCount={connections.length}
              terminalCount={terminalCount}
              inferenceRefs={inferenceRefs}
              hiddenInferenceCount={hiddenInferenceCount}
              classLayerRows={classLayerRows}
              showConnections={showConnections}
              showTerminals={showTerminals}
              allLayersVisible={allLayersVisible}
              onToggleConnections={() => setShowConnections((value) => !value)}
              onToggleTerminals={() => setShowTerminals((value) => !value)}
              onSetSymbolGroupHidden={setSymbolGroupHidden}
              onReset={resetLayers}
            />
          )}
          <div
            className="absolute left-0 top-0 origin-top-left"
            style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
          >
            <img
              src={api.imageUrl(project.id)}
              width={iw}
              height={ih}
              alt={project.name}
              draggable={false}
              className="block max-w-none select-none bg-white shadow-lg"
            />
            <EditorCanvasSvg
              iw={iw}
              ih={ih}
              zoom={zoom}
              showConnections={showConnections}
              showTerminals={showTerminals}
              connections={connections}
              nodePos={nodePos}
              selectedConn={selectedConn}
              visibleSymbols={visibleSymbols}
              selectedRef={selectedRef}
              colorOf={colorOf}
              draftRect={draftRect}
              draftClassKey={classKey}
            />
          </div>
        </div>

        {/* ---------------- 右パネル ---------------- */}
        {inspectorOpen && (
          <EditorInspector
            inspectorRef={inspectorRef}
            desktopInspector={desktopInspector}
            onClose={closeInspector}
            tab={tab}
            onTabChange={setTab}
            symbols={symbols}
            connections={connections}
            classes={classes}
            colorOf={colorOf}
            labelOf={labelOf}
            hiddenSymbols={hiddenSymbols}
            selectedRef={selectedRef}
            onSelectSymbol={setSelectedRef}
            onSetSymbolHidden={setSymbolHidden}
            onDeleteSymbol={deleteSymbol}
            pushHistory={pushHistory}
            setSymbols={setSymbols}
            setConnections={setConnections}
            selectedConn={selectedConn}
            onSelectConnection={setSelectedConn}
            project={project}
            metaSaveStatus={metaSaveStatus}
            onQueueMetaSave={queueMetaSave}
          />
        )}
      </div>
    </div>
  )
}


