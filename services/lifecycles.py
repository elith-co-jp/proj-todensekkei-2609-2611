"""状態の移り変わり（ライフサイクル）を 1 か所に書く（DD-0047）。

これまで、学習ジョブ・裏の解析ジョブ・回路の変更セットの状態は、文字列の列（status / decision）を
あちこちで直接書き換えて決めていた。どの状態からどこへ移れるかはコードを読み通さないと分からず、
「完了したジョブをもう一度失敗にする」「判定済みの学習をもう一度採用する」といった移り方を止める仕組みが無かった。

ここでは、状態と移り方を python-statemachine（https://github.com/fgmacedo/python-statemachine、MIT）の
宣言で書く。状態の値は DB・API の文字列そのまま（値は変えない）。機械はモデル（ORM の行・dataclass）の
列を読み書きするだけで、状態を別に持たない。

- 許されない移り方は ``TransitionNotAllowed`` になる（呼ぶ側で 400・409 などの応答に変える）。
- 状態図は ``python scripts/state_diagrams.py`` で Mermaid に書き出せる（docs/state-machines.md。
  tests/test_lifecycles.py が、文書が定義と食い違っていないかを確かめる）。

使い方::

    lifecycles.training_run(run).succeed()        # running → success
    lifecycles.training_decision(run).adopt()     # pending → adopted
"""

from __future__ import annotations

from typing import Any

from statemachine import State, StateMachine
from statemachine.exceptions import TransitionNotAllowed

__all__ = [
    "AnalysisJobLifecycle",
    "ChangeSetLifecycle",
    "TrainingDecisionLifecycle",
    "TrainingRunLifecycle",
    "TransitionNotAllowed",
    "MACHINES",
    "analysis_job",
    "change_set",
    "fail_if_running",
    "training_decision",
    "training_run",
]


class TrainingRunLifecycle(StateMachine):
    """学習ジョブ（TrainingRun.status）。学習のスレッドが終われば成功か失敗のどちらかで止まる。

    アプリを止めると学習のスレッドも消える。起動時に running のまま残った行は ``interrupt`` で失敗にする
    （そのままだと「別の学習が実行中です」で次の学習を始められなくなる）。
    """

    running = State("学習中", initial=True, value="running")
    success = State("完了", final=True, value="success")
    failed = State("失敗", final=True, value="failed")

    succeed = running.to(success)
    fail = running.to(failed)
    interrupt = running.to(failed)


class TrainingDecisionLifecycle(StateMachine):
    """学習済みモデルの採用判定（TrainingRun.decision）。

    学習中・失敗の間は判定の対象外（空文字）。学習が終わると、比べる相手があれば採用待ち、
    初回で比べる相手が無ければその場で採用になる。採用待ちからは利用者が採用か見送りを選ぶ。
    """

    undecided = State("対象外", initial=True, value="")
    pending = State("採用待ち", value="pending")
    adopted = State("採用", final=True, value="adopted")
    rejected = State("見送り", final=True, value="rejected")

    await_review = undecided.to(pending)
    auto_adopt = undecided.to(adopted)
    adopt = pending.to(adopted)
    reject = pending.to(rejected)


class AnalysisJobLifecycle(StateMachine):
    """裏の解析ジョブ（DD-0039 の AnalysisJob.status）。

    中止は状態ではなく ``cancel_requested`` の印で受け付け、解析中の図面を終えたところで止まる。
    止まったとき、残りの図面があれば中止、全部終えていれば完了になる。
    """

    running = State("解析中", initial=True, value="running")
    done = State("完了", final=True, value="done")
    cancelled = State("中止", final=True, value="cancelled")
    failed = State("失敗", final=True, value="failed")

    request_cancel = running.to.itself(internal=True, on="_mark_cancel_requested")
    finish = running.to(cancelled, cond="stopped_early") | running.to(done)
    fail = running.to(failed)

    def stopped_early(self) -> bool:
        job = self.model
        return bool(job.cancel_requested) and job.done < len(job.project_ids)

    def _mark_cancel_requested(self) -> None:
        self.model.cancel_requested = True


class ChangeSetLifecycle(StateMachine):
    """回路の変更セット（DD-0034 の CircuitChangeSet.status）。取り消しは 1 度だけ。"""

    applied = State("適用中", initial=True, value="applied")
    undone = State("取り消し済み", final=True, value="undone")

    undo = applied.to(undone)


# 状態図を書き出す・確かめる対象（名前 → 機械）
MACHINES: dict[str, type[StateMachine]] = {
    "TrainingRun.status": TrainingRunLifecycle,
    "TrainingRun.decision": TrainingDecisionLifecycle,
    "AnalysisJob.status": AnalysisJobLifecycle,
    "CircuitChangeSet.status": ChangeSetLifecycle,
}


def fail_if_running(machine: TrainingRunLifecycle) -> bool:
    """学習が失敗で終わった。まだ学習中なら失敗にする（もう終わっていれば何もしない）。失敗にしたら True。"""
    if not machine.running.is_active:
        return False
    machine.fail()
    return True


def _bind(machine: type[StateMachine], model: Any, field: str) -> Any:
    return machine(model, state_field=field)


def training_run(run: Any) -> TrainingRunLifecycle:
    return _bind(TrainingRunLifecycle, run, "status")


def training_decision(run: Any) -> TrainingDecisionLifecycle:
    return _bind(TrainingDecisionLifecycle, run, "decision")


def analysis_job(job: Any) -> AnalysisJobLifecycle:
    return _bind(AnalysisJobLifecycle, job, "status")


def change_set(row: Any) -> ChangeSetLifecycle:
    return _bind(ChangeSetLifecycle, row, "status")
