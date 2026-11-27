"""对照分析引擎。

提供两类核心判定：

1. 两套协议的“首次允许差异”——在乘积 LTS 上 BFS，保证最短；
2. 清理活性——严格区分
   “存在一条可完成路径”（EF 到达接受状态）与
   “所有合法路径最终都会清理”（AG(AF 接受状态)），
   并用前缀 + 环的形式给出最小不可清理闭环见证。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Hashable

from .lts import Budget, LTS, ReachabilityResult, StepKind, reachable, shortest_path

#: 分歧类别（与三类失败一一对应，外加可见状态不一致）
DIV_MISSING = "missing_edge"
DIV_INVALID = "invalid_target"
DIV_REJECT = "true_reject"
DIV_VISIBLE = "visible_mismatch"


@dataclass(frozen=True, slots=True)
class Divergence:
    found: bool
    action_seq: tuple[int, ...]
    divergence_type: str | None
    kind_a: StepKind | None
    kind_b: StepKind | None
    obs_a: Hashable | None
    obs_b: Hashable | None
    expanded: int
    complete: bool  # 预算内是否穷举完整；False 即“结论仍未证”


def _classify(ka: StepKind, kb: StepKind) -> str:
    kinds = {ka, kb}
    if StepKind.REJECTED in kinds:
        return DIV_REJECT
    if StepKind.INVALID_TARGET in kinds:
        return DIV_INVALID
    return DIV_MISSING


def shortest_divergence(
    a: LTS, b: LTS, budget: Budget | None = None
) -> Divergence:
    """返回 A、B 首次出现允许差异的最短抽象动作序列。

    乘积 BFS：同一前缀下两边各自迁移；正常迁移要求到达状态的
    *可见投影*一致。任何一步出现 MOVED/非MOVED 差异、不同失败类别、
    或可见投影不一致，即在该前缀首次分歧。
    """
    budget = budget or Budget()
    if a.alphabet != b.alphabet:
        raise ValueError("两协议动作字母表必须一致（可选动作配置需相同）")

    if a.observe(a.initial) != b.observe(b.initial):
        return Divergence(
            found=True, action_seq=(), divergence_type=DIV_VISIBLE,
            kind_a=None, kind_b=None,
            obs_a=a.observe(a.initial), obs_b=b.observe(b.initial),
            expanded=0, complete=True,
        )

    start = (a.initial, b.initial)
    seen: set[tuple[Hashable, Hashable]] = {start}
    # 队列元素：(a 状态, b 状态, 动作前缀)
    queue: deque[tuple[Hashable, Hashable, tuple[int, ...]]] = deque(
        [(a.initial, b.initial, ())]
    )
    expanded = 0
    while queue:
        if not budget.charge():
            return Divergence(False, (), None, None, None, None, None,
                              expanded, complete=False)
        sa, sb, prefix = queue.popleft()
        expanded += 1
        for action in a.alphabet:
            ra, rb = a.step(sa, action), b.step(sb, action)
            seq = prefix + (action,)
            if ra.kind != rb.kind:
                return Divergence(
                    True, seq, _classify(ra.kind, rb.kind),
                    ra.kind, rb.kind, None, None, expanded, True,
                )
            if ra.kind is not StepKind.MOVED:
                continue  # 两边同类失败：行为一致，不产生新乘积状态
            oa, ob = a.observe(ra.target), b.observe(rb.target)
            if oa != ob:
                return Divergence(
                    True, seq, DIV_VISIBLE,
                    StepKind.MOVED, StepKind.MOVED, oa, ob, expanded, True,
                )
            node = (ra.target, rb.target)
            if node not in seen:
                seen.add(node)
                queue.append((ra.target, rb.target, seq))
    return Divergence(False, (), None, None, None, None, None,
                      expanded, complete=True)


# ---------------------------------------------------------------------------
# 清理活性
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TrapWitness:
    """不可清理闭环见证：prefix 到达坏状态；cycle 为最小环（死端时为空）。"""

    kind: str  # "deadend" | "cycle"
    prefix: tuple[int, ...]
    cycle: tuple[int, ...]
    bad_state: Hashable
    total_length: int


@dataclass(frozen=True, slots=True)
class LivenessVerdict:
    # EF：初态“存在一条可完成路径”
    ef_verdict: str        # "reachable" | "unreachable" | "unknown"
    ef_path: tuple[int, ...] | None
    # AG(EF)：每个可达局面都仍保留完成路径（无不可逆陷阱）
    agef_verdict: str      # "holds" | "violated" | "unknown"
    # AF：字面意义的“所有合法路径最终都会清理”，最小不动点 μZ.(acc ∪ AX Z)
    af_verdict: str        # "holds" | "violated" | "unknown"
    witness: TrapWitness | None        # AG(EF) 违反时的最小闭环/死端见证
    af_witness: TrapWitness | None     # AF 违反时的最小见证
    reach: ReachabilityResult
    good_states: frozenset[Hashable]   # 能到达接受态的状态（AG(EF) 好状态）
    bad_states: frozenset[Hashable]    # 无法完成必要清理的状态
    af_states: frozenset[Hashable]     # 所有合法路径必达接受态的状态
    deadends: frozenset[Hashable]
    cyclic_bad: frozenset[Hashable]
    search_boundary: dict[str, int]


def _moved_successors(lts: LTS, state: Hashable) -> list[tuple[int, Hashable]]:
    table = lts.transitions.get(state) or {}
    out: list[tuple[int, Hashable]] = []
    for action in lts.alphabet:
        if action not in table:
            continue
        result = lts.step(state, action)
        if result.kind is StepKind.MOVED:
            out.append((action, result.target))
    return out


def _bfs_shortest(
    lts: LTS,
    sources: set[Hashable],
    allowed: frozenset[Hashable],
    stop_predicate,
) -> tuple[Hashable | None, tuple[int, ...], dict[Hashable, tuple[Hashable, int] | None]]:
    """allowed 子图上的多源 BFS，返回首个满足 stop_predicate 的状态与最短序列。"""
    parents: dict[Hashable, tuple[Hashable, int] | None] = {
        s: None for s in sources
    }
    queue: deque[Hashable] = deque(sources)
    while queue:
        state = queue.popleft()
        if stop_predicate(state):
            path: list[int] = []
            cur = state
            while parents[cur] is not None:
                parent, action = parents[cur]  # type: ignore[misc]
                path.append(action)
                cur = parent
            path.reverse()
            return state, tuple(path), parents
        for action, nxt in _moved_successors(lts, state):
            if nxt in allowed and nxt not in parents:
                parents[nxt] = (state, action)
                queue.append(nxt)
    return None, (), parents


def _tarjan_scc(
    nodes: frozenset[Hashable], successors: dict[Hashable, list[Hashable]]
) -> list[list[Hashable]]:
    """迭代版 Tarjan 强连通分量，避免大规模夹具触发递归深度问题。"""
    index_counter = 0
    index: dict[Hashable, int] = {}
    lowlink: dict[Hashable, int] = {}
    on_stack: set[Hashable] = set()
    stack: list[Hashable] = []
    components: list[list[Hashable]] = []

    for root in nodes:
        if root in index:
            continue
        work: list[tuple[Hashable, int]] = [(root, 0)]
        index[root] = lowlink[root] = index_counter
        index_counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, pi = work[-1]
            succ = successors.get(v, [])
            if pi < len(succ):
                work[-1] = (v, pi + 1)
                w = succ[pi]
                if w not in index:
                    index[w] = lowlink[w] = index_counter
                    index_counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, 0))
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], index[w])
            else:
                if lowlink[v] == index[v]:
                    comp: list[Hashable] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    components.append(comp)
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
    return components


def cleanup_liveness(
    lts: LTS, budget: Budget | None = None
) -> LivenessVerdict:
    """三个严格区分的活性判定。

    * EF（存在可完成路径）：接受态是否从初态可达；
    * AG(EF)（每个局面都仍可完成清理）：不存在不可逆陷阱，
      违反时给出坏子图中的最小死端/闭环见证；
    * AF（所有合法路径最终都会清理）：最小不动点
      μZ.(接受态 ∪ AX Z)，自愿打转的非接受环同样违反。
    """
    budget = budget or Budget()
    reach = reachable(lts, budget)
    rset = reach.reached
    complete = reach.complete

    # --- EF：可达集中是否含接受状态（BFS 父指针给出最短完成路径）---
    accept_reached = rset & lts.accept
    ef_path: tuple[int, ...] | None = None
    if accept_reached:
        target = min(accept_reached, key=lambda s: len(shortest_path(reach.parents, s)))
        ef_path = shortest_path(reach.parents, target)
        ef_verdict = "reachable"
    elif complete:
        ef_verdict = "unreachable"
    else:
        ef_verdict = "unknown"

    # --- AG(EF)：在迁移图上反向求“能到达接受态”的状态（好状态）---
    reverse: dict[Hashable, list[Hashable]] = {s: [] for s in rset}
    moved_edges = 0
    for s in rset:
        for _, t in _moved_successors(lts, s):
            if t in rset:
                reverse[t].append(s)
                moved_edges += 1
    good: set[Hashable] = set(accept_reached)
    queue: deque[Hashable] = deque(accept_reached)
    while queue:
        t = queue.popleft()
        for s in reverse[t]:
            if s not in good:
                good.add(s)
                queue.append(s)
    bad = frozenset(rset - good)

    # --- 坏状态中的死端与环 ---
    deadends = frozenset(
        s for s in bad if not _moved_successors(lts, s)
    )
    succ_map: dict[Hashable, list[Hashable]] = {
        s: [t for _, t in _moved_successors(lts, s) if t in bad] for s in bad
    }
    cyclic: set[Hashable] = set()
    for comp in _tarjan_scc(bad, succ_map):
        if len(comp) > 1:
            cyclic.update(comp)
        elif comp[0] in succ_map.get(comp[0], []):
            cyclic.add(comp[0])  # 自环
    cyclic_bad = frozenset(cyclic)

    if not complete:
        agef_verdict = "unknown"
        witness: TrapWitness | None = None
    elif lts.initial in bad:
        agef_verdict = "violated"
        witness = _minimal_witness(lts, bad, deadends, cyclic_bad)
    else:
        agef_verdict = "holds"
        witness = None

    # --- AF：最小不动点 μZ.(接受态 ∪ AX Z) ---
    af_good, af_bad = _least_fixpoint_af(lts, rset)
    af_states = frozenset(af_good)
    if not complete:
        af_verdict = "unknown"
        af_witness: TrapWitness | None = None
    elif lts.initial in af_bad:
        af_verdict = "violated"
        af_deadends = frozenset(
            s for s in af_bad if not _moved_successors(lts, s)
        )
        af_succ: dict[Hashable, list[Hashable]] = {
            s: [t for _, t in _moved_successors(lts, s) if t in af_bad]
            for s in af_bad
        }
        af_cyclic: set[Hashable] = set()
        for comp in _tarjan_scc(af_bad, af_succ):
            if len(comp) > 1:
                af_cyclic.update(comp)
            elif comp[0] in af_succ.get(comp[0], []):
                af_cyclic.add(comp[0])
        af_witness = _minimal_witness(
            lts, af_bad, af_deadends, frozenset(af_cyclic),
        )
    else:
        af_verdict = "holds"
        af_witness = None

    boundary = {
        "reachable_states": len(rset),
        "moved_edges": moved_edges,
        "agef_good_states": len(good),
        "trap_bad_states": len(bad),
        "deadend_states": len(deadends),
        "cyclic_bad_states": len(cyclic_bad),
        "af_good_states": len(af_good),
        "af_bad_states": len(af_bad),
        "budget_used": reach.budget.used,
        "budget_limit": reach.budget.limit if reach.budget.limit is not None else -1,
    }
    return LivenessVerdict(
        ef_verdict=ef_verdict, ef_path=ef_path,
        agef_verdict=agef_verdict, af_verdict=af_verdict,
        witness=witness, af_witness=af_witness,
        reach=reach, good_states=frozenset(good), bad_states=bad,
        af_states=af_states,
        deadends=deadends, cyclic_bad=cyclic_bad,
        search_boundary=boundary,
    )


def _least_fixpoint_af(
    lts: LTS, rset: frozenset[Hashable]
) -> tuple[set[Hashable], frozenset[Hashable]]:
    """AF 接受态的最小不动点：Z = 接受态 ∪ {s | 全部后继 ∈ Z}。

    与“反向可达好状态”（AG(EF)）不同：这里要求**全部**后继已在 Z 中，
    因此非接受态的自愿打转环永远进不了 Z。
    """
    good: set[Hashable] = set(lts.accept & rset)
    succ_cache: dict[Hashable, list[Hashable]] = {
        s: [t for _, t in _moved_successors(lts, s) if t in rset]
        for s in rset
    }
    changed = True
    while changed:
        changed = False
        for s in rset:
            if s in good:
                continue
            outs = succ_cache[s]
            if outs and all(t in good for t in outs):
                good.add(s)
                changed = True
    return good, frozenset(rset - good)


def _minimal_witness(
    lts: LTS,
    bad: frozenset[Hashable],
    deadends: frozenset[Hashable],
    cyclic_bad: frozenset[Hashable],
) -> TrapWitness:
    """在坏状态子图中找总长最小的不可清理见证（死端或套索环）。"""
    candidates: list[TrapWitness] = []

    # 完整 BFS（停止谓词恒假）拿到初态到各坏状态的最短前缀父指针
    _, _, prefix_parents = _bfs_shortest(
        lts, {lts.initial}, bad, lambda _s: False
    )

    # 死端见证：初态到任意坏状态死端的最短前缀
    for target in deadends:
        if target in prefix_parents:
            candidates.append(TrapWitness(
                "deadend", _rebuild(prefix_parents, target), (), target,
                len(_rebuild(prefix_parents, target)),
            ))

    # 环见证：对每个位于环上的坏状态求最短前缀 + 回到自身的最短环
    for t in cyclic_bad:
        if t not in prefix_parents:
            continue
        prefix = _rebuild(prefix_parents, t)
        cycle = _shortest_cycle(lts, bad, t)
        if cycle is None:
            continue
        candidates.append(TrapWitness(
            "cycle", prefix, cycle, t, len(prefix) + len(cycle),
        ))

    if not candidates:
        # 理论上不会发生：初态在坏集合中必有死端或环
        raise RuntimeError("坏状态子图既无死端也无环，判定逻辑有误")
    return min(candidates, key=lambda w: (w.total_length, w.prefix, w.cycle))


def _rebuild(
    parents: dict[Hashable, tuple[Hashable, int] | None], target: Hashable
) -> tuple[int, ...]:
    path: list[int] = []
    cur = target
    while parents[cur] is not None:
        parent, action = parents[cur]  # type: ignore[misc]
        path.append(action)
        cur = parent
    path.reverse()
    return tuple(path)


def _shortest_cycle(
    lts: LTS, allowed: frozenset[Hashable], start: Hashable
) -> tuple[int, ...] | None:
    """start 在 allowed 子图上回到自身的最短动作环。"""
    parents: dict[Hashable, tuple[Hashable, int]] = {}
    seen: set[Hashable] = {start}
    queue: deque[Hashable] = deque()
    for action, nxt in _moved_successors(lts, start):
        if nxt not in allowed:
            continue
        if nxt == start:
            return (action,)  # 自环
        if nxt not in seen:
            seen.add(nxt)
            parents[nxt] = (start, action)
            queue.append(nxt)
    while queue:
        state = queue.popleft()
        for action, nxt in _moved_successors(lts, state):
            if nxt not in allowed:
                continue
            if nxt == start:
                # 由 state 沿父指针回到 start，再补上入边
                path: list[int] = []
                cur = state
                while cur != start:
                    parent, edge = parents[cur]
                    path.append(edge)
                    cur = parent
                path.reverse()
                path.append(action)
                return tuple(path)
            if nxt not in seen:
                seen.add(nxt)
                parents[nxt] = (state, action)
                queue.append(nxt)
    return None
