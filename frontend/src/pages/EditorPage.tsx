import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useBlocker, useParams } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  Cable,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  CloudUpload,
  Download,
  Eye,
  EyeOff,
  Layers,
  Maximize2,
  MousePointer2,
  PanelRightClose,
  PanelRightOpen,
  Redo2,
  Save,
  Sparkles,
  Square,
  Trash2,
  Undo2,
  ZoomIn,
  ZoomOut,
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
import type { ProjectScopedSave } from '../utils/workspace'

type Mode = 'select' | 'box' | 'terminal' | 'connect'
type Corner = 'nw' | 'ne' | 'sw' | 'se'
type Snapshot = { symbols: SymbolBox[]; connections: Connection[] }
type SaveStatus = 'pending' | 'saving' | 'saved' | 'error'

function buildAnnotationPayload(symbols: SymbolBox[], connections: Connection[]) {
  return {
    symbols: symbols.map((symbol) => ({
      ref: symbol.ref,
      class_key: symbol.class_key,
      label: symbol.label,
      cx: symbol.cx,
      cy: symbol.cy,
      w: symbol.w,
      h: symbol.h,
      note: symbol.note,
      origin: symbol.origin ?? 'manual',
      confidence: symbol.confidence ?? null,
      terminals: symbol.terminals.map((terminal) => ({
        ref: terminal.ref,
        name: terminal.name,
        tx: terminal.tx,
        ty: terminal.ty,
      })),
    })),
    connections,
  }
}

type AnnotationPayload = ReturnType<typeof buildAnnotationPayload>
type QueuedSave = ProjectScopedSave<AnnotationPayload>

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === 'string' || value === null
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isAnnotationPayload(value: unknown): value is AnnotationPayload {
  if (!isRecord(value) || !Array.isArray(value.symbols) || !Array.isArray(value.connections)) return false
  const validSymbols = value.symbols.every((symbol) => {
    if (!isRecord(symbol) || !Array.isArray(symbol.terminals)) return false
    return (
      typeof symbol.ref === 'string' &&
      typeof symbol.class_key === 'string' &&
      isNullableString(symbol.label) &&
      isFiniteNumber(symbol.cx) &&
      isFiniteNumber(symbol.cy) &&
      isFiniteNumber(symbol.w) &&
      isFiniteNumber(symbol.h) &&
      isNullableString(symbol.note) &&
      (symbol.origin === undefined || symbol.origin === 'manual' || symbol.origin === 'inference') &&
      (symbol.confidence === undefined || symbol.confidence === null || isFiniteNumber(symbol.confidence)) &&
      symbol.terminals.every(
        (terminal) =>
          isRecord(terminal) &&
          typeof terminal.ref === 'string' &&
          typeof terminal.name === 'string' &&
          isFiniteNumber(terminal.tx) &&
          isFiniteNumber(terminal.ty),
      )
    )
  })
  if (!validSymbols) return false
  return value.connections.every(
    (connection) =>
      isRecord(connection) &&
      typeof connection.from_symbol_ref === 'string' &&
      isNullableString(connection.from_terminal_ref) &&
      typeof connection.to_symbol_ref === 'string' &&
      isNullableString(connection.to_terminal_ref) &&
      isNullableString(connection.wire_no) &&
      isNullableString(connection.net_id) &&
      (connection.kind === 'wire' || connection.kind === 'sheet_ref') &&
      isNullableString(connection.external_ref) &&
      isNullableString(connection.note),
  )
}

function buildMetaForm(project: ProjectDetail) {
  return {
    name: project.name,
    sheet_no: project.sheet_no ?? '',
    page_no: project.page_no ?? '',
    revision: project.revision ?? '',
    status: project.status,
    assignee: project.assignee ?? '',
    note: project.note ?? '',
  }
}

type MetaForm = ReturnType<typeof buildMetaForm>
type QueuedMetaSave = ProjectScopedSave<MetaForm>

function isMetaForm(value: unknown): value is MetaForm {
  return (
    isRecord(value) &&
    typeof value.name === 'string' &&
    typeof value.sheet_no === 'string' &&
    typeof value.page_no === 'string' &&
    typeof value.revision === 'string' &&
    typeof value.status === 'string' &&
    typeof value.assignee === 'string' &&
    typeof value.note === 'string'
  )
}

const MODES: { id: Mode; label: string; key: string; icon: typeof Square }[] = [
  { id: 'select', label: '選択・移動', key: 'V', icon: MousePointer2 },
  { id: 'box', label: 'シンボル描画', key: 'B', icon: Square },
  { id: 'terminal', label: '端子を置く', key: 'T', icon: CircleDot },
  { id: 'connect', label: '配線 (from-to)', key: 'C', icon: Cable },
]

const INSPECTOR_TABS = [
  ['symbols', 'シンボル'],
  ['connections', '配線'],
  ['meta', '図面情報'],
] as const
type InspectorTab = (typeof INSPECTOR_TABS)[number][0]

const SAVE_STATUS_LABELS: Record<SaveStatus, { short: string; long: string }> = {
  pending: { short: '未保存', long: '変更を検出' },
  saving: { short: '保存中', long: '自動保存中…' },
  saved: { short: '保存済', long: '自動保存済み' },
  error: { short: '再試行', long: '保存に失敗しました。押すと再試行します' },
}

/** ドラッグ操作の種類 */
type Drag =
  | { kind: 'none' }
  | { kind: 'pan'; sx: number; sy: number; ox: number; oy: number }
  | { kind: 'draw'; x1: number; y1: number; x2: number; y2: number }
  | { kind: 'move'; ref: string; dx: number; dy: number }
  | { kind: 'resize'; ref: string; corner: Corner }

const MIN_BOX = 0.002

// AI 推論の取り込み時に「既存シンボルと同一」とみなす許容誤差（正規化座標）
const INFERENCE_CENTER_TOLERANCE = 0.01
const INFERENCE_SIZE_TOLERANCE = 0.02

function clamp01(v: number) {
  return v < 0 ? 0 : v > 1 ? 1 : v
}

/** レイヤー行の表示状態: 全表示 / 全非表示 / 一部非表示 */
type LayerState = 'on' | 'off' | 'partial'
function layerStateOf(hidden: number, total: number): LayerState {
  if (total === 0 || hidden === 0) return 'on'
  return hidden === total ? 'off' : 'partial'
}

/** 矩形が画像の外へはみ出さないように中心を寄せる */
function fitInside(cx: number, cy: number, w: number, h: number) {
  const bw = Math.min(w, 1)
  const bh = Math.min(h, 1)
  return {
    cx: Math.min(1 - bw / 2, Math.max(bw / 2, cx)),
    cy: Math.min(1 - bh / 2, Math.max(bh / 2, cy)),
    w: bw,
    h: bh,
  }
}

function nextSymbolRef(symbols: SymbolBox[]) {
  let n = symbols.length + 1
  const used = new Set(symbols.map((s) => s.ref))
  while (used.has(`SYM-${String(n).padStart(4, '0')}`)) n += 1
  return `SYM-${String(n).padStart(4, '0')}`
}

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
  const [inspectorOpen, setInspectorOpen] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 1024px)').matches,
  )
  const [desktopInspector, setDesktopInspector] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 1024px)').matches,
  )
  const [shortcutsVisible, setShortcutsVisible] = useState(false)

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
  const saveInFlightRef = useRef(false)
  const activeSaveRef = useRef<QueuedSave | null>(null)
  const queuedSavesRef = useRef(new Map<number, QueuedSave>())
  const failedSavesRef = useRef(new Map<number, QueuedSave>())
  const lastSavedKeysRef = useRef(new Map<number, string>())
  const lastSavedPayloadsRef = useRef(new Map<number, AnnotationPayload>())
  const annotationSaveGenerationRef = useRef(new Map<number, number>())
  const flushSaveRef = useRef<() => Promise<void>>(async () => undefined)
  const queuedMetaSavesRef = useRef(new Map<number, QueuedMetaSave>())
  const failedMetaSavesRef = useRef(new Map<number, QueuedMetaSave>())
  const lastSavedMetaKeysRef = useRef(new Map<number, string>())
  const lastSavedMetaPayloadsRef = useRef(new Map<number, MetaForm>())
  const metaSaveGenerationRef = useRef(new Map<number, number>())
  const metaSaveInFlightRef = useRef(false)
  const activeMetaSaveRef = useRef<QueuedMetaSave | null>(null)
  const metaSaveTimersRef = useRef(new Map<number, number>())
  const flushMetaSaveRef = useRef<() => Promise<void>>(async () => undefined)
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

  flushMetaSaveRef.current = async () => {
    if (metaSaveInFlightRef.current) return
    const queued = takeNextProjectSave(queuedMetaSavesRef.current)
    if (!queued) return
    if (queued.key === lastSavedMetaKeysRef.current.get(queued.projectId)) {
      failedMetaSavesRef.current.delete(queued.projectId)
      clearPersistedProjectSave('meta', queued.projectId)
      refreshDraftStorageBlock(queued.projectId)
      if (queuedMetaSavesRef.current.size > 0) void flushMetaSaveRef.current()
      else if (queued.projectId === currentProjectIdRef.current) setMetaSaveStatus('saved')
      return
    }

    metaSaveInFlightRef.current = true
    activeMetaSaveRef.current = queued
    if (queued.projectId === currentProjectIdRef.current) {
      setMetaSaveStatus('saving')
      setError(null)
    }
    let succeeded = false
    try {
      await api.updateMeta(queued.projectId, queued.payload)
      lastSavedMetaKeysRef.current.set(queued.projectId, queued.key)
      lastSavedMetaPayloadsRef.current.set(queued.projectId, queued.payload)
      metaSaveGenerationRef.current.set(
        queued.projectId,
        (metaSaveGenerationRef.current.get(queued.projectId) ?? 0) + 1,
      )
      failedMetaSavesRef.current.delete(queued.projectId)
      if (!queuedMetaSavesRef.current.has(queued.projectId)) {
        clearPersistedProjectSave('meta', queued.projectId)
        refreshDraftStorageBlock(queued.projectId)
      }
      if (queued.projectId === currentProjectIdRef.current) {
        setError(null)
      }
      succeeded = true
    } catch (caught) {
      const hasNewer = queuedMetaSavesRef.current.has(queued.projectId)
      if (!hasNewer) failedMetaSavesRef.current.set(queued.projectId, queued)
      if (queued.projectId === currentProjectIdRef.current) {
        setMetaSaveStatus('error')
        setError(caught instanceof Error ? caught.message : String(caught))
      } else {
        setNotice(`ページ ${queued.projectId} の図面情報を保存できませんでした`)
      }
    } finally {
      metaSaveInFlightRef.current = false
      activeMetaSaveRef.current = null
      if (queuedMetaSavesRef.current.size > 0) {
        void flushMetaSaveRef.current()
      } else if (succeeded && queued.projectId === currentProjectIdRef.current) {
        setMetaSaveStatus('saved')
      }
    }
  }

  const queueMetaSave = useCallback((targetProjectId: number, payload: MetaForm, immediate = false) => {
    const save = createProjectScopedSave(targetProjectId, payload)
    const queuedSave = queuedMetaSavesRef.current.get(targetProjectId)
    const activeSave =
      activeMetaSaveRef.current?.projectId === targetProjectId ? activeMetaSaveRef.current : null
    const failedSave = failedMetaSavesRef.current.get(targetProjectId)
    const latestOutstandingSave = queuedSave ?? activeSave ?? failedSave
    if (save.key === lastSavedMetaKeysRef.current.get(targetProjectId) && !activeSave) {
      queuedMetaSavesRef.current.delete(targetProjectId)
      failedMetaSavesRef.current.delete(targetProjectId)
      const timer = metaSaveTimersRef.current.get(targetProjectId)
      if (timer !== undefined) window.clearTimeout(timer)
      metaSaveTimersRef.current.delete(targetProjectId)
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
        const timer = metaSaveTimersRef.current.get(targetProjectId)
        if (timer !== undefined) window.clearTimeout(timer)
        metaSaveTimersRef.current.delete(targetProjectId)
        void flushMetaSaveRef.current()
      }
      return
    }
    failedMetaSavesRef.current.delete(targetProjectId)
    enqueueProjectSave(queuedMetaSavesRef.current, save)
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
    const timer = metaSaveTimersRef.current.get(targetProjectId)
    if (timer !== undefined) window.clearTimeout(timer)
    metaSaveTimersRef.current.delete(targetProjectId)
    if (immediate) {
      void flushMetaSaveRef.current()
      return
    }
    const nextTimer = window.setTimeout(() => {
      metaSaveTimersRef.current.delete(targetProjectId)
      void flushMetaSaveRef.current()
    }, 400)
    metaSaveTimersRef.current.set(targetProjectId, nextTimer)
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
    const annotationGenerationAtRequest = annotationSaveGenerationRef.current.get(projectId) ?? 0
    const metaGenerationAtRequest = metaSaveGenerationRef.current.get(projectId) ?? 0
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
        const knownMetaKey = lastSavedMetaKeysRef.current.get(detail.id)
        const metaSavedDuringRequest =
          (metaSaveGenerationRef.current.get(detail.id) ?? 0) > metaGenerationAtRequest
        const confirmedMetaPayload =
          metaSavedDuringRequest && knownMetaKey && knownMetaKey !== serverMetaKey
            ? lastSavedMetaPayloadsRef.current.get(detail.id) ?? null
            : null
        if (!confirmedMetaPayload) {
          lastSavedMetaKeysRef.current.set(detail.id, serverMetaKey)
          lastSavedMetaPayloadsRef.current.delete(detail.id)
        }
        const persistedMetaSave = loadPersistedProjectSave('meta', detail.id, isMetaForm)
        if (persistedMetaSave?.key === serverMetaKey) {
          clearPersistedProjectSave('meta', detail.id)
        } else if (
          persistedMetaSave &&
          !queuedMetaSavesRef.current.has(detail.id) &&
          activeMetaSaveRef.current?.projectId !== detail.id &&
          !failedMetaSavesRef.current.has(detail.id)
        ) {
          failedMetaSavesRef.current.set(detail.id, persistedMetaSave)
        }
        const pendingMetaSave = queuedMetaSavesRef.current.get(detail.id)
        const activeMetaSave =
          activeMetaSaveRef.current?.projectId === detail.id ? activeMetaSaveRef.current : null
        const failedMetaSave = failedMetaSavesRef.current.get(detail.id)
        const localMetaPayload =
          pendingMetaSave?.payload ??
          activeMetaSave?.payload ??
          failedMetaSave?.payload ??
          confirmedMetaPayload
        setProject(localMetaPayload ? { ...detail, ...localMetaPayload } : detail)

        const serverAnnotationPayload = buildAnnotationPayload(detail.symbols, detail.connections)
        const serverAnnotationKey = JSON.stringify(serverAnnotationPayload)
        const knownAnnotationKey = lastSavedKeysRef.current.get(detail.id)
        const annotationSavedDuringRequest =
          (annotationSaveGenerationRef.current.get(detail.id) ?? 0) > annotationGenerationAtRequest
        const confirmedAnnotationPayload =
          annotationSavedDuringRequest && knownAnnotationKey && knownAnnotationKey !== serverAnnotationKey
            ? lastSavedPayloadsRef.current.get(detail.id) ?? null
            : null
        if (!confirmedAnnotationPayload) {
          lastSavedKeysRef.current.set(detail.id, serverAnnotationKey)
          lastSavedPayloadsRef.current.delete(detail.id)
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
          !queuedSavesRef.current.has(detail.id) &&
          activeSaveRef.current?.projectId !== detail.id &&
          !failedSavesRef.current.has(detail.id)
        ) {
          failedSavesRef.current.set(detail.id, persistedAnnotationSave)
        }
        const pendingSave = queuedSavesRef.current.get(detail.id)
        const activeSave = activeSaveRef.current?.projectId === detail.id ? activeSaveRef.current : null
        const failedSave = failedSavesRef.current.get(detail.id)
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
        if (pendingSave) void flushSaveRef.current()
        if (pendingMetaSave) void flushMetaSaveRef.current()
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      alive = false
      if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current)
      if (queuedSavesRef.current.size > 0) void flushSaveRef.current()
    }
  }, [projectId, refreshDraftStorageBlock])

  useEffect(
    () => () => {
      for (const timer of metaSaveTimersRef.current.values()) window.clearTimeout(timer)
      metaSaveTimersRef.current.clear()
      if (queuedMetaSavesRef.current.size > 0) void flushMetaSaveRef.current()
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

  flushSaveRef.current = async () => {
    if (saveInFlightRef.current) return
    const queued = takeNextProjectSave(queuedSavesRef.current)
    if (!queued) return
    if (queued.key === lastSavedKeysRef.current.get(queued.projectId)) {
      failedSavesRef.current.delete(queued.projectId)
      clearPersistedProjectSave('annotations', queued.projectId)
      refreshDraftStorageBlock(queued.projectId)
      if (queuedSavesRef.current.size > 0) void flushSaveRef.current()
      else if (queued.projectId === currentProjectIdRef.current) setSaveStatus('saved')
      return
    }

    saveInFlightRef.current = true
    activeSaveRef.current = queued
    if (queued.projectId === currentProjectIdRef.current) {
      setSaveStatus('saving')
      setError(null)
    }
    let succeeded = false
    try {
      const res = await api.saveAnnotations(queued.projectId, queued.payload)
      lastSavedKeysRef.current.set(queued.projectId, queued.key)
      lastSavedPayloadsRef.current.set(queued.projectId, queued.payload)
      annotationSaveGenerationRef.current.set(
        queued.projectId,
        (annotationSaveGenerationRef.current.get(queued.projectId) ?? 0) + 1,
      )
      failedSavesRef.current.delete(queued.projectId)
      if (!queuedSavesRef.current.has(queued.projectId)) {
        clearPersistedProjectSave('annotations', queued.projectId)
        refreshDraftStorageBlock(queued.projectId)
      }
      if (queued.projectId === currentProjectIdRef.current) setError(null)
      succeeded = true
      if (res.skipped.length) setNotice(`${res.skipped.length} 件の配線を保存できませんでした`)
    } catch (e) {
      const hasNewerSameProject = queuedSavesRef.current.has(queued.projectId)
      if (!hasNewerSameProject) failedSavesRef.current.set(queued.projectId, queued)
      if (queued.projectId === currentProjectIdRef.current) {
        setSaveStatus('error')
        setError(e instanceof Error ? e.message : String(e))
      } else {
        setNotice(`ページ ${queued.projectId} の保存に失敗しました。保存ボタンで再試行できます`)
      }
    } finally {
      saveInFlightRef.current = false
      activeSaveRef.current = null
      const shouldContinue = queuedSavesRef.current.size > 0
      if (shouldContinue) {
        if (queuedSavesRef.current.has(currentProjectIdRef.current)) setSaveStatus('pending')
        void flushSaveRef.current()
      } else if (succeeded && queued.projectId === currentProjectIdRef.current) {
        setSaveStatus('saved')
      }
    }
  }

  useEffect(() => {
    if (!isCurrentProject(project, projectId)) return
    const key = JSON.stringify(annotationPayload)
    const queuedSave = queuedSavesRef.current.get(projectId)
    const activeSave = activeSaveRef.current?.projectId === projectId ? activeSaveRef.current : null
    const failedSave = failedSavesRef.current.get(projectId)
    const latestOutstandingSave = queuedSave ?? activeSave ?? failedSave
    if (key === lastSavedKeysRef.current.get(projectId) && !activeSave) {
      queuedSavesRef.current.delete(projectId)
      failedSavesRef.current.delete(projectId)
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
    failedSavesRef.current.delete(projectId)
    const save = createProjectScopedSave(projectId, annotationPayload)
    enqueueProjectSave(queuedSavesRef.current, save)
    if (!persistProjectSave('annotations', save)) setDraftStorageBlocked(true)
    else refreshDraftStorageBlock(projectId)
    setSaveStatus('pending')
    if (autosaveTimerRef.current !== null) window.clearTimeout(autosaveTimerRef.current)
    autosaveTimerRef.current = window.setTimeout(() => {
      autosaveTimerRef.current = null
      void flushSaveRef.current()
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
    const queuedSave = queuedSavesRef.current.get(projectId)
    const activeSave = activeSaveRef.current?.projectId === projectId ? activeSaveRef.current : null
    const failedSave = failedSavesRef.current.get(projectId)
    const latestOutstandingSave = queuedSave ?? activeSave ?? failedSave
    if (
      key !== latestOutstandingSave?.key &&
      (key !== lastSavedKeysRef.current.get(projectId) || Boolean(latestOutstandingSave))
    ) {
      const save = createProjectScopedSave(projectId, annotationPayload)
      failedSavesRef.current.delete(projectId)
      enqueueProjectSave(queuedSavesRef.current, save)
      if (!persistProjectSave('annotations', save)) setDraftStorageBlocked(true)
      else refreshDraftStorageBlock(projectId)
    } else if (latestOutstandingSave === failedSave && failedSave) {
      failedSavesRef.current.delete(projectId)
      enqueueProjectSave(queuedSavesRef.current, failedSave)
    }
    await flushSaveRef.current()
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

  const onCanvasKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && mode === 'box') {
      event.preventDefault()
      addSymbol(0.44, 0.46, 0.56, 0.54)
      return
    }
    if (event.key === 'Enter' && mode === 'terminal' && selectedRef) {
      event.preventDefault()
      const selectedSymbol = symbolByRef.get(selectedRef)
      if (selectedSymbol) addTerminal(selectedRef, selectedSymbol.cx, selectedSymbol.cy)
      return
    }
    if (event.key === 'Enter' && mode === 'connect' && selectedRef) {
      event.preventDefault()
      const node = { symbolRef: selectedRef, terminalRef: null }
      if (pending) {
        addConnection(pending, node)
        setPending(null)
      } else {
        setPending(node)
      }
      return
    }
    if (!selectedRef || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return

    event.preventDefault()
    const positionStep = event.altKey ? 0.001 : 0.005
    const sizeStep = event.altKey ? 0.002 : 0.01
    pushHistory()
    setSymbols((previous) =>
      previous.map((symbol) => {
        if (symbol.ref !== selectedRef) return symbol
        if (event.shiftKey) {
          const widthDelta = event.key === 'ArrowRight' ? sizeStep : event.key === 'ArrowLeft' ? -sizeStep : 0
          const heightDelta = event.key === 'ArrowDown' ? sizeStep : event.key === 'ArrowUp' ? -sizeStep : 0
          return {
            ...symbol,
            ...fitInside(
              symbol.cx,
              symbol.cy,
              Math.max(MIN_BOX, symbol.w + widthDelta),
              Math.max(MIN_BOX, symbol.h + heightDelta),
            ),
          }
        }
        const xDelta = event.key === 'ArrowRight' ? positionStep : event.key === 'ArrowLeft' ? -positionStep : 0
        const yDelta = event.key === 'ArrowDown' ? positionStep : event.key === 'ArrowUp' ? -positionStep : 0
        return { ...symbol, ...fitInside(symbol.cx + xDelta, symbol.cy + yDelta, symbol.w, symbol.h) }
      }),
    )
  }

  /* ------------------------------------------------------------------ キーボード */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (/input|textarea|select/i.test(el?.tagName ?? '')) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        void saveNow()
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo()
        else undo()
        return
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedRef) {
          e.preventDefault()
          deleteSymbol(selectedRef)
        } else if (selectedConn !== null) {
          e.preventDefault()
          pushHistory()
          setConnections((prev) => prev.filter((_, i) => i !== selectedConn))
          setSelectedConn(null)
        }
        return
      }
      if (e.key === 'Escape') {
        setPending(null)
        setSelectedRef(null)
        setSelectedConn(null)
        cancelActivePointer()
        return
      }
      const k = e.key.toLowerCase()
      if (k === 'v') setMode('select')
      if (k === 'b') setMode('box')
      if (k === 't') setMode('terminal')
      if (k === 'c') setMode('connect')
      if (/^[1-9]$/.test(e.key)) {
        const c = classes[Number(e.key) - 1]
        if (c) setClassKey(c.key)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cancelActivePointer, classes, redo, saveNow, selectedConn, selectedRef, undo])

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
      return { key, label: labelOf(key), color: colorOf(key), total: members.length, hidden }
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
      <header className="flex flex-none flex-wrap items-center gap-2 border-b border-white/10 bg-[#07111f] px-3 py-2.5 shadow-xl shadow-slate-950/20 sm:gap-3 sm:px-4">
        <Link
          to="/"
          className="icon-button border border-white/10 bg-white/[0.06] text-slate-200 hover:bg-white/10 hover:text-white"
          aria-label="図面一覧に戻る"
          title="図面一覧に戻る"
        >
          <ArrowLeft size={19} />
        </Link>

        <div className="min-w-0 flex-1 basis-[180px]">
          <div className="flex items-center gap-2 text-[9px] font-bold uppercase tracking-[0.18em] text-cyan-300">
            Annotation workspace
            {navigation.position > 0 && (
              <span className="rounded-full bg-white/[0.07] px-2 py-0.5 text-slate-400">
                {navigation.position} / {navigation.total}
              </span>
            )}
          </div>
          <h1 className="mt-0.5 truncate text-sm font-bold text-white sm:text-base">{project.name}</h1>
          <div className="mt-0.5 hidden truncate text-[10px] text-slate-400 sm:block">
            {project.sheet_no ? `シート ${project.sheet_no}` : 'シート番号未設定'} · {project.image_width}×
            {project.image_height}px · シンボル {symbols.length} · 配線 {connections.length}
          </div>
        </div>

        <nav className="flex items-center gap-1 rounded-xl border border-white/10 bg-black/20 p-1" aria-label="図面ページ移動">
          {navigation.previousId ? (
            <Link
              to={`/projects/${navigation.previousId}`}
              className="icon-button h-8 w-8 text-slate-300 hover:bg-white/10 hover:text-white"
              aria-label="前の図面"
              title="前の図面"
            >
              <ChevronLeft size={18} />
            </Link>
          ) : (
            <span className="icon-button h-8 w-8 text-slate-700" aria-hidden="true">
              <ChevronLeft size={18} />
            </span>
          )}
          <span className="min-w-12 text-center text-[10px] font-bold text-slate-400">
            {navigation.position || '—'} / {navigation.total || '—'}
          </span>
          {navigation.nextId ? (
            <Link
              to={`/projects/${navigation.nextId}`}
              className="icon-button h-8 w-8 text-slate-300 hover:bg-white/10 hover:text-white"
              aria-label="次の図面"
              title="次の図面"
            >
              <ChevronRight size={18} />
            </Link>
          ) : (
            <span className="icon-button h-8 w-8 text-slate-700" aria-hidden="true">
              <ChevronRight size={18} />
            </span>
          )}
        </nav>

        <Link to={`/projects/${projectId}/analysis`} className="icon-button border border-white/10 text-slate-300 hover:bg-white/10 hover:text-white" title="解析結果を表示" aria-label="解析結果を表示">
          <Cable size={17} />
        </Link>
        <button
          type="button"
          className="flex min-h-10 items-center gap-1.5 rounded-xl border border-cyan-400/30 bg-cyan-400/10 px-3 text-xs font-bold text-cyan-100 transition hover:bg-cyan-400/20 disabled:opacity-40"
          onClick={() => void applyPredictions()}
          disabled={aiApplying}
          aria-busy={aiApplying}
          title="保存済みの AI 推論結果を編集用シンボルとして取り込みます"
        >
          <Sparkles className={aiApplying ? 'animate-pulse' : undefined} size={15} />
          AI 推論
        </button>
        <button
          type="button"
          className="icon-button border border-white/10 bg-white/[0.06] text-slate-300 hover:bg-white/10 hover:text-white"
          onClick={() => void exportCurrentProject()}
          disabled={exporting}
          aria-busy={exporting}
          aria-label={exporting ? 'この図面を出力中' : 'この図面を出力'}
          title={exporting ? 'この図面を出力中' : 'この図面を出力'}
        >
          <Download className={exporting ? 'animate-pulse' : undefined} size={17} />
        </button>
        <button
          type="button"
          className={`flex min-h-10 items-center gap-2 rounded-xl border px-3 text-xs font-bold transition ${
            saveStatus === 'error'
              ? 'border-rose-400/40 bg-rose-400/10 text-rose-200 hover:bg-rose-400/20'
              : saveStatus === 'saved'
                ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
                : 'border-cyan-400/30 bg-cyan-400/10 text-cyan-100'
          }`}
          onClick={() => void saveNow()}
          aria-live="polite"
          aria-label={saveStatusLabel.long}
          title={saveStatusLabel.long}
        >
          {saveStatus === 'saving' ? (
            <CloudUpload className="animate-pulse" size={16} />
          ) : saveStatus === 'error' ? (
            <AlertCircle size={16} />
          ) : saveStatus === 'saved' ? (
            <CheckCircle2 size={16} />
          ) : (
            <Save size={16} />
          )}
          <span className="text-[10px] sm:hidden">{saveStatusLabel.short}</span>
          <span className="hidden sm:inline">{saveStatusLabel.long}</span>
        </button>
      </header>

      {/* ---------------- 編集ツールバー ---------------- */}
      <div className="thin-scroll flex flex-none items-center gap-2 overflow-x-auto border-b border-white/10 bg-slate-900 px-3 py-2 sm:px-4">
        <div role="toolbar" aria-label="アノテーションモード" className="flex items-center gap-1.5">
          {MODES.map(({ id, label, key, icon: Icon }) => (
            <button
              type="button"
              key={id}
              id={`annotation-mode-${id}`}
              onClick={() => {
                setMode(id)
                setPending(null)
              }}
              onKeyDown={(event) => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
                event.preventDefault()
                const currentIndex = MODES.findIndex((item) => item.id === id)
                const nextIndex =
                  event.key === 'Home'
                    ? 0
                    : event.key === 'End'
                      ? MODES.length - 1
                      : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + MODES.length) % MODES.length
                const nextMode = MODES[nextIndex].id
                setMode(nextMode)
                setPending(null)
                window.requestAnimationFrame(() => document.getElementById(`annotation-mode-${nextMode}`)?.focus())
              }}
              aria-pressed={mode === id}
              aria-label={`${label}（${key}キー）`}
              tabIndex={mode === id ? 0 : -1}
              className={`inline-flex min-h-10 items-center gap-2 whitespace-nowrap rounded-xl border px-3 text-xs font-bold transition ${
                mode === id
                  ? 'border-cyan-300 bg-cyan-300 text-slate-950 shadow-lg shadow-cyan-400/20'
                  : 'border-white/10 bg-white/[0.04] text-slate-300 hover:border-white/20 hover:bg-white/[0.08] hover:text-white'
              }`}
            >
              <Icon size={16} />
              <span className="hidden md:inline">{label}</span>
              <span className={`kbd border-0 ${mode === id ? 'bg-slate-950/10 text-slate-800' : 'bg-black/25 text-slate-400'}`}>
                {key}
              </span>
            </button>
          ))}
        </div>

        <span className="mx-1 h-7 w-px flex-none bg-white/10" />
        <label className="flex items-center gap-2 whitespace-nowrap text-[11px] font-bold text-slate-300">
          シンボル種別
          <span
            className="h-3.5 w-3.5 flex-none rounded-full ring-2 ring-white/15"
            style={{ background: colorOf(classKey) }}
          />
          <select
            className="min-h-10 w-48 rounded-xl border border-white/10 bg-white/[0.06] px-3 text-xs font-semibold text-white outline-none transition focus:border-cyan-400 md:w-60"
            value={classKey}
            onChange={(event) => setClassKey(event.target.value)}
          >
            {classes.map((item, index) => (
              <option key={item.key} value={item.key} className="bg-slate-900 text-white">
                {index < 9 ? `${index + 1}. ` : ''}
                {item.label}（{item.key}）
              </option>
            ))}
          </select>
        </label>

        <span className="mx-1 h-7 w-px flex-none bg-white/10" />
        <button
          type="button"
          className={`inline-flex min-h-10 items-center gap-2 whitespace-nowrap rounded-xl border px-3 text-xs font-bold transition ${
            layersOpen
              ? 'border-cyan-400/40 bg-cyan-400/10 text-cyan-100 hover:bg-cyan-400/20'
              : 'border-white/10 bg-white/[0.04] text-slate-300 hover:border-white/20 hover:bg-white/[0.08] hover:text-white'
          }`}
          onClick={() => setLayersOpen((value) => !value)}
          aria-expanded={layersOpen}
          aria-controls="annotation-layers"
          title="アノテーションの種類ごとに表示を切り替えます"
        >
          <Layers size={16} />
          <span className="hidden md:inline">レイヤー</span>
          {hiddenAnnotationCount > 0 && (
            <span
              className="rounded-full bg-amber-400/20 px-1.5 font-mono text-[10px] text-amber-200"
              title={`表示をオフにしたアノテーション ${hiddenAnnotationCount} 件`}
            >
              {hiddenAnnotationCount}
            </span>
          )}
        </button>

        <div className="flex-1" />
        <div className="flex items-center gap-1" role="group" aria-label={`編集履歴 ${histVersion}`}>
          <button
            type="button"
            className="icon-button text-slate-400 hover:bg-white/10 hover:text-white"
            onClick={undo}
            disabled={undoStack.current.length === 0}
            aria-label="元に戻す"
            title="元に戻す（Ctrl+Z）"
          >
            <Undo2 size={17} />
          </button>
          <button
            type="button"
            className="icon-button text-slate-400 hover:bg-white/10 hover:text-white"
            onClick={redo}
            disabled={redoStack.current.length === 0}
            aria-label="やり直す"
            title="やり直す（Ctrl+Shift+Z）"
          >
            <Redo2 size={17} />
          </button>
        </div>
        <button
          type="button"
          className={`icon-button hidden border lg:inline-flex ${
            inspectorOpen
              ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-200'
              : 'border-white/10 bg-white/[0.04] text-slate-400 hover:bg-white/10 hover:text-white'
          }`}
          onClick={() => setInspectorOpen((value) => !value)}
          aria-expanded={inspectorOpen}
          aria-controls="annotation-inspector"
          aria-label={inspectorOpen ? '詳細パネルを閉じる' : '詳細パネルを開く'}
          title={inspectorOpen ? '詳細パネルを閉じる' : '詳細パネルを開く'}
        >
          {inspectorOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
        </button>
      </div>

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
                void flushMetaSaveRef.current()
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
          {!inspectorOpen && (
            <button
              type="button"
              className="absolute right-3 top-3 z-10 flex min-h-10 items-center gap-2 rounded-xl border border-white/70 bg-white/90 px-3 text-xs font-bold text-slate-700 shadow-lg shadow-slate-900/10 backdrop-blur lg:hidden"
              onClick={() => setInspectorOpen(true)}
              aria-controls="annotation-inspector"
            >
              <PanelRightOpen size={16} /> 詳細
            </button>
          )}
          <div className="pointer-events-none absolute left-3 top-3 z-10 flex items-center gap-2 rounded-xl border border-white/70 bg-white/85 px-3 py-2 text-xs font-bold text-slate-700 shadow-lg shadow-slate-900/10 backdrop-blur sm:left-4 sm:top-4">
            {(() => {
              const activeMode = MODES.find((item) => item.id === mode) ?? MODES[0]
              const ActiveIcon = activeMode.icon
              return (
                <>
                  <ActiveIcon size={15} className="text-cyan-700" />
                  <span>{activeMode.label}</span>
                  <span className="kbd">{activeMode.key}</span>
                </>
              )
            })()}
          </div>
          {layersOpen && (
            <div
              id="annotation-layers"
              className="absolute right-3 top-14 z-10 flex max-h-[calc(100%-8rem)] w-64 flex-col overflow-hidden rounded-2xl border border-white/70 bg-white/95 text-slate-800 shadow-xl shadow-slate-900/15 backdrop-blur sm:right-4 lg:top-4"
              onPointerDown={(event) => event.stopPropagation()}
              onWheel={(event) => event.stopPropagation()}
              onKeyDown={(event) => {
                // キャンバスの編集処理（矩形/端子/配線の確定・選択枠の移動/削除）へ
                // 流すとパネル操作中に図面が変わるため、それらのキーは止める。
                // それ以外のキー（Undo・保存・モード切替等）は編集ショートカットとして
                // 通し、パネルにフォーカスがあっても使えるようにする
                const blocked =
                  event.key === 'Enter' ||
                  event.key === 'Delete' ||
                  event.key === 'Backspace' ||
                  event.key.startsWith('Arrow')
                if (blocked) event.stopPropagation()
              }}
            >
              <div className="flex flex-none items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
                <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-500">
                  Layers
                </span>
                <button
                  type="button"
                  className="min-h-8 rounded-lg px-2 text-[11px] font-bold text-cyan-700 hover:bg-cyan-50 disabled:text-slate-300"
                  onClick={resetLayers}
                  disabled={allLayersVisible}
                >
                  すべて表示
                </button>
              </div>
              <div
                className="thin-scroll flex-1 overflow-auto p-1.5"
                role="group"
                aria-label="アノテーションのレイヤー"
              >
                <LayerRow
                  icon={Cable}
                  color="#7c3aed"
                  label="配線"
                  count={connections.length}
                  state={showConnections ? 'on' : 'off'}
                  onToggle={() => setShowConnections((value) => !value)}
                />
                <LayerRow
                  icon={CircleDot}
                  color="#265f44"
                  label="端子"
                  count={terminalCount}
                  state={showTerminals ? 'on' : 'off'}
                  onToggle={() => setShowTerminals((value) => !value)}
                />
                {inferenceRefs.length > 0 && (
                  <LayerRow
                    icon={Sparkles}
                    color="#0891b2"
                    label="AI 検出"
                    count={inferenceRefs.length}
                    state={layerStateOf(hiddenInferenceCount, inferenceRefs.length)}
                    onToggle={() =>
                      setSymbolGroupHidden(inferenceRefs, hiddenInferenceCount === 0)
                    }
                  />
                )}
                {classLayerRows.length > 0 && (
                  <div className="px-2 pb-1 pt-2 text-[10px] font-bold text-slate-400">
                    シンボル（種別）
                  </div>
                )}
                {classLayerRows.map((layer) => (
                  <LayerRow
                    key={layer.key}
                    icon={Square}
                    color={layer.color}
                    label={layer.label}
                    count={layer.hidden > 0 ? `${layer.total - layer.hidden}/${layer.total}` : layer.total}
                    state={layerStateOf(layer.hidden, layer.total)}
                    onToggle={() =>
                      setSymbolGroupHidden(
                        symbols.filter((s) => s.class_key === layer.key).map((s) => s.ref),
                        layer.hidden === 0,
                      )
                    }
                  />
                ))}
              </div>
            </div>
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
            <svg
              className="pointer-events-none absolute left-0 top-0"
              width={iw}
              height={ih}
              viewBox={`0 0 ${iw} ${ih}`}
            >
              {/* 配線 */}
              {showConnections && connections.map((c, i) => {
                const a = nodePos(c.from_symbol_ref, c.from_terminal_ref)
                const b = nodePos(c.to_symbol_ref, c.to_terminal_ref)
                if (!a || !b) return null
                const active = selectedConn === i
                return (
                  <g key={`c${i}`}>
                    <line
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke={active ? '#9ACA3B' : c.kind === 'sheet_ref' ? '#db2777' : '#7c3aed'}
                      strokeWidth={(active ? 3.5 : 2) / zoom}
                      strokeDasharray={c.kind === 'sheet_ref' ? `${6 / zoom} ${4 / zoom}` : undefined}
                      opacity={0.85}
                    />
                    <circle cx={b.x} cy={b.y} r={4 / zoom} fill={active ? '#9ACA3B' : '#7c3aed'} />
                  </g>
                )
              })}
              {/* シンボル */}
              {visibleSymbols.map((s) => {
                const x = (s.cx - s.w / 2) * iw
                const y = (s.cy - s.h / 2) * ih
                const w = s.w * iw
                const h = s.h * ih
                const active = selectedRef === s.ref
                const color = colorOf(s.class_key)
                return (
                  <g key={s.ref}>
                    <rect
                      x={x}
                      y={y}
                      width={w}
                      height={h}
                      fill={color}
                      fillOpacity={active ? 0.18 : 0.08}
                      stroke={color}
                      strokeWidth={(active ? 2.5 : 1.5) / zoom}
                      strokeDasharray={s.origin === 'inference' ? `${6 / zoom} ${3 / zoom}` : undefined}
                    />
                    <text
                      x={x}
                      y={y - 3 / zoom}
                      fill={color}
                      fontSize={11 / zoom}
                      fontWeight="600"
                      style={{ paintOrder: 'stroke' }}
                      stroke="#fff"
                      strokeWidth={2.5 / zoom}
                    >
                      {s.origin === 'inference' ? 'AI ' : ''}
                      {s.ref}
                      {s.label ? ` ${s.label}` : ''}
                      {s.confidence != null ? ` ${Math.round(s.confidence * 100)}%` : ''}
                    </text>
                    {active &&
                      ([
                        [x, y],
                        [x + w, y],
                        [x, y + h],
                        [x + w, y + h],
                      ] as [number, number][]).map(([hx, hy], i) => (
                        <rect
                          key={i}
                          x={hx - 4 / zoom}
                          y={hy - 4 / zoom}
                          width={8 / zoom}
                          height={8 / zoom}
                          fill="#fff"
                          stroke={color}
                          strokeWidth={1.5 / zoom}
                        />
                      ))}
                    {showTerminals && s.terminals.map((t) => (
                      <g key={t.ref}>
                        <circle
                          cx={t.tx * iw}
                          cy={t.ty * ih}
                          r={4.5 / zoom}
                          fill="#fff"
                          stroke="#3f9067"
                          strokeWidth={2 / zoom}
                        />
                        <text
                          x={t.tx * iw + 6 / zoom}
                          y={t.ty * ih - 4 / zoom}
                          fill="#265f44"
                          fontSize={10 / zoom}
                          style={{ paintOrder: 'stroke' }}
                          stroke="#fff"
                          strokeWidth={2 / zoom}
                        >
                          {t.name}
                        </text>
                      </g>
                    ))}
                  </g>
                )
              })}
              {draftRect && (
                <rect
                  x={draftRect.x}
                  y={draftRect.y}
                  width={draftRect.w}
                  height={draftRect.h}
                  fill={colorOf(classKey)}
                  fillOpacity={0.15}
                  stroke={colorOf(classKey)}
                  strokeWidth={2 / zoom}
                  strokeDasharray={`${5 / zoom} ${3 / zoom}`}
                />
              )}
            </svg>
          </div>
          <div className="absolute bottom-3 left-3 z-20 flex items-center gap-1 rounded-2xl border border-white/70 bg-white/90 p-1.5 text-slate-700 shadow-xl shadow-slate-900/15 backdrop-blur sm:bottom-4 sm:left-4">
            <button
              type="button"
              className="icon-button h-9 w-9 hover:bg-slate-100"
              onClick={() => zoomAt(1 / 1.3, window.innerWidth / 2, window.innerHeight / 2)}
              aria-label="図面を縮小"
              title="図面を縮小"
            >
              <ZoomOut size={17} />
            </button>
            <button
              type="button"
              className="flex min-h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs font-bold hover:bg-slate-100"
              onClick={() => fit(false)}
              title="図面全体を表示"
            >
              <Maximize2 size={15} />
              <span className="hidden sm:inline">全体</span>
            </button>
            <button
              type="button"
              className="icon-button h-9 w-9 hover:bg-slate-100"
              onClick={() => zoomAt(1.3, window.innerWidth / 2, window.innerHeight / 2)}
              aria-label="図面を拡大"
              title="図面を拡大"
            >
              <ZoomIn size={17} />
            </button>
            <span className="min-w-12 border-l border-slate-200 pl-2 text-center font-mono text-[11px] font-bold text-slate-500">
              {Math.round(zoom * 100)}%
            </span>
          </div>

          <div className="absolute bottom-3 right-3 z-20 sm:bottom-4 sm:right-4">
            {shortcutsVisible && (
              <div className="mb-2 max-w-[calc(100vw-1.5rem)] rounded-2xl border border-white/30 bg-slate-950/95 p-3 text-[11px] font-semibold leading-5 text-white shadow-2xl backdrop-blur sm:max-w-sm">
                <div className="mb-2 font-black text-white">図面操作</div>
                ホイールで拡大縮小 ／ Alt+ドラッグまたは空白ドラッグで移動
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span className="kbd">B 描画</span>
                  <span className="kbd">T 端子</span>
                  <span className="kbd">C 配線</span>
                  <span className="kbd">V 選択</span>
                  <span className="kbd">Del 削除</span>
                  <span className="kbd">Ctrl+S 即時保存</span>
                </div>
              </div>
            )}
            <button
              type="button"
              className="flex min-h-10 items-center gap-2 rounded-xl border border-slate-300 bg-white px-3 text-xs font-black text-slate-950 shadow-lg shadow-slate-900/15 backdrop-blur hover:bg-slate-50"
              onClick={() => setShortcutsVisible((value) => !value)}
              aria-expanded={shortcutsVisible}
            >
              {shortcutsVisible ? <EyeOff size={15} /> : <Eye size={15} />}
              操作ヒント
            </button>
          </div>
        </div>

        {/* ---------------- 右パネル ---------------- */}
        {inspectorOpen && (
          <>
            <button
              type="button"
              className="absolute inset-0 z-20 bg-slate-950/55 backdrop-blur-[2px] lg:hidden"
              onClick={() => setInspectorOpen(false)}
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
                  onClick={closeInspector}
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
                onClick={() => setTab(key)}
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
                  setTab(nextKey)
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
                        if (symbolHidden) setSymbolHidden(s.ref, false)
                        setSelectedRef(s.ref)
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
                      onClick={() => setSymbolHidden(s.ref, !symbolHidden)}
                      aria-pressed={symbolHidden}
                      aria-label={symbolHidden ? `${s.ref}を表示` : `${s.ref}を非表示`}
                      title={symbolHidden ? `${s.ref}を表示` : `${s.ref}を非表示`}
                    >
                      {symbolHidden ? <EyeOff size={13} /> : <Eye size={13} />}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      onClick={() => deleteSymbol(s.ref)}
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
                      onClick={() => setSelectedConn(i)}
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
              onQueueSave={queueMetaSave}
            />
          </div>
            </aside>
          </>
        )}
      </div>
    </div>
  )
}

/* ==================================================================== 図面情報 */
function MetaPanel({
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

/* ==================================================================== レイヤー行 */
function LayerRow({
  icon: Icon,
  color,
  label,
  count,
  state,
  onToggle,
}: {
  icon: typeof Square
  color: string
  label: string
  count: React.ReactNode
  state: LayerState
  onToggle: () => void
}) {
  const title =
    state === 'on' ? `${label}を非表示` : state === 'partial' ? `${label}をすべて表示` : `${label}を表示`
  return (
    <div className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-100">
      <Icon size={14} style={{ color }} className="flex-none" />
      <span className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-700">{label}</span>
      <span className="font-mono text-[10px] text-slate-400">{count}</span>
      <button
        type="button"
        className={`icon-button h-7 w-7 hover:bg-slate-200 ${
          state === 'on' ? 'text-slate-600' : state === 'partial' ? 'text-amber-500' : 'text-slate-300'
        }`}
        onClick={onToggle}
        aria-pressed={state !== 'on'}
        aria-label={title}
        title={title}
      >
        {state === 'on' ? <Eye size={15} /> : <EyeOff size={15} />}
      </button>
    </div>
  )
}
