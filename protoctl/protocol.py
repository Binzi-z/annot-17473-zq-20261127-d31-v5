"""编号化动作协议的构建器。

正确协议 ``P`` 实现题面的全部保护语义：

* 可重试虚拟资源数受 ``retry_budget`` 限制（预算为隐藏内部状态）；
* 取消动作在仍有并行读者引用时被真正拒绝，不能仅凭取消释放资源；
* 复用动作只有在“资源已按规定清理”且“归还原所有者”完成、
  且无残留读者引用时才放行（``states.reuse_allowed``）；
* 第三读者（readers == 3）与其他读者一样持有抽象引用。

缺陷变体 ``Q`` 用于对照，四类注入分别对应三种失败类别与一种
“同终态、禁开中间态”的可见性偏差：

``missing``  缺边、``invalid`` 无效状态、``reject`` 真正拒绝、
``shadow``   影子中间态（最终可见状态可达，但经过不应开放的中间状态）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import Act, OPTIONAL
from .lts import LTS, REJECT
from .states import (
    MAX_READERS,
    ROLE_BORROWER,
    ROLE_OWNER,
    LTSState,
    VisibleState,
    reuse_allowed,
)

DEFECTS = (None, "missing", "invalid", "reject", "shadow")


@dataclass(frozen=True, slots=True)
class ProtocolConfig:
    """协议配置：三个可选动作开关 + 可重试虚拟资源数。"""

    retry_budget: int = 2
    allow_retry: bool = True
    allow_cancel: bool = True
    allow_reclaim: bool = True

    def alphabet(self) -> tuple[int, ...]:
        enabled = [
            Act.ACQUIRE, Act.HANDOFF, Act.ADD_READER, Act.DROP_READER,
            Act.CLEAN, Act.RETURN_OWNER, Act.REUSE,
        ]
        if self.allow_retry:
            enabled.append(Act.RETRY)
        if self.allow_cancel:
            enabled.append(Act.CANCEL)
        if self.allow_reclaim:
            enabled.append(Act.RECLAIM)
        return tuple(int(a) for a in sorted(enabled, key=lambda x: int(x)))


def _initial(cfg: ProtocolConfig) -> LTSState:
    return LTSState(0, ROLE_OWNER, 0, 0, 0, cfg.retry_budget)


def _all_states(cfg: ProtocolConfig) -> frozenset[LTSState]:
    out: set[LTSState] = set()
    for acquired in (0, 1):
        for role in (ROLE_OWNER, ROLE_BORROWER):
            for readers in range(MAX_READERS + 1):
                for cleaned in (0, 1):
                    for returned in (0, 1):
                        for budget in range(cfg.retry_budget + 1):
                            out.add(LTSState(
                                acquired, role, readers, cleaned, returned, budget,
                            ))
    return frozenset(out)


# 无效状态缺陷使用的越界目标（角色编号越界）
_INVALID_TARGET = ("invalid", "role", 9)


def _shadow_key(role: int, budget: int) -> tuple:
    """影子中间态：保留角色与隐藏预算，确保第二次清理后与 P 同终态。"""
    return ("shadow", "intermediate", role, budget)


def _successor(state: LTSState, action: Act, cfg: ProtocolConfig):
    """正确协议 P 的迁移函数；返回 LTSState / REJECT。"""
    a, r, n, c, g, b = state.as_tuple()

    if action is Act.ACQUIRE:
        if a == 0:
            return LTSState(1, ROLE_OWNER, 0, 0, 0, cfg.retry_budget)
        return REJECT

    if action is Act.RETRY:
        if a == 1 and c == 0 and b > 0:
            return LTSState(a, r, n, c, g, b - 1)
        return REJECT  # 预算耗尽或未持有资源：真正拒绝

    if action is Act.CANCEL:
        if a == 0:
            return REJECT
        if n > 0:
            return REJECT  # 读者引用未清空，取消不能释放资源
        return LTSState(0, ROLE_OWNER, 0, 0, 0, cfg.retry_budget)

    if action is Act.HANDOFF:
        if a == 1 and r == ROLE_OWNER:
            return LTSState(a, ROLE_BORROWER, n, c, g, b)
        return REJECT

    if action is Act.RECLAIM:
        if a == 1 and r == ROLE_BORROWER:
            return LTSState(a, ROLE_OWNER, n, c, g, b)
        return REJECT

    if action is Act.ADD_READER:
        if a == 1 and n < MAX_READERS:
            return LTSState(a, r, n + 1, c, g, b)
        return REJECT

    if action is Act.DROP_READER:
        if a == 1 and n > 0:
            return LTSState(a, r, n - 1, c, g, b)
        return REJECT

    if action is Act.CLEAN:
        if a == 1 and n == 0 and c == 0:
            return LTSState(a, r, 0, 1, g, b)
        return REJECT

    if action is Act.RETURN_OWNER:
        if a == 1 and r == ROLE_BORROWER and c == 1 and n == 0:
            return LTSState(a, ROLE_OWNER, 0, c, 1, b)
        return REJECT

    if action is Act.REUSE:
        if reuse_allowed(state.visible()):
            return LTSState(1, ROLE_OWNER, 0, 0, 0, cfg.retry_budget)
        return REJECT  # 双条件守卫：显式真正拒绝

    raise ValueError(f"未知动作 {action!r}")


def build_protocol(
    name: str,
    cfg: ProtocolConfig | None = None,
    defect: str | None = None,
) -> LTS:
    """构建协议 LTS。defect=None 为正确协议 P，其余为缺陷变体 Q。"""
    if defect not in DEFECTS:
        raise ValueError(f"未知缺陷类型 {defect!r}，可选 {DEFECTS}")
    cfg = cfg or ProtocolConfig()
    states = _all_states(cfg)
    initial = _initial(cfg)
    alphabet = cfg.alphabet()

    transitions: dict[LTSState, dict[int, object]] = {}
    observations: dict[object, tuple] = {}
    for state in states:
        table: dict[int, object] = {}
        for code in alphabet:
            action = Act(code)
            target = _successor(state, action, cfg)
            table[code] = target
        transitions[state] = table
        observations[state] = state.visible()

    accept = frozenset(
        s for s in states
        if s.acquired == 1 and s.cleaned == 1 and s.returned == 1
        and s.readers == 0 and s.role == ROLE_OWNER
    )
    valid_states = frozenset(states)

    if defect is not None:
        valid_states = _inject_defect(
            defect, cfg, transitions, observations, valid_states,
        )

    return LTS(
        name=name,
        states=frozenset(states),
        initial=initial,
        alphabet=alphabet,
        transitions=transitions,
        valid_states=valid_states,
        observations=observations,
        accept=accept,
    )


def _inject_defect(
    defect: str,
    cfg: ProtocolConfig,
    transitions: dict[LTSState, dict[int, object]],
    observations: dict[object, tuple],
    valid_states: frozenset,
) -> frozenset:
    """在正确协议迁移表上注入单一缺陷，返回（可能扩大的）合法状态集。"""
    shadow_seen: set[tuple] = set()
    invalid = _INVALID_TARGET
    for state, table in transitions.items():
        a, r, n, c, g, b = state.as_tuple()

        if defect == "missing":
            # 删掉“第三读者放下引用”的边：缺边
            if a == 1 and n == 1:
                table.pop(int(Act.DROP_READER), None)

        elif defect == "invalid":
            # 角色切换指向声明状态集之外的无效状态
            if a == 1 and r == ROLE_OWNER and int(Act.HANDOFF) in table:
                table[int(Act.HANDOFF)] = invalid

        elif defect == "reject":
            # 清理动作被错误地真正拒绝，必要清理永远无法完成
            if a == 1 and n == 0 and c == 0:
                table[int(Act.CLEAN)] = REJECT

        elif defect == "shadow":
            # 清理先进入一个不应开放的中间态，再做一次清理才到正确终态
            if a == 1 and n == 0 and c == 0:
                mid = _shadow_key(r, b)
                table[int(Act.CLEAN)] = mid
                shadow_seen.add(mid)

    if defect == "shadow":
        # 中间态的可见投影与正确协议的“已清理”不同：
        # 现场会记录到一个不应开放的可见状态
        for mid in shadow_seen:
            _, _, role, budget = mid
            observations[mid] = VisibleState(1, role, 0, 0, 0)
            mid_table: dict[int, object] = {
                code: REJECT for code in cfg.alphabet()
            }
            mid_table[int(Act.CLEAN)] = LTSState(1, role, 0, 1, 0, budget)
            transitions[mid] = mid_table  # type: ignore[assignment]
        # 影子态是 Q 的合法可达状态（有观察值、有出边），
        # 但它对应的可见状态不应被规约开放：分歧按 visible_mismatch 报告
        valid_states = valid_states | frozenset(shadow_seen)

    return valid_states
