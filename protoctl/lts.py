"""通用有穷标号迁移系统（LTS）与三类失败的严格区分。

三类失败（题面要求分别处理）：

* ``MISSING_EDGE``  缺边：该状态下根本没有这条动作的迁移；
* ``INVALID_TARGET`` 无效状态：迁移目标落在声明的合法状态集之外；
* ``REJECTED``       真正拒绝：存在显式拒绝边，协议明确表示“不允许”。

正常迁移为 ``MOVED``。LTS 全部由可 pickle 的普通数据构成，
以便 spawn 出来的真实子进程直接接收协议对象。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Hashable


class StepKind(IntEnum):
    MISSING_EDGE = 0
    INVALID_TARGET = 1
    REJECTED = 2
    MOVED = 3


class _RejectSentinel:
    """显式拒绝边的目标哨兵（单例，可 pickle）。"""

    _instance: "_RejectSentinel | None" = None

    def __new__(cls) -> "_RejectSentinel":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "REJECT"

    def __reduce__(self):
        # 跨 spawn 进程可靠反序列化：按完全限定类重建，__new__ 保证仍是单例
        return (_RejectSentinel, ())


#: 显式拒绝哨兵
REJECT = _RejectSentinel()


@dataclass(frozen=True, slots=True)
class StepResult:
    kind: StepKind
    target: Any  # MOVED 时为目标状态；其余为 None 或哨兵


@dataclass
class Budget:
    """搜索预算（按展开状态数计费）。limit=None 表示不限。"""

    limit: int | None = None
    used: int = 0
    exhausted: bool = False

    def charge(self) -> bool:
        """消费一次展开额度；返回是否仍可继续。"""
        self.used += 1
        if self.limit is not None and self.used >= self.limit:
            self.exhausted = True
            return False
        return True

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)


@dataclass
class LTS:
    """通用 LTS；状态可为任意可哈希对象。"""

    name: str
    states: frozenset[Hashable]
    initial: Hashable
    alphabet: tuple[int, ...]
    transitions: dict[Hashable, dict[int, Any]]
    valid_states: frozenset[Hashable]
    observations: dict[Hashable, Hashable]   # 状态 -> 可观察投影
    accept: frozenset[Hashable]              # 完成必要清理的接受状态

    def step(self, state: Hashable, action: int) -> StepResult:
        table = self.transitions.get(state)
        if table is None or action not in table:
            return StepResult(StepKind.MISSING_EDGE, None)
        target = table[action]
        if target is REJECT:
            return StepResult(StepKind.REJECTED, REJECT)
        if target not in self.valid_states:
            return StepResult(StepKind.INVALID_TARGET, target)
        return StepResult(StepKind.MOVED, target)

    def observe(self, state: Hashable) -> Hashable:
        return self.observations[state]


@dataclass(frozen=True, slots=True)
class ReachabilityResult:
    reached: frozenset[Hashable]
    parents: dict[Hashable, tuple[Hashable, int] | None]
    edges_traversed: int
    budget: Budget
    complete: bool  # 预算内是否穷举完整


def reachable(lts: LTS, budget: Budget | None = None) -> ReachabilityResult:
    """从初态出发的可达状态 BFS（同时记录父指针用于还原最短路径）。"""
    budget = budget or Budget()
    parents: dict[Hashable, tuple[Hashable, int] | None] = {lts.initial: None}
    queue: deque[Hashable] = deque([lts.initial])
    edges = 0
    complete = True
    while queue:
        if not budget.charge():
            complete = False
            break
        state = queue.popleft()
        table = lts.transitions.get(state) or {}
        for action in lts.alphabet:
            if action not in table:
                continue
            edges += 1
            result = lts.step(state, action)
            if result.kind is not StepKind.MOVED:
                continue
            nxt = result.target
            if nxt not in parents:
                parents[nxt] = (state, action)
                queue.append(nxt)
    return ReachabilityResult(frozenset(parents), parents, edges, budget, complete)


def shortest_path(
    parents: dict[Hashable, tuple[Hashable, int] | None],
    target: Hashable,
) -> tuple[int, ...]:
    """由 reachable 的父指针还原动作编号序列。"""
    path: list[int] = []
    current = target
    while parents[current] is not None:
        parent, action = parents[current]  # type: ignore[misc]
        path.append(action)
        current = parent
    path.reverse()
    return tuple(path)
