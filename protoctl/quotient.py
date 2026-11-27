"""观察等价商压缩与同构分支规模夹具。

商压缩：按“观察值 + 各动作后继所在等价类”做分区细化（
partition refinement），得到最粗的观察等价划分。隐藏的重试预算
只影响内部状态、不影响可见投影与后续可观察行为时，对应状态会被合并。

规模夹具：生成参数化的同构分支树（每个内点有 branching 个同构分支），
用于在状态规约（原始 LTS 与商化 LTS）前后核对判定一致，并分别报告
等价状态压缩与搜索边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable

from .lts import LTS, REJECT, StepKind, reachable
from .engine import cleanup_liveness, shortest_divergence


@dataclass(frozen=True, slots=True)
class Quotient:
    lts: LTS
    partition: tuple[frozenset[Hashable], ...]
    class_of: dict[Hashable, int]
    original_reachable: int
    quotient_states: int
    merged_pairs: int


def _moved_or_special(lts: LTS, state: Hashable, action: int):
    """返回后继签名：(kind 编号, 等价类或哨兵)。"""
    result = lts.step(state, action)
    if result.kind is StepKind.MOVED:
        return (int(StepKind.MOVED), result.target)
    return (int(result.kind), None)


def observational_quotient(
    lts: LTS, restrict_reachable: bool = True
) -> Quotient:
    """观察等价商（分区细化到不动点）。"""
    if restrict_reachable:
        universe = reachable(lts).reached
    else:
        universe = lts.states

    # 初始划分：按观察值分组
    blocks: dict[Hashable, list[Hashable]] = {}
    for state in universe:
        blocks.setdefault(lts.observe(state), []).append(state)
    class_of: dict[Hashable, int] = {}
    signatures_old: dict[Hashable, tuple] = {}
    groups = list(blocks.values())
    for idx, members in enumerate(groups):
        for state in members:
            class_of[state] = idx

    # 迭代细化：签名 = (观察值, 每个动作的 (kind, 后继等价类))
    while True:
        new_signature: dict[Hashable, tuple] = {}
        for state in universe:
            parts: list = [lts.observe(state)]
            for action in lts.alphabet:
                kind_code, target = _moved_or_special(lts, state, action)
                succ_class = class_of[target] if target is not None else -1
                parts.append((action, kind_code, succ_class))
            new_signature[state] = tuple(parts)

        # 按新签名重新分块
        next_blocks: dict[tuple, list[Hashable]] = {}
        for state in universe:
            next_blocks.setdefault(new_signature[state], []).append(state)
        next_groups = list(next_blocks.values())
        next_class: dict[Hashable, int] = {}
        for idx, members in enumerate(next_groups):
            for state in members:
                next_class[state] = idx

        if next_class == class_of:
            partition = tuple(frozenset(m) for m in next_groups)
            break
        class_of = next_class
        signatures_old = new_signature

    # 用代表元构造商 LTS
    rep_of_class: dict[int, Hashable] = {}
    for state, cid in class_of.items():
        rep_of_class.setdefault(cid, state)

    q_transitions: dict[Hashable, dict[int, object]] = {}
    q_observations: dict[Hashable, Hashable] = {}
    q_states = frozenset(rep_of_class.values())
    for cid, rep in rep_of_class.items():
        table: dict[int, object] = {}
        for action in lts.alphabet:
            result = lts.step(rep, action)
            if result.kind is StepKind.MOVED:
                table[action] = rep_of_class[class_of[result.target]]
            elif result.kind is StepKind.REJECTED:
                table[action] = REJECT
            # MISSING_EDGE / INVALID_TARGET：不放入边表，保持语义
        q_transitions[rep] = table
        q_observations[rep] = lts.observe(rep)

    q_accept = frozenset(
        rep_of_class[class_of[s]] for s in lts.accept if s in class_of
    )
    quotient = LTS(
        name=f"{lts.name}//obs",
        states=q_states,
        initial=rep_of_class[class_of[lts.initial]],
        alphabet=lts.alphabet,
        transitions=q_transitions,
        valid_states=q_states,
        observations=q_observations,
        accept=q_accept,
    )
    merged = sum(len(b) * (len(b) - 1) // 2 for b in partition)
    return Quotient(
        lts=quotient,
        partition=partition,
        class_of=class_of,
        original_reachable=len(universe),
        quotient_states=len(partition),
        merged_pairs=merged,
    )


# ---------------------------------------------------------------------------
# 同构分支规模夹具
# ---------------------------------------------------------------------------

def build_branch_fixture(
    name: str, depth: int, branching: int, *, defect: str | None = None
) -> LTS:
    """确定性同构分支夹具。

    节点编号为路径元组；每个内点有 branching 个同构孩子，
    孩子选择由动作 20+k（k=0..branching-1）编码，完成动作 30 仅叶节点可用。
    深度 depth、分支 branching 共同控制规模；兄弟子树彼此同构，
    因此商压缩会把它们合并。defect 可选 reject / missing / shadow。
    """
    SELECT_ACTIONS = tuple(20 + k for k in range(branching))
    DONE = 30
    alphabet = SELECT_ACTIONS + (DONE,)

    states: set[tuple] = set()
    transitions: dict[tuple, dict[int, object]] = {}
    observations: dict[tuple, tuple] = {}
    accept: set[tuple] = set()
    shadow_mid = ("shadow-mid",)

    def build(node: tuple, level: int) -> None:
        states.add(node)
        table: dict[int, object] = {}
        observations[node] = (level,)
        if level < depth:
            for k, action in enumerate(SELECT_ACTIONS):
                child = node + (k,)
                table[action] = child
                build(child, level + 1)
        else:
            accept.add(node)
            if defect == "reject":
                table[DONE] = REJECT
            elif defect == "missing":
                pass  # 缺边
            elif defect == "shadow":
                table[DONE] = shadow_mid
            else:
                table[DONE] = ("done",) + node
                done_state = ("done",) + node
                states.add(done_state)
                observations[done_state] = (depth + 1,)
                transitions[done_state] = {}
                accept.add(done_state)
                accept.discard(node)
        transitions[node] = table

    root: tuple = ()
    build(root, 0)

    if defect == "shadow":
        states.add(shadow_mid)
        observations[shadow_mid] = (-1,)  # 不应开放的可见投影
        transitions[shadow_mid] = {
            DONE: ("done-shadow",),
        }
        done_state = ("done-shadow",)
        states.add(done_state)
        observations[done_state] = (depth + 1,)
        transitions[done_state] = {}
        accept.add(done_state)
        valid = frozenset(states)
    else:
        valid = frozenset(states)

    return LTS(
        name=name,
        states=frozenset(states),
        initial=root,
        alphabet=alphabet,
        transitions=transitions,
        valid_states=valid,
        observations=observations,
        accept=frozenset(accept),
    )


def consistency_report(
    left: LTS, right: LTS, budget_limit: int | None = None
) -> dict:
    """状态规约前后（或两协议之间）判定一致性报告。"""
    from .lts import Budget

    q_left = observational_quotient(left)
    q_right = observational_quotient(right)

    b1 = Budget(budget_limit)
    b2 = Budget(budget_limit)
    div_raw = shortest_divergence(left, right, b1)
    div_quo = shortest_divergence(q_left.lts, q_right.lts, b2)

    l1 = Budget(budget_limit)
    l2 = Budget(budget_limit)
    live_raw = cleanup_liveness(left, l1)
    live_quo = cleanup_liveness(q_left.lts, l2)

    consistent = (
        div_raw.found == div_quo.found
        and div_raw.action_seq == div_quo.action_seq
        and live_raw.ef_verdict == live_quo.ef_verdict
        and live_raw.agef_verdict == live_quo.agef_verdict
        and live_raw.af_verdict == live_quo.af_verdict
    )
    return {
        "consistent": consistent,
        "raw": {
            "states": q_left.original_reachable,
            "divergence": div_raw.action_seq,
            "divergence_found": div_raw.found,
            "divergence_complete": div_raw.complete,
            "ef": live_raw.ef_verdict,
            "agef": live_raw.agef_verdict,
            "af": live_raw.af_verdict,
            "boundary": live_raw.search_boundary,
        },
        "quotient": {
            "states": q_left.quotient_states,
            "merged_pairs": q_left.merged_pairs,
            "divergence": div_quo.action_seq,
            "divergence_found": div_quo.found,
            "divergence_complete": div_quo.complete,
            "ef": live_quo.ef_verdict,
            "agef": live_quo.agef_verdict,
            "af": live_quo.af_verdict,
            "boundary": live_quo.search_boundary,
        },
        "raw_budget_used": b1.used + l1.used,
        "quotient_budget_used": b2.used + l2.used,
        "raw_complete": div_raw.complete and live_raw.reach.complete,
        "quotient_complete": div_quo.complete and live_quo.reach.complete,
    }
