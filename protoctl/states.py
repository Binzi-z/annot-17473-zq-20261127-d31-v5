"""有限状态与可见状态投影。

完整状态是内部状态（含隐藏的剩余重试预算）；可见状态是对照器对外、
对日志、对双进程模拟器唯一暴露的投影。两个完整状态可能投影到同一个
可见状态（重试预算不可见），这正是商压缩要合并的等价类。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 角色编号：0 = 所有者，1 = 借用者
ROLE_OWNER = 0
ROLE_BORROWER = 1

#: 并行读者上限（第三读者即 readers == 3 的情形）
MAX_READERS = 3


@dataclass(frozen=True, slots=True)
class VisibleState:
    """对外可见状态：全部字段为整数，可直接 JSON 序列化。"""

    acquired: int       # 资源是否持有：0/1
    role: int           # 当前角色：ROLE_OWNER / ROLE_BORROWER
    readers: int        # 并行读者持有的抽象引用数 0..3
    cleaned: int        # “资源已按规定清理”：0/1
    returned: int       # “归还原所有者”：0/1

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return (self.acquired, self.role, self.readers, self.cleaned, self.returned)

    def as_log(self) -> dict[str, int]:
        """现场日志形态：只含整数可见状态，不含任何内部字段。"""
        return {
            "acquired": self.acquired,
            "role": self.role,
            "readers": self.readers,
            "cleaned": self.cleaned,
            "returned": self.returned,
        }


@dataclass(frozen=True, slots=True)
class LTSState:
    """协议完整内部状态。"""

    acquired: int
    role: int
    readers: int
    cleaned: int
    returned: int
    retries_left: int   # 隐藏字段：不出现在可见投影中

    def visible(self) -> VisibleState:
        return VisibleState(
            acquired=self.acquired,
            role=self.role,
            readers=self.readers,
            cleaned=self.cleaned,
            returned=self.returned,
        )

    def as_tuple(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.acquired,
            self.role,
            self.readers,
            self.cleaned,
            self.returned,
            self.retries_left,
        )


#: 受保护的复用许可：只有“已清理”且“已归还”且无残留读者引用时才允许复用。
def reuse_allowed(visible: VisibleState) -> bool:
    return (
        visible.acquired == 1
        and visible.cleaned == 1
        and visible.returned == 1
        and visible.readers == 0
    )
