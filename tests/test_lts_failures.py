"""三类失败严格区分的测试：缺边、无效状态、真正拒绝。"""

import unittest

from protoctl.lts import LTS, REJECT, StepKind, reachable


def _tiny_lts() -> LTS:
    # s0 --1--> s1；s1 --2--> REJECT；s0 --3--> 越界无效状态；
    # 动作 4 在任何状态都没有边（缺边）
    states = frozenset({"s0", "s1"})
    transitions = {
        "s0": {1: "s1", 3: ("outside",)},
        "s1": {2: REJECT},
    }
    return LTS(
        name="tiny",
        states=states,
        initial="s0",
        alphabet=(1, 2, 3, 4),
        transitions=transitions,
        valid_states=states,
        observations={"s0": ("v0",), "s1": ("v1",)},
        accept=frozenset({"s1"}),
    )


class FailureKindTests(unittest.TestCase):
    def setUp(self):
        self.lts = _tiny_lts()

    def test_moved(self):
        result = self.lts.step("s0", 1)
        self.assertIs(result.kind, StepKind.MOVED)
        self.assertEqual(result.target, "s1")

    def test_missing_edge(self):
        result = self.lts.step("s0", 4)
        self.assertIs(result.kind, StepKind.MISSING_EDGE)
        self.assertIsNone(result.target)

    def test_invalid_target(self):
        result = self.lts.step("s0", 3)
        self.assertIs(result.kind, StepKind.INVALID_TARGET)
        self.assertEqual(result.target, ("outside",))

    def test_true_reject(self):
        result = self.lts.step("s1", 2)
        self.assertIs(result.kind, StepKind.REJECTED)
        self.assertIs(result.target, REJECT)

    def test_three_kinds_are_distinct_enum_values(self):
        kinds = {StepKind.MISSING_EDGE, StepKind.INVALID_TARGET,
                 StepKind.REJECTED, StepKind.MOVED}
        self.assertEqual(len(kinds), 4)

    def test_reachability_skips_non_moved(self):
        reach = reachable(self.lts)
        self.assertEqual(reach.reached, frozenset({"s0", "s1"}))
        # 边表中共 3 条迁移尝试（s0 的动作 1、3 与 s1 的动作 2）；
        # 动作 4 缺边不计，拒绝/无效边计入尝试但不产生新可达状态
        self.assertEqual(reach.edges_traversed, 3)
        self.assertTrue(reach.complete)


if __name__ == "__main__":
    unittest.main()
