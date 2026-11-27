"""协议构建器与具体保护语义测试。"""

import unittest

from protoctl.actions import Act
from protoctl.lts import StepKind
from protoctl.protocol import DEFECTS, ProtocolConfig, build_protocol
from protoctl.states import ROLE_OWNER


def _step(lts, state, action):
    return lts.step(state, action)


class ProtocolConfigTests(unittest.TestCase):
    def test_alphabet_reflects_optional_switches(self):
        full = ProtocolConfig().alphabet()
        self.assertIn(int(Act.RETRY), full)
        self.assertIn(int(Act.CANCEL), full)
        self.assertIn(int(Act.RECLAIM), full)
        stripped = ProtocolConfig(
            allow_retry=False, allow_cancel=False, allow_reclaim=False,
        ).alphabet()
        self.assertNotIn(int(Act.RETRY), stripped)
        self.assertNotIn(int(Act.CANCEL), stripped)
        self.assertNotIn(int(Act.RECLAIM), stripped)
        self.assertEqual(len(stripped), 7)

    def test_defect_catalog(self):
        self.assertEqual(DEFECTS, (None, "missing", "invalid", "reject", "shadow"))


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = ProtocolConfig(retry_budget=2)
        self.p = build_protocol("P", self.cfg)
        self.s0 = self.p.initial

    def _acquire(self):
        r = _step(self.p, self.s0, Act.ACQUIRE)
        self.assertIs(r.kind, StepKind.MOVED)
        return r.target

    def test_cancel_cannot_release_with_reader_references(self):
        s = self._acquire()
        # 第三读者：连开三个并行读者
        for _ in range(3):
            r = _step(self.p, s, Act.ADD_READER)
            self.assertIs(r.kind, StepKind.MOVED)
            s = r.target
        self.assertEqual(s.visible().readers, 3)
        # 仅凭取消动作不能释放资源：真正拒绝
        r = _step(self.p, s, Act.CANCEL)
        self.assertIs(r.kind, StepKind.REJECTED)
        # 全部读者放下引用后，取消才允许
        for _ in range(3):
            r = _step(self.p, s, Act.DROP_READER)
            self.assertIs(r.kind, StepKind.MOVED)
            s = r.target
        r = _step(self.p, s, Act.CANCEL)
        self.assertIs(r.kind, StepKind.MOVED)
        self.assertEqual(r.target.acquired, 0)

    def test_reuse_requires_cleanup_and_return_both(self):
        s = self._acquire()
        # 未清理未归还：复用被真正拒绝
        self.assertIs(_step(self.p, s, Act.REUSE).kind, StepKind.REJECTED)
        # 只清理不归还
        r = _step(self.p, s, Act.CLEAN)
        self.assertIs(r.kind, StepKind.MOVED)
        s = r.target
        self.assertIs(_step(self.p, s, Act.REUSE).kind, StepKind.REJECTED)
        # 角色切换到借用者后，清理完成才能归还
        r = _step(self.p, s, Act.HANDOFF)
        s = r.target
        r = _step(self.p, s, Act.RETURN_OWNER)
        s = r.target
        self.assertEqual(s.visible().cleaned, 1)
        self.assertEqual(s.visible().returned, 1)
        # 双条件齐备且无读者：复用放行，进入新一轮持有
        r = _step(self.p, s, Act.REUSE)
        self.assertIs(r.kind, StepKind.MOVED)
        self.assertEqual(r.target.acquired, 1)
        self.assertEqual(r.target.cleaned, 0)
        self.assertEqual(r.target.returned, 0)

    def test_retry_budget_is_limited_and_hidden(self):
        s = self._acquire()
        for _ in range(self.cfg.retry_budget):
            r = _step(self.p, s, Act.RETRY)
            self.assertIs(r.kind, StepKind.MOVED)
            # 可见投影在重试前后不变（预算是隐藏内部状态）
            self.assertEqual(r.target.visible(), s.visible())
            s = r.target
        # 预算耗尽：真正拒绝，而不是缺边
        r = _step(self.p, s, Act.RETRY)
        self.assertIs(r.kind, StepKind.REJECTED)

    def test_handoff_and_reclaim_role_switch(self):
        s = self._acquire()
        r = _step(self.p, s, Act.HANDOFF)
        self.assertEqual(r.target.visible().role, 1)
        r = _step(self.p, r.target, Act.RECLAIM)
        self.assertEqual(r.target.visible().role, ROLE_OWNER)

    def test_happy_path_reaches_accept(self):
        word = (1, 4, 8, 9)
        s = self.s0
        for action in word:
            r = _step(self.p, s, action)
            self.assertIs(r.kind, StepKind.MOVED)
            s = r.target
        self.assertIn(s, self.p.accept)


if __name__ == "__main__":
    unittest.main()
