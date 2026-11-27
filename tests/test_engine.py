"""引擎判定测试：最短分歧、三性质活性、最小见证、预算未证。"""

import unittest

from protoctl.actions import Act
from protoctl.engine import cleanup_liveness, shortest_divergence
from protoctl.lts import Budget, LTS, REJECT, StepKind, reachable
from protoctl.protocol import ProtocolConfig, build_protocol


class DivergenceTests(unittest.TestCase):
    def setUp(self):
        self.cfg = ProtocolConfig(retry_budget=1)
        self.p = build_protocol("P", self.cfg)

    def test_shortest_divergence_per_defect_classification(self):
        expected = {
            "missing": ((1, 6, 7), "missing_edge"),
            "invalid": ((1, 4), "invalid_target"),
            "reject": ((1, 8), "true_reject"),
            "shadow": ((1, 8), "visible_mismatch"),
        }
        for defect, (word, kind) in expected.items():
            q = build_protocol(f"Q-{defect}", self.cfg, defect)
            div = shortest_divergence(self.p, q)
            self.assertTrue(div.found, defect)
            self.assertEqual(div.action_seq, word, defect)
            self.assertEqual(div.divergence_type, kind, defect)
            self.assertTrue(div.complete)

    def test_identical_protocols_have_no_divergence(self):
        twin = build_protocol("P2", self.cfg)
        div = shortest_divergence(self.p, twin)
        self.assertFalse(div.found)
        self.assertEqual(div.action_seq, ())
        self.assertTrue(div.complete)

    def test_divergence_word_is_actually_minimal(self):
        # 最短词的每个严格前缀都不应产生分歧（直接用引擎逐前缀复算）
        q = build_protocol("Q-shadow", self.cfg, "shadow")
        div = shortest_divergence(self.p, q)
        sa, sb = self.p.initial, q.initial
        for action in div.action_seq[:-1]:
            ra, rb = self.p.step(sa, action), q.step(sb, action)
            self.assertEqual(ra.kind, rb.kind)
            if ra.kind is StepKind.MOVED:
                self.assertEqual(
                    self.p.observe(ra.target), q.observe(rb.target)
                )
                sa, sb = ra.target, rb.target

    def test_alphabet_mismatch_rejected(self):
        other_cfg = ProtocolConfig(allow_cancel=False)
        q = build_protocol("Q", other_cfg)
        with self.assertRaises(ValueError):
            shortest_divergence(self.p, q)


def _branching_lts() -> LTS:
    """手工 LTS：s0 有两条合法路径，一条到接受态、一条进自愿环。

    s0 --1--> s1 --2--> accept（可完成路径存在）
    s0 --3--> loop，loop --4--> loop（自愿打转，AF 违反但 AG(EF) 成立）
    因此 EF=reachable、AG(EF)=holds、AF=violated，三个性质互不相同。
    """
    states = frozenset({"s0", "s1", "loop", "accept"})
    transitions = {
        "s0": {1: "s1", 3: "loop"},
        "s1": {2: "accept"},
        "loop": {4: "loop"},
        "accept": {},
    }
    return LTS(
        name="branching", states=states, initial="s0",
        alphabet=(1, 2, 3, 4), transitions=transitions,
        valid_states=states,
        observations={s: (s,) for s in states},
        accept=frozenset({"accept"}),
    )


def _trap_lts() -> LTS:
    """s0 --1--> trap，trap --2--> trap：无接受态可达，纯环陷阱。"""
    states = frozenset({"s0", "trap"})
    transitions = {"s0": {1: "trap"}, "trap": {2: "trap"}}
    return LTS(
        name="trap", states=states, initial="s0", alphabet=(1, 2),
        transitions=transitions, valid_states=states,
        observations={s: (s,) for s in states}, accept=frozenset(),
    )


def _deadend_lts() -> LTS:
    """s0 --1--> dead：非接受态死端，死端见证。"""
    states = frozenset({"s0", "dead"})
    transitions = {"s0": {1: "dead"}, "dead": {}}
    return LTS(
        name="deadend", states=states, initial="s0", alphabet=(1, 2),
        transitions=transitions, valid_states=states,
        observations={s: (s,) for s in states}, accept=frozenset(),
    )


class LivenessTests(unittest.TestCase):
    def test_three_properties_are_distinct_on_branching_lts(self):
        v = cleanup_liveness(_branching_lts())
        self.assertEqual(v.ef_verdict, "reachable")
        self.assertEqual(v.ef_path, (1, 2))
        self.assertEqual(v.agef_verdict, "holds")
        self.assertEqual(v.af_verdict, "violated")
        # AF 违反见证：从 s0 沿动作 3 进入自愿环，环为 (4,)
        self.assertIsNotNone(v.af_witness)
        self.assertEqual(v.af_witness.prefix, (3,))
        self.assertEqual(v.af_witness.cycle, (4,))

    def test_pure_cycle_trap_witness(self):
        v = cleanup_liveness(_trap_lts())
        self.assertEqual(v.ef_verdict, "unreachable")
        self.assertEqual(v.agef_verdict, "violated")
        self.assertEqual(v.af_verdict, "violated")
        w = v.witness
        self.assertEqual(w.kind, "cycle")
        self.assertEqual(w.prefix, (1,))
        self.assertEqual(w.cycle, (2,))
        self.assertEqual(w.total_length, 2)

    def test_deadend_witness(self):
        v = cleanup_liveness(_deadend_lts())
        self.assertEqual(v.agef_verdict, "violated")
        w = v.witness
        self.assertEqual(w.kind, "deadend")
        self.assertEqual(w.prefix, (1,))
        self.assertEqual(w.cycle, ())

    def test_correct_protocol_properties(self):
        p = build_protocol("P", ProtocolConfig(retry_budget=1))
        v = cleanup_liveness(p)
        self.assertEqual(v.ef_verdict, "reachable")
        self.assertEqual(v.agef_verdict, "holds")
        # 存在自愿 acquire/cancel 永不清扫的合法路径：AF 不成立
        self.assertEqual(v.af_verdict, "violated")
        self.assertIsNone(v.witness)
        self.assertIsNotNone(v.af_witness)


class BudgetTests(unittest.TestCase):
    def test_exhausted_budget_marks_unknown_not_guess(self):
        lts = _branching_lts()
        v = cleanup_liveness(lts, Budget(limit=1))
        self.assertFalse(v.reach.complete)
        self.assertEqual(v.ef_verdict, "unknown")
        self.assertEqual(v.agef_verdict, "unknown")
        self.assertEqual(v.af_verdict, "unknown")
        self.assertIsNone(v.witness)
        self.assertTrue(v.reach.budget.exhausted)

    def test_sufficient_budget_completes(self):
        v = cleanup_liveness(_branching_lts(), Budget(limit=64))
        self.assertTrue(v.reach.complete)
        self.assertEqual(v.agef_verdict, "holds")


if __name__ == "__main__":
    unittest.main()
