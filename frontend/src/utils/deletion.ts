import type { DeletePreview } from '../types'

/** 削除の確認に並べる「一緒に消えるもの」「残るもの」（DD-0052）。件数が 0 のものは出さない。 */
export interface DeleteLists {
  removes: string[]
  keeps: string[]
}

function line(count: number, text: string, note?: string): string[] {
  return count > 0 ? [`${text} ${count.toLocaleString()} 件${note ? `（${note}）` : ''}`] : []
}

/** 図面ごと消すとき */
export function projectDeleteLists(p: DeletePreview): DeleteLists {
  const c = p.counts
  return {
    removes: [
      `図面 ${p.project_ids.length} 枚（登録した画像・ページの情報）`,
      ...line(c.symbols, 'アノテーションの記号'),
      ...line(c.terminals, '記号の端子'),
      ...line(c.connections, '配線'),
      ...line(c.predictions, 'AI の検出候補'),
      ...line(c.structures, '構造解析の結果'),
      ...line(c.drawing_sets, '図面セットへの登録', 'セット自体とほかの図面は残ります'),
      ...line(c.change_sets, '回路の変更履歴', '図面が無いと元に戻せないため'),
      ...line(c.image_files, 'ほかの図面が使っていない画像ファイル'),
    ],
    keeps: [
      ...(c.training_runs > 0
        ? [`学習の履歴（${c.training_runs} 件）と、学習でできたモデル。履歴の中の図面は「削除済み」になります`]
        : []),
      'ほかの図面、登録済みのモデル、I/O 表、書き出し済みの ZIP',
    ],
  }
}

/** AI の解析結果だけを消すとき */
export function analysisClearLists(p: DeletePreview): DeleteLists {
  const c = p.counts
  const removes = [...line(c.predictions, 'AI の検出候補'), ...line(c.structures, '構造解析の結果')]
  return {
    removes: removes.length > 0
      ? [...removes, 'エディタの「AI 検出の確認状況」の集計（もう一度 AI 解析すると戻ります）']
      : ['消す解析結果はありません'],
    keeps: [
      `図面 ${p.project_ids.length} 枚と、その画像`,
      'アノテーション（記号・端子・配線。AI から取り込んだものも含む）',
      '図面セット、回路の変更履歴、学習の履歴とモデル',
    ],
  }
}
