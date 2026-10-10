import type { ProjectScopedSave } from '../../utils/workspace'

/**
 * プロジェクト単位の保存キュー（アノテーション / 図面情報で同型）。
 * 直前に保存が確定した内容・未送信キュー・失敗分・送信中のものを
 * ページ遷移や再読込をまたいで追跡するための状態一式。
 */
export type SaveQueueStore<T> = {
  /** 送信待ちのペイロード（projectId → 最新の1件） */
  queued: Map<number, ProjectScopedSave<T>>
  /** 送信に失敗し未送信のままのペイロード */
  failed: Map<number, ProjectScopedSave<T>>
  /** 現在サーバーへ送信中のペイロード */
  active: ProjectScopedSave<T> | null
  /** このキューの送信処理が実行中か */
  inFlight: boolean
  /** サーバー保存が確定した最新ペイロードのキー */
  lastSavedKeys: Map<number, string>
  /** サーバー保存が確定した最新ペイロード */
  lastSavedPayloads: Map<number, T>
  /** プロジェクト読込後に保存が確定した回数（読込競合の検出用） */
  generation: Map<number, number>
  /** デバウンス中のタイマー */
  timers: Map<number, number>
  /** キューを処理する送信関数（レンダーごとに最新クロージャで差し替える） */
  flush: () => Promise<void>
}

export function createSaveQueueStore<T>(): SaveQueueStore<T> {
  return {
    queued: new Map(),
    failed: new Map(),
    active: null,
    inFlight: false,
    lastSavedKeys: new Map(),
    lastSavedPayloads: new Map(),
    generation: new Map(),
    timers: new Map(),
    flush: async () => undefined,
  }
}
