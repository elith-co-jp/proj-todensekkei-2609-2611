import { describe, expect, it } from 'vitest'

import type { DeletePreview } from '../types'
import { analysisClearLists, projectDeleteLists } from './deletion'

const preview = (counts: Partial<DeletePreview['counts']>): DeletePreview => ({
  project_ids: [1, 2],
  names: ['a', 'b'],
  missing_ids: [],
  counts: {
    symbols: 0, terminals: 0, connections: 0, predictions: 0, structures: 0,
    drawing_sets: 0, change_sets: 0, image_files: 0, training_runs: 0, ...counts,
  },
  blockers: [],
  analysis_blockers: [],
})

describe('削除の確認に並べる内容', () => {
  it('図面ごと消すときは件数のあるものだけ並べ、学習の履歴は残るものに出す', () => {
    const lists = projectDeleteLists(preview({ symbols: 1200, change_sets: 1, training_runs: 3 }))
    expect(lists.removes).toEqual([
      '図面 2 枚（登録した画像・ページの情報）',
      'アノテーションの記号 1,200 件',
      '回路の変更履歴 1 件（図面が無いと元に戻せないため）',
    ])
    expect(lists.keeps[0]).toContain('学習の履歴（3 件）')
  })

  it('解析結果だけを消すときは、アノテーションが残ることを示す', () => {
    expect(analysisClearLists(preview({})).removes).toEqual(['消す解析結果はありません'])
    const lists = analysisClearLists(preview({ predictions: 4, structures: 2 }))
    expect(lists.removes.slice(0, 2)).toEqual(['AI の検出候補 4 件', '構造解析の結果 2 件'])
    expect(lists.keeps.join()).toContain('アノテーション')
  })
})
