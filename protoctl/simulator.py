"""编号化工作区模拟器与双真实本地进程核对。

模拟器是纯内存的合成组件：一个编号工作区（如 WS-0001）持有一套协议
LTS 与当前状态，按给定的抽象动作号脚本推进。现场日志严格只记录
合成动作号、结果类别编号与可见状态整数投影，不记录任何符号名或
真实工作区信息。

编排方用 ``spawn`` 方式启动**两个真实本地进程**，各跑一个模拟器
实例（正确协议 P / 对照协议 Q），父进程只凭两份现场日志独立定位
首次分歧，并独立复核最小不可清理环。
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
from dataclasses import dataclass
from pathlib import Path

from .lts import LTS, StepKind


@dataclass(frozen=True, slots=True)
class WorkspaceJob:
    """一个编号工作区的作业（全部可 pickle，便于 spawn 子进程接收）。"""

    workspace_id: str
    lts: LTS
    script: tuple[int, ...]
    log_path: str
    repeat_cycle: tuple[int, ...] = ()   # 非空时脚本跑完后再重复该环
    cycle_rounds: int = 0
    expect_accept: bool | None = None    # 期望终态是否为接受态（None 不校验）


def _visible_or_none(lts: LTS, state) -> dict | None:
    if state is None:
        return None
    obs = lts.observe(state)
    if isinstance(obs, dict):
        return dict(obs)
    if hasattr(obs, "as_log"):
        return obs.as_log()
    if isinstance(obs, tuple):
        return {"obs": list(obs)}
    return {"obs": obs}


def run_job(job: WorkspaceJob) -> dict:
    """在当前进程内驱动一个编号工作区（子进程入口，也是可单测的纯函数）。"""
    lts = job.lts
    state = lts.initial
    log_rows: list[dict] = []
    reached_accept = state in lts.accept
    junction_visible: dict | None = None

    def do(action: int, step: int) -> None:
        nonlocal state, reached_accept
        result = lts.step(state, action)
        row: dict = {
            "step": step,
            "action": int(action),
            "result": int(result.kind),
            "visible": None,
        }
        if result.kind is StepKind.MOVED:
            state = result.target
            row["visible"] = _visible_or_none(lts, state)
            reached_accept = reached_accept or state in lts.accept
        log_rows.append(row)

    step = 0
    for action in job.script:
        step += 1
        do(action, step)

    cycle_seen: list[dict] = []
    if job.repeat_cycle:
        # 记录环起点的可见状态，每轮结束后必须回到同一可见状态
        junction_visible = _visible_or_none(lts, state)
        for _ in range(job.cycle_rounds):
            for action in job.repeat_cycle:
                step += 1
                do(action, step)
            cycle_seen.append(_visible_or_none(lts, state))

    Path(job.log_path).parent.mkdir(parents=True, exist_ok=True)
    header = {
        "workspace_id": job.workspace_id,
        "pid": os.getpid(),
        "protocol": lts.name,
        "rows": log_rows,
    }
    Path(job.log_path).write_text(
        json.dumps(header, ensure_ascii=False, indent=1), encoding="utf-8",
    )

    accept_ok = None
    if job.expect_accept is not None:
        accept_ok = reached_accept == job.expect_accept
    return {
        "workspace_id": job.workspace_id,
        "pid": os.getpid(),
        "log_path": job.log_path,
        "rows": len(log_rows),
        "reached_accept": reached_accept,
        "expect_accept": job.expect_accept,
        "accept_check": accept_ok,
        "junction_visible": junction_visible,
        "cycle_round_visible": cycle_seen,
    }


def _worker_entry(job: WorkspaceJob, queue: "mp.Queue") -> None:
    """spawn 子进程的顶层入口：跑完作业后把摘要写回队列。"""
    queue.put(run_job(job))


@dataclass(frozen=True, slots=True)
class CrosscheckResult:
    p_pid: int
    q_pid: int
    log_p: str
    log_q: str
    first_divergence_step: int          # 1 基；-1 表示无分歧
    first_divergence_word: tuple[int, ...]
    divergence_result_pair: tuple[int, int] | None
    divergence_visible_pair: tuple[dict, dict] | None
    trap_confirmed: bool
    accept_checks: tuple[bool | None, bool | None]


def _load_log(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def divergence_from_logs(log_p: dict, log_q: dict) -> tuple[int, tuple[int, ...], dict | None]:
    """父进程只凭两份现场日志独立定位首次允许差异。"""
    rows_p, rows_q = log_p["rows"], log_q["rows"]
    word: list[int] = []
    for i, (rp, rq) in enumerate(zip(rows_p, rows_q), start=1):
        word.append(rp["action"])
        if rp["result"] != rq["result"] or rp["visible"] != rq["visible"]:
            detail = {"step": i, "result_p": rp["result"], "result_q": rq["result"],
                      "visible_p": rp["visible"], "visible_q": rq["visible"]}
            return i, tuple(word), detail
    return -1, (), None


def run_two_process_crosscheck(
    job_p: WorkspaceJob,
    job_q: WorkspaceJob,
) -> CrosscheckResult:
    """启动两个真实本地进程（spawn），分别驱动 P、Q 编号工作区。

    显式创建两个独立 ``Process``（而非进程池复用 worker），
    保证 P、Q 各有不同 PID；父进程经 Queue 收集摘要，
    并只凭两份现场日志独立定位首次分歧。
    """
    ctx = mp.get_context("spawn")
    queue: "mp.Queue" = ctx.Queue()
    proc_p = ctx.Process(target=_worker_entry, args=(job_p, queue))
    proc_q = ctx.Process(target=_worker_entry, args=(job_q, queue))
    proc_p.start()
    proc_q.start()
    summaries: list[dict] = [queue.get(), queue.get()]
    proc_p.join()
    proc_q.join()
    if proc_p.exitcode != 0 or proc_q.exitcode != 0:
        raise RuntimeError(
            f"子进程异常退出：P exit={proc_p.exitcode}，Q exit={proc_q.exitcode}"
        )
    by_ws = {s["workspace_id"]: s for s in summaries}
    summary_p = by_ws[job_p.workspace_id]
    summary_q = by_ws[job_q.workspace_id]

    log_p = _load_log(job_p.log_path)
    log_q = _load_log(job_q.log_path)
    step, word, detail = divergence_from_logs(log_p, log_q)

    # 独立复核最小不可清理环：每轮结束的可见状态必须等于环起点
    trap_confirmed = True
    if summary_q["junction_visible"] is not None:
        junction = summary_q["junction_visible"]
        trap_confirmed = all(
            row == junction for row in summary_q["cycle_round_visible"]
        ) and summary_q["reached_accept"] is False

    return CrosscheckResult(
        p_pid=summary_p["pid"],
        q_pid=summary_q["pid"],
        log_p=job_p.log_path,
        log_q=job_q.log_path,
        first_divergence_step=step,
        first_divergence_word=word,
        divergence_result_pair=(
            (detail["result_p"], detail["result_q"]) if detail else None
        ),
        divergence_visible_pair=(
            (detail["visible_p"], detail["visible_q"]) if detail else None
        ),
        trap_confirmed=trap_confirmed,
        accept_checks=(summary_p["accept_check"], summary_q["accept_check"]),
    )
