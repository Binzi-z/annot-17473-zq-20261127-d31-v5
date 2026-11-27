"""商压缩与同构分支规模夹具测试。"""

import unittest

from protoctl.engine import cleanup_liveness, shortest_divergence
from protoctl.lts import reachable
from protoctl.protocol import ProtocolConfig, build_protocol
from protoctl.quotient import (
    build_branch_fixture,
    consistency_report,
    observational_quotient,
)


class QuotientTests(unittest.TestCase):
    def test_hidden_budget_states_share_observation(self):
        p = build_protocol("P", ProtocolConfig(retry_budget=2))
        q = observational_quotient(p)
        self.assertLess(q.quotient_states, q.original_reachable)
        self.assertGreater(q.merged_pairs, 0)

    def test_liveness_invariant_under_quotient(self):
        p = build_protocol("P", ProtocolConfig(retry_budget=2))
        raw = cleanup_liveness(p)
        quo = cleanup_liveness(observational_quotient(p).lts)
        self.assertEqual(raw.ef_verdict, quo.ef_verdict)
        self.assertEqual(raw.agef_verdict, quo.agef_verdict)
        self.assertEqual(raw.af_verdict, quo.af_verdict)

    def test_quotient_does_not_introduce_divergence_with_self(self):
        p = build_protocol("P", ProtocolConfig(retry_budget=1))
        q = observational_quotient(p).lts
        self.assertFalse(shortest_divergence(q, q).found)

    def test_branch_fixture_isomorphic_subtrees_collapse(self):
        fixture = build_branch_fixture("F", depth=3, branching=3)
        raw_count = len(reachable(fixture).reached)
        q = observational_quotient(fixture)
        # 同构分支：等价类数只与深度相关（每层一个类 + 完成态）
        self.assertEqual(q.quotient_states, 5)
        self.assertLess(q.quotient_states * 4, raw_count)

    def test_consistency_raw_vs_quotient_across_defects(self):
        for defect in (None, "reject", "missing", "shadow"):
            good = build_branch_fixture("G", depth=2, branching=2)
            bad = build_branch_fixture("B", depth=2, branching=2, defect=defect)
            report = consistency_report(good, bad)
            self.assertTrue(report["consistent"], defect)
            self.assertEqual(
                report["raw"]["divergence"], report["quotient"]["divergence"],
                defect,
            )

    def test_budget_exhaustion_reported_as_incomplete(self):
        good = build_branch_fixture("G", depth=3, branching=2)
        bad = build_branch_fixture("B", depth=3, branching=2, defect="reject")
        report = consistency_report(good, bad, budget_limit=2)
        self.assertFalse(report["raw_complete"])
        self.assertFalse(report["quotient_complete"])


if __name__ == "__main__":
    unittest.main()
