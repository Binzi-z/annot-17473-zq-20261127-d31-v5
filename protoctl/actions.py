"""抽象动作编号。

协议中所有动作都只是合成编号（1..10），编号固定，便于跨进程与日志记录；
符号名仅供本项目内部实现使用，现场日志只记录编号。
"""

from __future__ import annotations

from enum import IntEnum


class Act(IntEnum):
    """抽象动作编号。"""

    ACQUIRE = 1        # 获取虚拟资源
    RETRY = 2          # 重试（受可重试虚拟资源数预算限制，可选）
    CANCEL = 3         # 取消（可选；读者引用未清空时不得释放资源）
    HANDOFF = 4        # 角色切换：所有者 -> 借用者
    RECLAIM = 5        # 角色收回：借用者 -> 所有者（可选）
    ADD_READER = 6     # 增加一个并行读者的抽象引用
    DROP_READER = 7    # 释放一个并行读者的抽象引用
    CLEAN = 8          # 资源按规定清理
    RETURN_OWNER = 9   # 归还原所有者
    REUSE = 10         # 复用工作区（受双条件守卫）


#: 可选动作：协议配置关闭时从动作字母表中移除
OPTIONAL = frozenset({Act.RETRY, Act.CANCEL, Act.RECLAIM})


def code(name: Act) -> int:
    """对外只暴露合成编号。"""
    return int(name)


def from_code(value: int) -> Act:
    """按编号解析动作，非法编号抛 KeyError（调用方按无效输入处理）。"""
    return Act(value)
