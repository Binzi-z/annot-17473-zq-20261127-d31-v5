"""离线对照器命令行入口（纯标准库）。

子命令：

* ``demo``    双真实进程现场核对最短分歧与最小不可清理环；
* ``exhaust`` 小协议穷举全部可达状态，独立验证最短分歧的最短性；
* ``scale``   同构分支规模夹具：商压缩、搜索边界、预算耗尽未证结论。
"""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

from .engine import cleanup_liveness, shortest_divergence
from .lts import Budget, StepKind, reachable
from .mini import mini_liveness, mini_shortest_divergence
from .protocol import DEFECTS, ProtocolConfig, build_protocol
from .quotient import (
    build_branch_fixture,
    consistency_report,
    observational_quotient,
)
from .simulator import WorkspaceJob, run_job, run_two_process_crosscheck

ARTIFACTS = Path("artifacts")

KIND_NAME = {
    StepKind.MISSING_EDGE: "缺边",
    StepKind.INVALID_TARGET: "无效状态",
    StepKind.REJECTED: "真正拒绝",
    StepKind.MOVED: "正常迁移",
}


def _word(word) -> str:
    return "(" + ",".join(str(int(a)) for a in word) + ")"


def _print(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def cmd_demo(args: argparse.Namespace) -> int:
    cfg = ProtocolConfig(retry_budget=args.retry_budget)
    P = build_protocol("P-正确协议", cfg)
    defect = args.defect
    Q = build_protocol(f"Q-缺陷({defect})", cfg, defect)
    ARTIFACTS.mkdir(exist_ok=True)

    _print("一、离线引擎：首次允许差异（乘积 BFS，保证最短）")
    div = shortest_divergence(P, Q)
    print(f"最短抽象动作序列：{_word(div.action_seq)}")
    print(f"分歧类别：{div.divergence_type}，展开乘积状态 {div.expanded} 个，穷举完整={div.complete}")
    if div.obs_a is not None:
        print(f"可见状态 P={div.obs_a}")
        print(f"可见状态 Q={div.obs_b}（不应开放/不一致的中间可见状态）")
    elif div.kind_a is not None:
        print(f"迁移结果 P={KIND_NAME[div.kind_a]}，Q={KIND_NAME[div.kind_b]}")

    _print("二、小模型独立穷举（词树，独立算法交叉核对）")
    mini_div = mini_shortest_divergence(P, Q, max_depth=args.max_depth)
    same_word = mini_div.word == div.action_seq
    print(f"独立穷举最短词：{_word(mini_div.word)}，检查动作 {mini_div.words_examined} 个")
    print(f"与引擎一致：{same_word}，类别：{mini_div.divergence_type}")

    _print("三、清理活性：EF / AG(EF) / AF 严格区分")
    for tag, lts in (("P", P), ("Q", Q)):
        v = cleanup_liveness(lts)
        mv = mini_liveness(lts, max_depth=args.max_depth)
        print(f"[{tag}] EF={v.ef_verdict}（最短完成词 {_word(v.ef_path) if v.ef_path else '无'}）")
        print(f"      AG(EF)={v.agef_verdict}  AF={v.af_verdict}")
        print(f"      小模型核对：EF={mv.ef_verdict} AG(EF)={mv.agef_verdict} AF={mv.af_verdict}；{mv.note}")
        print(f"      搜索边界：{json.dumps(v.search_boundary, ensure_ascii=False)}")
        if v.witness is not None:
            w = v.witness
            print(f"      不可清理闭环见证：前缀{_word(w.prefix)} + 环{_word(w.cycle)}"
                  f"（{w.kind}，总长 {w.total_length}）")

    _print("四、两个真实本地进程 + 编号化工作区模拟器现场核对")
    print(f"进程启动方式：multiprocessing spawn，平台 {platform.platform()}")
    # 分歧现场：两进程跑同一脚本，日志只含动作号与可见状态
    script = div.action_seq + (int(4), int(9))
    job_p = WorkspaceJob(
        "WS-0001", P, script, str(ARTIFACTS / "ws0001_p.json"),
        expect_accept=True,
    )
    job_q = WorkspaceJob(
        "WS-0002", Q, script, str(ARTIFACTS / "ws0002_q.json"),
        expect_accept=False,
    )
    cross = run_two_process_crosscheck(job_p, job_q)
    print(f"P 进程 PID={cross.p_pid}，Q 进程 PID={cross.q_pid}（两个独立本地进程）")
    print(f"日志首次分歧步：{cross.first_divergence_step}，动作词 {_word(cross.first_divergence_word)}")
    print(f"结果类别编号对 (P,Q)={cross.divergence_result_pair}，"
          f"终态预期校验通过(P,Q)={cross.accept_checks}")
    print(f"现场日志：{cross.log_p} / {cross.log_q}（仅合成动作号 + 可见状态）")

    # 最小不可清理环现场：在 Q 上重复引擎给出的环，独立复核可见状态闭合
    vq = cleanup_liveness(Q)
    if vq.witness is not None and vq.witness.kind == "cycle":
        trap_job_p = WorkspaceJob(
            "WS-0003", P, vq.witness.prefix, str(ARTIFACTS / "ws0003_p_trap.json"),
            repeat_cycle=vq.witness.cycle, cycle_rounds=3,
        )
        trap_job_q = WorkspaceJob(
            "WS-0004", Q, vq.witness.prefix, str(ARTIFACTS / "ws0004_q_trap.json"),
            repeat_cycle=vq.witness.cycle, cycle_rounds=3, expect_accept=False,
        )
        trap = run_two_process_crosscheck(trap_job_p, trap_job_q)
        print(f"环动作 {_word(vq.witness.cycle)} 重复 3 轮，可见状态闭合且从未接受：{trap.trap_confirmed}")

    _print("五、保护语义现场（第三读者引用 + 复用双条件守卫）")
    guard_word = (1, 6, 6, 6, 3)  # 获取后连开三个并行读者，再取消
    guard = run_job(WorkspaceJob(
        "WS-GUARD", P, guard_word, str(ARTIFACTS / "ws_guard.json"),
    ))
    log = json.loads(Path(guard["log_path"]).read_text(encoding="utf-8"))
    rows = log["rows"]
    readers3 = next(r for r in rows
                    if r["action"] == 6 and r["visible"] and r["visible"]["readers"] == 3)
    cancel_idx = next(i for i, r in enumerate(rows) if r["action"] == 3)
    cancel_row = rows[cancel_idx]
    # 拒绝行 visible 为 None：拒绝不改变状态，资源仍持有的事实由拒绝前一行见证
    visible_at_cancel = rows[cancel_idx - 1]["visible"]
    print(f"第三读者持有引用时可见状态：{readers3['visible']}")
    print(f"取消动作结果类别编号={cancel_row['result']}（2=真正拒绝）；"
          f"拒绝不改状态，拒绝前可见状态 acquired={visible_at_cancel['acquired']}，"
          f"资源未仅凭取消动作释放")

    ok = (
        same_word
        and cross.first_divergence_word == div.action_seq
        and cancel_row["result"] == int(StepKind.REJECTED)
        and visible_at_cancel["acquired"] == 1
    )
    print("\n结论：" + ("全部核对一致" if ok else "存在不一致，请检查日志"))
    return 0 if ok else 1


def cmd_exhaust(args: argparse.Namespace) -> int:
    cfg = ProtocolConfig(retry_budget=args.retry_budget)
    P = build_protocol("P", cfg)
    reach = reachable(P)
    _print(f"小协议全状态穷举（声明 {len(P.states)} 个，可达 {len(reach.reached)} 个）")

    # 穷举校验：对每个缺陷，独立词树给出的最短词必须等于引擎乘积 BFS 的最短词
    all_ok = True
    for defect in (d for d in DEFECTS if d is not None):
        Q = build_protocol(f"Q-{defect}", cfg, defect)
        engine_div = shortest_divergence(P, Q)
        mini_div = mini_shortest_divergence(P, Q, max_depth=args.max_depth)
        ok = engine_div.action_seq == mini_div.word and len(engine_div.action_seq) > 0
        all_ok &= ok
        print(f"缺陷 {defect:7s}：引擎最短 {_word(engine_div.action_seq)} "
              f"({engine_div.divergence_type})，穷举最短 {_word(mini_div.word)}，一致={ok}")

    # 最短性的独立见证：比最短词更短的所有词都不产生分歧（由词树 BFS 层序保证）
    print(f"穷举检查动作总数上界深度={args.max_depth}，小模型词树按长度层序展开，"
          "首个分歧词即全局最短词")
    print("结论：" + ("最短性独立验证通过" if all_ok else "验证失败"))
    return 0 if all_ok else 1


def cmd_scale(args: argparse.Namespace) -> int:
    _print("同构分支规模夹具：状态规约前后判定一致性")
    print(f"参数：depth={args.depth} branching={args.branching}，预算={args.budget}")
    good = build_branch_fixture("夹具-正常", args.depth, args.branching)
    bad = build_branch_fixture("夹具-拒绝", args.depth, args.branching, defect="reject")

    report = consistency_report(good, bad, budget_limit=args.budget)
    raw, quo = report["raw"], report["quotient"]
    print(f"原始 LTS：可达状态 {raw['states']}，商化后 {quo['states']}，"
          f"合并等价状态对 {quo['merged_pairs']}")
    print(f"原始搜索边界：{json.dumps(raw['boundary'], ensure_ascii=False)}")
    print(f"商化搜索边界：{json.dumps(quo['boundary'], ensure_ascii=False)}")
    print(f"最短分歧（原始/商化）：{_word(raw['divergence'])} / {_word(quo['divergence'])}")
    print(f"EF：{raw['ef']} / {quo['ef']}；AG(EF)：{raw['agef']} / {quo['agef']}；"
          f"AF：{raw['af']} / {quo['af']}")
    print(f"状态规约前后判定一致：{report['consistent']}")

    _print("预算耗尽场景：结论必须明确标注“仍未证”")
    tight = Budget(limit=max(1, args.depth))
    v = cleanup_liveness(good, tight)
    print(f"预算限额 {tight.limit}，实际展开 {tight.used}，穷举完整={v.reach.complete}")
    print(f"EF={v.ef_verdict}，AG(EF)={v.agef_verdict}，AF={v.af_verdict}")
    if not v.reach.complete:
        print("结论仍未证：预算用完，未达接受态且未排除不可清理环，拒绝给出猜测性判定")
    mini_tight = mini_liveness(good, max_depth=args.depth, budget=Budget(limit=2))
    print(f"小模型同场景：complete={mini_tight.complete}，AG(EF)={mini_tight.agef_verdict}，"
          f"AF={mini_tight.af_verdict}（{mini_tight.note}）")

    # 正常协议自身的商压缩统计
    q = observational_quotient(good)
    print(f"\n等价状态压缩汇总：{q.original_reachable} -> {q.quotient_states} 个等价类")
    return 0 if report["consistent"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m protoctl.cli",
        description="合成沙箱动作协议离线对照器（Python 3.12 标准库）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="双进程现场核对最短分歧与最小不可清理环")
    p_demo.add_argument("--defect", choices=[d for d in DEFECTS if d], default="reject")
    p_demo.add_argument("--retry-budget", type=int, default=2)
    p_demo.add_argument("--max-depth", type=int, default=10)
    p_demo.set_defaults(func=cmd_demo)

    p_ex = sub.add_parser("exhaust", help="小协议穷举验证最短性")
    p_ex.add_argument("--retry-budget", type=int, default=1)
    p_ex.add_argument("--max-depth", type=int, default=10)
    p_ex.set_defaults(func=cmd_exhaust)

    p_sc = sub.add_parser("scale", help="同构分支规模夹具与商压缩")
    p_sc.add_argument("--depth", type=int, default=4)
    p_sc.add_argument("--branching", type=int, default=3)
    p_sc.add_argument("--budget", type=int, default=None)
    p_sc.set_defaults(func=cmd_scale)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

