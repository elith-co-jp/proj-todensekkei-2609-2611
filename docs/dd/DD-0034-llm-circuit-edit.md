# DD-0034: LLM などによる図面の変更に備える（変更の操作・試す・適用・取り消し）

- 状態: レビュー中
- 作成日: 2026-11-18
- 関連文書: [DD-0033](DD-0033-timers-and-reading-errors.md), [DD-0032](DD-0032-synthetic-logic-validation.md), [DD-0031](DD-0031-consistency-check.md), [DD-0029](DD-0029-circuit-code-whatif-impact.md)
- 結果: [docs/evidence/DD-0034-edit-roundtrip-20261118.md](../evidence/DD-0034-edit-roundtrip-20261118.md)

## 1. 目的

これまでの「もしも」（DD-0029・DD-0033）は、図面を変えずに断線・溶着・タイマーの時間などを仮定して解くだけだった。
将来は LLM が「この段にインターロックを足して」「この接点を b 接点に」といった変更案を出し、人が確かめて図面に反映する、
という使い方をしたい。そのために、次をシステム側に用意する。LLM の呼び出しそのものは本 DD では行わない。

1. 図面への変更を、決まった形の操作（JSON）で表す。形は JSON Schema で公開し、そのまま LLM のツール定義になる。
2. 操作を試す（図面は変えない）と、差分・動作の違い・波及範囲・検査の結果が返る。LLM はこれを見て自分の案を直せる。
3. 人（または承認された仕組み）が適用すると、変更セットとして記録され、あとから取り消せる。

## 2. 変更の操作

`services/circuit_edit.py` の `OPS`。操作の並び（最大 200 個）は全体で 1 つの変更で、1 つでも不正なら何も変えない（原子的）。
誤りは「何番目の操作の何が悪いか」を返す。

| 操作 | 内容 |
|------|------|
| add_component | 部品（a/b 接点・リレーコイル・表示灯・電磁弁・ダイオード、接続点など）を足す |
| remove_component | 記号を消す（その記号の配線も消える） |
| set_label | ラベル（器具番号）を変える。接点なら、どのリレーで開閉するかが変わる |
| set_class | 種類を変える（a 接点 ↔ b 接点 など） |
| set_attribute | 属性を変える・消す（タイマーの種類・時間、コイル抵抗など） |
| connect / disconnect | 2 つの端子の間の配線をつなぐ・外す |
| insert_in_series | 2 端子の間の配線を切り、部品を直列に入れる（インターロックの追加など）。電線番号は from 側に残す |
| set_wire_no | 電線番号を変える |

端子は `{"symbol_ref": "SYM-0003", "terminal": "1"}` のように、記号の参照名と端子の名前（2 端子の部品は上が 1、下が 2）か
端子の参照名で指す。新しい記号の参照名は自動で `EDIT-0001` から振る（指定も可）。整合性チェック（DD-0031）の直し方は
`fix_to_op` で同じ操作に変換でき、人の直しと LLM の直しが同じ道を通る。

安全のため、操作はデータだけで、コード（`from_python` など）は受け付けない。

## 3. 試す（preview）

`preview(pages, ops)` は変更後の図面を作り、変更前と比べて次を返す。図面は変えない。

| 項目 | 内容 |
|------|------|
| notes | 操作ごとの説明（「SYM-0003 の種類を a 接点 → b 接点」など） |
| diff | 記号の追加・削除・変更、配線の追加・削除 |
| netlist_diff | 正規化したネットリスト（つながりの表現。節点は母線名か最小の端子名）の unified diff。座標や参照名の違いに左右されない |
| circuit | リレー・入力・タイマーの増減 |
| behaviour | 入力の組み合わせ（全 OFF・ランダム・変更に関わる入力を 1 つずつ反転、最大 24 通り）ごとのコイル・負荷の ON/OFF の違い、新しい警告 |
| impact | 変更に関わる器具ごとの波及範囲（DD-0029 の影響分析） |
| validation | 新しく出た回路の注意、整合性チェックで新しく出た指摘・解消した指摘、誤りの数 |
| base_hash / after_hash | 変更前・変更後の図面のハッシュ |

## 4. 適用・履歴・取り消し

- 適用（`edit_apply`）は試した時の `base_hash` を受け取り、今の図面と違えば適用しない（409）。試した後に誰かが図面を変えた
  まま、古い案を当ててしまうのを防ぐ。
- 適用すると変更セット（表 `circuit_change_sets`: 題・理由・誰が（user / llm など）・操作・説明・結果の要約・変更前の図面・
  変更後のハッシュ・状態）を残す。
- 取り消し（`edit_undo`）は変更前の図面に戻す。適用の後に図面が変わっていれば、`force` でない限り戻さない（409）。

## 5. API と画面

| API | 内容 |
|-----|------|
| GET /api/circuit/edit/tools | LLM 用のツール定義（`get_circuit_context`・`preview_circuit_edit`、input_schema は JSON Schema）と操作の定義 |
| POST /api/circuit/edit/context | 変更する図面の記号・配線・リレー・入力・タイマー・回路コード・base_hash |
| POST /api/circuit/edit/preview | 試す |
| POST /api/circuit/edit/apply | 適用（409: 図面が変わっている、422: 操作の誤り） |
| GET /api/circuit/edit/history | 変更セットの履歴 |
| POST /api/circuit/edit/{id}/undo | 取り消し（409 なら force で戻せる） |

回路ページに「図面の変更」パネルを足した。操作の JSON を貼って「試す」、結果を見て「適用」、履歴から「取り消す」。
LLM が出した操作をそのまま貼って確かめる場所でもある。「適用」は、試した内容から JSON を変えると押せなくなる。

## 6. LLM とつなぐときの形（将来）

ツール定義はそのまま Claude の Messages API の `tools` に渡せる形（name・description・input_schema）にしてある。
つなぐ側は次のように、LLM には試すことだけを許し、適用は人の確認の後に行う。

```python
tools = requests.get(f"{base}/api/circuit/edit/tools").json()["tools"]
messages = [{"role": "user", "content": "ER21216 の 3 段目に 52X の b 接点をインターロックとして入れて"}]
while True:
    resp = client.messages.create(model=MODEL, max_tokens=4096, tools=tools, messages=messages)
    messages.append({"role": "assistant", "content": resp.content})
    calls = [b for b in resp.content if b.type == "tool_use"]
    if not calls:
        break
    results = []
    for call in calls:
        path = {"get_circuit_context": "context", "preview_circuit_edit": "preview"}[call.name]
        body = {"project_ids": project_ids, **call.input}
        results.append({"type": "tool_result", "tool_use_id": call.id,
                        "content": json.dumps(requests.post(f"{base}/api/circuit/edit/{path}", json=body).json())})
    messages.append({"role": "user", "content": results})
# 最後に試した ops と base_hash を人に見せ、承認されたら /edit/apply（author="llm"）
```

適用はツールにしない（LLM が勝手に図面を変えない）。取り消しは人の操作として残す。

## 7. 検証

`edit_roundtrip(seed)`（`analysis/synthetic_circuits/logic_eval.py`）は、正解つきの合成回路（DD-0032）の正本を
3 通りに変え（段にインターロックの b 接点を直列に足す・接点の a/b を反転・接点の器具を別のリレーに）、同じ変更を
変更の操作で図面に当てて、変更後の図面のシミュレーションが変えた正本の正解（oracle）と一致するかを数える。
発振する回路は、正解が「落ち着かない」とき、シミュレーションも同じ警告を出せば一致とみなす。

```
python -m analysis.tools.validate_circuit_logic --seeds 2 --edits 60
```

60 回路・5,040 通りの比較ですべて一致した（[結果](../evidence/DD-0034-edit-roundtrip-20261118.md)）。
テストは `tests/test_circuit_edit.py`（ツール定義が正しい JSON Schema か、インターロックの差し込み、誤りの原子性、
各操作、合成回路の往復、整合性チェックの直しの変換、API の試す・適用・履歴・取り消し・409・422）と
`frontend/src/pages/circuit/EditPanel.test.tsx`。

## 8. 残り

- LLM の呼び出しと承認の流れ（誰が適用できるか）は、つなぐ時に決める。
- 複数の図面にまたがる変更は操作ごとに project_id を指すことでできるが、行先札（DD-0030）の付け替えを伴う変更の
  検証はまだ少ない。
