"""図面の解析をバックグラウンドで回すジョブ（DD-0039）。

全図面の解析は 1 枚約 5 秒で、90 枚なら 7〜8 分かかる。1 回の HTTP 要求で待つと進み具合が見えず、途中で 1 枚失敗すると
全部が保存されなかった。ジョブは裏のスレッドで 1 枚ずつ解析して図面ごとに保存し、失敗した図面は飛ばして続ける。
画面は進み具合を取りに来る。中止すると、解析中の図面を終えたところで止まる（保存済みの図面はそのまま）。

ジョブはプロセスの中だけに持つ（EXE・1 台のサーバの運用を前提にする。再起動すると一覧は消えるが、保存した結果は残る）。
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from models import utc_now_naive
from services.jst import iso_jst

# 新しいものから残すジョブの数
KEEP_JOBS = 20


@dataclass
class AnalysisJob:
    id: int
    project_ids: list[int]
    conf: float
    connector_conf: float | None
    status: str = "running"            # running / done / cancelled / failed
    done: int = 0
    current: str | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    cancel_requested: bool = False
    started_at: Any = field(default_factory=utc_now_naive)
    finished_at: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "total": len(self.project_ids), "done": self.done,
            "current": self.current, "conf": self.conf, "connector_conf": self.connector_conf,
            "results": list(self.results), "failures": list(self.failures), "error": self.error,
            "cancel_requested": self.cancel_requested,
            "detection_count": sum(r.get("detections", 0) for r in self.results),
            "started_at": iso_jst(self.started_at), "finished_at": iso_jst(self.finished_at),
        }


_jobs: dict[int, AnalysisJob] = {}
_ids = itertools.count(1)
_lock = threading.Lock()


def get(job_id: int) -> AnalysisJob | None:
    with _lock:
        return _jobs.get(job_id)


def recent() -> list[AnalysisJob]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j.id, reverse=True)


def running() -> bool:
    with _lock:
        return any(j.status == "running" for j in _jobs.values())


def cancel(job_id: int) -> AnalysisJob | None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None and job.status == "running":
            job.cancel_requested = True
        return job


def start(project_ids: list[int], conf: float, connector_conf: float | None,
          work: Callable[[AnalysisJob], None], *, on_finish: Callable[[], None] | None = None) -> AnalysisJob:
    """ジョブを作り、work(job) を裏のスレッドで回す。work は図面ごとに progress() を呼ぶ。終われば on_finish を呼ぶ。"""
    with _lock:
        job = AnalysisJob(next(_ids), list(project_ids), conf, connector_conf)
        _jobs[job.id] = job
        for old in sorted(_jobs)[:-KEEP_JOBS]:
            if _jobs[old].status != "running":
                del _jobs[old]

    def run() -> None:
        try:
            work(job)
            with _lock:
                job.status = "cancelled" if job.cancel_requested and job.done < len(job.project_ids) else "done"
        except Exception as exc:  # noqa: BLE001  ジョブの失敗は状態で返す（スレッドの外へは出さない）
            with _lock:
                job.status = "failed"
                job.error = str(exc) if isinstance(exc, RuntimeError) else "解析に失敗しました"
        finally:
            with _lock:
                job.current = None
                job.finished_at = utc_now_naive()
            if on_finish is not None:
                on_finish()

    threading.Thread(target=run, name=f"analysis-job-{job.id}", daemon=True).start()
    return job


def progress(job: AnalysisJob, event: dict[str, Any], next_name: str | None = None) -> None:
    """1 図面が終わった（ok なら result、失敗なら error）。next_name は次に解析する図面の名前。"""
    with _lock:
        job.done += 1
        if event.get("ok"):
            job.results.append(event["result"])
        else:
            job.failures.append({"project_id": event["project_id"], "name": event.get("name"),
                                 "message": event.get("error") or "解析に失敗しました"})
        job.current = next_name


def set_current(job: AnalysisJob, name: str | None) -> None:
    with _lock:
        job.current = name
