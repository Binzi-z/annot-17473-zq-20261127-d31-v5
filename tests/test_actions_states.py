"""动作编号与可见状态投影测试。"""

import unittest

from protoctl.actions import Act, OPTIONAL, from_code
from protoctl.states import (
    MAX_READERS,
    ROLE_BORROWER,
    ROLE_OWNER,
    LTSState,
    reuse_allowed,
)


class ActionTests(unittest.TestCase):
    def test_codes_are_stable_integers(self):
        self.assertEqual(int(Act.ACQUIRE), 1)
        self.assertEqual(int(Act.REUSE), 10)
        for act in Act:
            self.assertIsInstance(int(act), int)
            self.assertEqual(from_code(int(act)), act)
        with self.assertRaises(ValueError):
            from_code(999)

    def test_optional_set(self):
        self.assertEqual(
            {int(a) for a in OPTIONAL},
            {int(Act.RETRY), int(Act.CANCEL), int(Act.RECLAIM)},
        )


class VisibleStateTests(unittest.TestCase):
    def test_hidden_retry_budget_not_observable(self):
        s1 = LTSState(1, ROLE_OWNER, 0, 0, 0, 2)
        s2 = LTSState(1, ROLE_OWNER, 0, 0, 0, 1)
        self.assertEqual(s1.visible(), s2.visible())
        self.assertNotIn("retries", s1.visible().as_log())
        self.assertEqual(
            set(s1.visible().as_log()),
            {"acquired", "role", "readers", "cleaned", "returned"},
        )

    def test_reuse_guard_requires_both_cleanup_and_return(self):
        base = dict(acquired=1, role=ROLE_OWNER, readers=0)
        from protoctl.states import VisibleState

        self.assertFalse(reuse_allowed(VisibleState(**base, cleaned=0, returned=0)))
        self.assertFalse(reuse_allowed(VisibleState(**base, cleaned=1, returned=0)))
        self.assertFalse(reuse_allowed(VisibleState(**base, cleaned=0, returned=1)))
        # 残留读者引用时即使双条件完成也不得复用
        self.assertFalse(reuse_allowed(
            VisibleState(acquired=1, role=ROLE_OWNER, readers=1, cleaned=1, returned=1)
        ))
        self.assertTrue(reuse_allowed(
            VisibleState(acquired=1, role=ROLE_OWNER, readers=0, cleaned=1, returned=1)
        ))

    def test_max_readers_covers_third_reader(self):
        self.assertEqual(MAX_READERS, 3)
        self.assertNotEqual(ROLE_OWNER, ROLE_BORROWER)


if __name__ == "__main__":
    unittest.main()
