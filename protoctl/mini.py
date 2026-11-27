"""小模型独立穷举器。

本模块刻意不调用 ``engine`` 的乘积 BFS / 反向可达算法，而是用另一套
实现交叉核对：

* 最短分歧：按长度逐层穷举动作词（词树 BFS），
  第一个让两协议行为不同的词即最短分歧；
* 活性：EF 用深度受限 DFS 独立寻找接受状态；AG(AF) 用“好状态”
  最小不动点生长法（与 engine 的接受态反向 BFS 是不同实现）；
* 穷举全部短交错并统计：可完成词数、落入陷阱的词数等，
  从而严格区分“存在可完成路径”与“所有合法路径最终都会清理”。

词树统计与图判定刻意分开：深度边界上的节点不参与图判定，
避免把“尚未展开”误当成死端。预算耗尽时所有结论降级为
``unknown``（结论仍未证），绝不猜测。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Hashable

from .lts import Budget, LTS, StepKind, reachable


@dataclass(frozen=True, slots=True)
class MiniDivergence:
    found: bool
    word: tuple[int, ...]
    divergence_type: str | None
    depth_searched: int
    words_examined: int
    complete: bool


def _behavior(lts: LTS, state: Hashable, action: int):
    """词树中的一步行为：失败类别，或 (MOVED, 观察值, 目标状态)。"""
    result = lts.step(state, action)
    if result.kind is StepKind.MOVED:
        return (StepKind.MOVED, lts.observe(result.target), result.target)
    return (result.kind, None, None)


def mini_shortest_divergence(
    a: LTS, b: LTS, max_depth: int, budget: Budget | None = None
) -> MiniDivergence:
    """词树逐层穷举求最短分歧（与 engine.shortest_divergence 独立实现）。"""
    budget = budget or Budget()
    if a.alphabet != b.alphabet:
        raise ValueError("两协议动作字母表必须一致")
    words = 0
    # 队列元素：(a 状态, b 状态, 当前词)；按入队顺序即按长度非递减
    queue: deque[tuple[Hashable, Hashable, tuple[int, ...]]] = deque(
        [(a.initial, b.initial, ())]
    )
    seen_pairs: set[tuple[Hashable, Hashable]] = {(a.initial, b.initial)}
    while queue:
        sa, sb, word = queue.popleft()
        if len(word) >= max_depth:
            continue
        for action in a.alphabet:
            words += 1
            if not budget.charge():
                return MiniDivergence(False, word, None, len(word),
                                      words, complete=False)
            ba, bb = _behavior(a, sa, action), _behavior(b, sb, action)
            next_word = word + (action,)
            if ba[0] != bb[0]:
                return MiniDivergence(
                    True, next_word, ba[0].name.lower(),
                    len(next_word), words, True,
                )
            if ba[0] is StepKind.MOVED and ba[1] != bb[1]:
                return MiniDivergence(
                    True, next_word, "visible_mismatch",
                    len(next_word), words, True,
                )
            if ba[0] is StepKind.MOVED:
                pair = (ba[2], bb[2])
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    queue.append((ba[2], bb[2], next_word))
    return MiniDivergence(False, (), None, max_depth, words, True)


@dataclass(frozen=True, slots=True)
class MiniLiveness:
    ef_verdict: str        # reachable / unreachable / unknown
    ef_word: tuple[int, ...] | None
    agef_verdict: str      # holds / violated / unknown：每个局面都仍可完成
    af_verdict: str        # holds / violated / unknown：所有合法路径必达接受态
    depth: int
    words_total: int       # 穷举的短交错总数
    words_to_accept: int   # 以接受状态结束的词数
    trapped_words: int     # 进入坏状态且未经过接受态的短交错数
    complete: bool
    note: str


def _successor_map(lts: LTS, state: Hashable) -> list[tuple[int, Hashable]]:
    out: list[tuple[int, Hashable]] = []
    for action in lts.alphabet:
        result = lts.step(state, action)
        if result.kind is StepKind.MOVED:
            out.append((action, result.target))
    return out


def _enumerate_words(
    lts: LTS, max_depth: int, budget: Budget
) -> tuple[int, int, bool]:
    """词树穷举长度不超过 max_depth 的短交错，返回 (总词数, 接受词数, 是否截断)。"""
    words_total = 0
    words_to_accept = 0
    queue: deque[tuple[Hashable, int]] = deque([(lts.initial, 0)])
    truncated = False
    while queue:
        state, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for _, nxt in _successor_map(lts, state):
            words_total += 1
            if not budget.charge():
                truncated = True
                break
            if nxt in lts.accept:
                words_to_accept += 1
            queue.append((nxt, depth + 1))
        if truncated:
            break
    return words_total, words_to_accept, truncated


def _ef_dfs(
    lts: LTS, reached: set[Hashable], max_depth: int
) -> tuple[str, tuple[int, ...] | None]:
    """深度受限 DFS 独立寻找接受态；并以完整可达图兜底确认可达性。"""
    accept_in_full = bool(reached & lts.accept)
    stack: list[tuple[Hashable, int, tuple[int, ...]]] = [
        (lts.initial, 0, ())
    ]
    visited: set[Hashable] = set()
    found_word: tuple[int, ...] | None = None
    while stack:
        state, depth, word = stack.pop()
        if state in lts.accept:
            found_word = word
            break
        if depth >= max_depth or state in visited:
            continue
        visited.add(state)
        for action, nxt in _successor_map(lts, state):
            stack.append((nxt, depth + 1, word + (action,)))
    if accept_in_full:
        return "reachable", found_word
    return "unreachable", None


def _agef_good_states(
    lts: LTS, reached: set[Hashable]
) -> set[Hashable]:
    """AG(EF) 好状态：能到达接受态的状态（本模块独立的反向生长实现）。

    从接受态出发，沿反向边生长；非接受态死端永远进不来。
    """
    good: set[Hashable] = set(lts.accept & reached)
    reverse: dict[Hashable, list[Hashable]] = {s: [] for s in reached}
    for s in reached:
        for _, t in _successor_map(lts, s):
            if t in reached:
                reverse[t].append(s)
    queue: deque[Hashable] = deque(good)
    while queue:
        t = queue.popleft()
        for s in reverse[t]:
            if s not in good:
                good.add(s)
                queue.append(s)
    return good


def _af_fixpoint(
    lts: LTS, reached: set[Hashable]
) -> set[Hashable]:
    """AF 好状态最小不动点：接受态，或所有合法后继都是好状态。

    从接受态向外生长，而非从全集消去——后者是最大不动点，
    会在“无接受态的纯环”上错误地把整环保留。
    非接受态死端后继为空，永远不会被加入。
    """
    good: set[Hashable] = set(lts.accept & reached)
    succ_cache: dict[Hashable, list[Hashable]] = {
        s: [t for _, t in _successor_map(lts, s) if t in reached]
        for s in reached
    }
    changed = True
    while changed:
        changed = False
        for s in reached:
            if s in good:
                continue
            outs = succ_cache[s]
            if outs and all(t in good for t in outs):
                good.add(s)
                changed = True
    return good


def _count_trapped_words(
    lts: LTS,
    reached: set[Hashable],
    good: set[Hashable],
    max_depth: int,
) -> int:
    """统计进入坏状态（不在好集合）且全程未经过接受态的短交错数。"""
    bad = reached - good
    count = 0
    queue: deque[tuple[Hashable, int]] = deque([(lts.initial, 0)])
    while queue:
        state, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for _, nxt in _successor_map(lts, state):
            if nxt in lts.accept:
                continue
            if nxt in bad:
                count += 1
            queue.append((nxt, depth + 1))
    return count


def mini_liveness(
    lts: LTS, max_depth: int, budget: Budget | None = None
) -> MiniLiveness:
    """独立词树穷举 + 最小不动点生长法判定 EF / AG(AF)。

    词树只负责短交错统计；EF / AG(AF) 在完整可达图上判定，
    状态收集借用通用 ``reachable``，但两个判定算法都在本模块独立实现。
    """
    budget = budget or Budget()

    # 1. 词树统计短交错（仅统计，不作为图判定依据）
    words_total, words_to_accept, truncated = _enumerate_words(
        lts, max_depth, budget,
    )

    # 2. 完整可达图：状态收集用通用 BFS，判定算法仍独立
    reached = set(reachable(lts).reached)

    # 3. EF：深度受限 DFS（完整图兜底）
    ef_verdict, ef_word = _ef_dfs(lts, reached, max_depth)

    # 4. AG(EF)：每个局面是否都仍保留完成路径（反向生长，独立实现）
    agef_good = _agef_good_states(lts, reached)
    # 5. AF：所有合法路径必达接受态（最小不动点，独立实现）
    af_good = _af_fixpoint(lts, reached)
    trapped_words = _count_trapped_words(lts, reached, agef_good, max_depth)

    if truncated:
        return MiniLiveness(
            ef_verdict=ef_verdict,
            ef_word=ef_word,
            agef_verdict="unknown",
            af_verdict="unknown",
            depth=max_depth, words_total=words_total,
            words_to_accept=words_to_accept,
            trapped_words=trapped_words,
            complete=False,
            note="预算或深度受限，穷举不完整：活性结论仍未证",
        )

    agef_holds = lts.initial in agef_good
    af_holds = lts.initial in af_good
    if ef_verdict == "reachable" and not agef_holds:
        note = "存在可完成路径，但存在无法完成必要清理的局面（AG(EF) 违反）"
    elif ef_verdict == "reachable" and not af_holds:
        note = "存在可完成路径且无死局，但并非所有合法路径都最终清理（AF 违反）"
    elif af_holds:
        note = "全部合法路径均可最终到达接受态"
    else:
        note = "不存在任何可完成路径"
    return MiniLiveness(
        ef_verdict=ef_verdict,
        ef_word=ef_word,
        agef_verdict=("holds" if agef_holds else "violated"),
        af_verdict=("holds" if af_holds else "violated"),
        depth=max_depth, words_total=words_total,
        words_to_accept=words_to_accept,
        trapped_words=trapped_words,
        complete=True,
        note=note,
    )
