"""小模型独立穷举器与引擎的交叉核对测试。"""

import unittest

from protoctl.engine import cleanup_liveness, shortest_divergence
from protoctl.lts import Budget
from protoctl.mini import mini_liveness, mini_shortest_divergence
from protoctl.protocol import ProtocolConfig, build_protocol

# 与 test_engine 中相同语义的手工 LTS，这里独立再建一遍，避免跨模块耦合
from tests.test_engine import _branching_lts, _deadend_lts, _trap_lts


class MiniDivergenceTests(unittest.TestCase):
    def test_matches_engine_for_all_defects(self):
        cfg = ProtocolConfig(retry_budget=1)
        p = build_protocol("P", cfg)
        for defect in ("missing", "invalid", "reject", "shadow"):
            q = build_protocol(f"Q-{defect}", cfg, defect)
            engine = shortest_divergence(p, q)
            mini = mini_shortest_divergence(p, q, max_depth=10)
            self.assertTrue(mini.complete)
            self.assertEqual(mini.word, engine.action_seq, defect)

    def test_budget_truncation_is_explicit(self):
        cfg = ProtocolConfig(retry_budget=1)
        p = build_protocol("P", cfg)
        q = build_protocol("Q", cfg, "reject")
        mini = mini_shortest_divergence(p, q, max_depth=10, budget=Budget(limit=1))
        self.assertFalse(mini.complete)
        self.assertFalse(mini.found)


class MiniLivenessTests(unittest.TestCase):
    def test_matches_engine_on_protocol_family(self):
        cfg = ProtocolConfig(retry_budget=1)
        for defect in (None, "missing", "invalid", "reject", "shadow"):
            lts = build_protocol("L", cfg, defect)
            engine = cleanup_liveness(lts)
            mini = mini_liveness(lts, max_depth=10)
            self.assertTrue(mini.complete)
            self.assertEqual(mini.ef_verdict, engine.ef_verdict, defect)
            self.assertEqual(mini.agef_verdict, engine.agef_verdict, defect)
            self.assertEqual(mini.af_verdict, engine.af_verdict, defect)

    def test_ef_vs_af_distinction_on_branching_lts(self):
        # 存在可完成路径，但并非所有合法路径都最终清理
        mini = mini_liveness(_branching_lts(), max_depth=8)
        self.assertEqual(mini.ef_verdict, "reachable")
        self.assertEqual(mini.agef_verdict, "holds")
        self.assertEqual(mini.af_verdict, "violated")
        self.assertGreater(mini.words_total, mini.words_to_accept)

    def test_trap_and_deadend_violate_both(self):
        for factory in (_trap_lts, _deadend_lts):
            mini = mini_liveness(factory(), max_depth=8)
            self.assertEqual(mini.ef_verdict, "unreachable")
            self.assertEqual(mini.agef_verdict, "violated")
            self.assertEqual(mini.af_verdict, "violated")
            self.assertGreater(mini.trapped_words, 0)

    def test_budget_exhaustion_yields_unknown(self):
        mini = mini_liveness(_branching_lts(), max_depth=8, budget=Budget(limit=1))
        self.assertFalse(mini.complete)
        self.assertEqual(mini.agef_verdict, "unknown")
        self.assertEqual(mini.af_verdict, "unknown")
        self.assertIn("仍未证", mini.note)


if __name__ == "__main__":
    unittest.main()
