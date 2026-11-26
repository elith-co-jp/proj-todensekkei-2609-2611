"""状態の移り方（services/lifecycles.py、DD-0047）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import database
from models import TrainingRun
from services import lifecycles
from services.lifecycles import TransitionNotAllowed

ROOT = Path(__file__).resolve().parents[1]


def test_training_run_moves_once_from_running():
    run = SimpleNamespace(status="running")
    lifecycles.training_run(run).succeed()
    assert run.status == "success"
    # 終わったジョブを失敗に書き換えない
    with pytest.raises(TransitionNotAllowed):
        lifecycles.training_run(run).fail()
    assert run.status == "success"


def test_fail_if_running_keeps_finished_runs():
    run = SimpleNamespace(status="success")
    assert lifecycles.fail_if_running(lifecycles.training_run(run)) is False
    assert run.status == "success"
    run = SimpleNamespace(status="running")
    assert lifecycles.fail_if_running(lifecycles.training_run(run)) is True
    assert run.status == "failed"


def test_training_decision_flow():
    run = SimpleNamespace(decision="")
    lifecycles.training_decision(run).await_review()
    assert run.decision == "pending"
    with pytest.raises(TransitionNotAllowed):  # 採用待ちは自動採用しない
        lifecycles.training_decision(run).auto_adopt()
    lifecycles.training_decision(run).reject()
    assert run.decision == "rejected"
    with pytest.raises(TransitionNotAllowed):  # 判定済みはやり直さない
        lifecycles.training_decision(run).adopt()

    first = SimpleNamespace(decision="")
    lifecycles.training_decision(first).auto_adopt()
    assert first.decision == "adopted"


def _job(**kw):
    base = {"status": "running", "cancel_requested": False, "done": 0, "project_ids": [1, 2]}
    return SimpleNamespace(**{**base, **kw})


def test_analysis_job_finish_depends_on_cancel_and_progress():
    job = _job(done=1)
    lifecycles.analysis_job(job).request_cancel()
    assert job.cancel_requested is True and job.status == "running"
    lifecycles.analysis_job(job).finish()
    assert job.status == "cancelled"

    # 中止を頼んでも、全部終えていれば完了
    job = _job(done=2, cancel_requested=True)
    lifecycles.analysis_job(job).finish()
    assert job.status == "done"

    job = _job()
    lifecycles.analysis_job(job).fail()
    assert job.status == "failed"
    with pytest.raises(TransitionNotAllowed):
        lifecycles.analysis_job(job).request_cancel()
    assert job.cancel_requested is False


def test_change_set_undo_once():
    row = SimpleNamespace(status="applied")
    lifecycles.change_set(row).undo()
    assert row.status == "undone"
    with pytest.raises(TransitionNotAllowed):
        lifecycles.change_set(row).undo()


def test_states_match_documented_values():
    """状態の値は DB・API・画面（frontend/src/types.ts）の文字列と同じでなければならない。"""
    values = {name: [s.value for s in m.states] for name, m in lifecycles.MACHINES.items()}
    assert values == {
        "TrainingRun.status": ["running", "success", "failed"],
        "TrainingRun.decision": ["", "pending", "adopted", "rejected"],
        "AnalysisJob.status": ["running", "done", "cancelled", "failed"],
        "CircuitChangeSet.status": ["applied", "undone"],
    }


def test_state_diagram_doc_is_up_to_date():
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "state_diagrams.py"), "--check"],
                         capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr


def _insert_run(status: str, decision: str = "") -> int:
    db = database.SessionLocal()
    try:
        run = TrainingRun(status=status, decision=decision, project_ids_json="[]")
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


def test_recover_interrupted_runs_unblocks_training():
    """前回のプロセスで学習中のまま残った行は、起動時に失敗になり、次の学習を止めない。"""
    from services.training_service import recover_interrupted_runs, running_training

    stale = _insert_run("running")
    done = _insert_run("success", "pending")
    db = database.SessionLocal()
    try:
        assert running_training(db) is not None
    finally:
        db.close()

    assert recover_interrupted_runs() == 1
    assert recover_interrupted_runs() == 0

    db = database.SessionLocal()
    try:
        assert running_training(db) is None
        stale_row = db.get(TrainingRun, stale)
        assert stale_row.status == "failed"
        assert stale_row.finished_at is not None
        assert "中断" in (stale_row.log_tail or "")
        done_row = db.get(TrainingRun, done)
        assert (done_row.status, done_row.decision) == ("success", "pending")
    finally:
        db.close()
