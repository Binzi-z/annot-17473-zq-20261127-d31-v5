"""编号化工作区模拟器与双真实本地进程测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from protoctl.actions import Act
from protoctl.engine import cleanup_liveness, shortest_divergence
from protoctl.lts import StepKind
from protoctl.protocol import ProtocolConfig, build_protocol
from protoctl.simulator import (
    WorkspaceJob,
    divergence_from_logs,
    run_job,
    run_two_process_crosscheck,
)


class SimulatorInProcessTests(unittest.TestCase):
    def setUp(self):
        self.cfg = ProtocolConfig(retry_budget=1)
        self.p = build_protocol("P", self.cfg)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_log_records_only_action_numbers_and_visible_ints(self):
        job = WorkspaceJob(
            "WS-T1", self.p, (1, 6, 6, 6, 3),
            str(Path(self.tmp.name) / "t1.json"),
        )
        summary = run_job(job)
        payload = json.loads(Path(summary["log_path"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["workspace_id"], "WS-T1")
        allowed_row_keys = {"step", "action", "result", "visible"}
        allowed_visible_keys = {
            "acquired", "role", "readers", "cleaned", "returned",
        }
        for row in payload["rows"]:
            self.assertEqual(set(row), allowed_row_keys)
            self.assertIsInstance(row["action"], int)
            self.assertIsInstance(row["result"], int)
            if row["visible"] is not None:
                self.assertEqual(set(row["visible"]), allowed_visible_keys)
                self.assertTrue(
                    all(isinstance(v, int) for v in row["visible"].values())
                )

    def test_cancel_rejection_visible_remains_none_on_row(self):
        job = WorkspaceJob(
            "WS-T2", self.p, (1, 6, 3),
            str(Path(self.tmp.name) / "t2.json"),
        )
        run_job(job)
        rows = json.loads(
            Path(job.log_path).read_text(encoding="utf-8")
        )["rows"]
        cancel = next(r for r in rows if r["action"] == int(Act.CANCEL))
        self.assertEqual(cancel["result"], int(StepKind.REJECTED))
        self.assertIsNone(cancel["visible"])

    def test_divergence_from_logs_locates_first_step(self):
        word = (1, 8)
        p_job = WorkspaceJob(
            "WS-A", self.p, word, str(Path(self.tmp.name) / "a.json"),
        )
        q = build_protocol("Q", self.cfg, "reject")
        q_job = WorkspaceJob(
            "WS-B", q, word, str(Path(self.tmp.name) / "b.json"),
        )
        run_job(p_job)
        run_job(q_job)
        step, found_word, detail = divergence_from_logs(
            json.loads(Path(p_job.log_path).read_text(encoding="utf-8")),
            json.loads(Path(q_job.log_path).read_text(encoding="utf-8")),
        )
        self.assertEqual(step, 2)
        self.assertEqual(found_word, word)
        self.assertEqual(detail["result_p"], int(StepKind.MOVED))
        self.assertEqual(detail["result_q"], int(StepKind.REJECTED))


class TwoRealProcessTests(unittest.TestCase):
    def setUp(self):
        self.cfg = ProtocolConfig(retry_budget=1)
        self.p = build_protocol("P-正确", self.cfg)
        self.q = build_protocol("Q-拒绝", self.cfg, "reject")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_two_distinct_processes_reproduce_divergence_and_trap(self):
        div = shortest_divergence(self.p, self.q)
        job_p = WorkspaceJob(
            "WS-P", self.p, div.action_seq,
            str(Path(self.tmp.name) / "p.json"), expect_accept=False,
        )
        job_q = WorkspaceJob(
            "WS-Q", self.q, div.action_seq,
            str(Path(self.tmp.name) / "q.json"), expect_accept=False,
        )
        cross = run_two_process_crosscheck(job_p, job_q)

        # 两个真实独立进程
        self.assertNotEqual(cross.p_pid, cross.q_pid)
        self.assertGreater(cross.p_pid, 0)
        self.assertGreater(cross.q_pid, 0)
        # 父进程只凭日志独立定位的分歧与引擎一致
        self.assertEqual(cross.first_divergence_word, div.action_seq)
        self.assertEqual(cross.divergence_result_pair, (3, 2))

        # 最小不可清理环现场复核
        verdict = cleanup_liveness(self.q)
        self.assertEqual(verdict.witness.kind, "cycle")
        trap_p = WorkspaceJob(
            "WS-TP", self.p, verdict.witness.prefix,
            str(Path(self.tmp.name) / "tp.json"),
            repeat_cycle=verdict.witness.cycle, cycle_rounds=3,
        )
        trap_q = WorkspaceJob(
            "WS-TQ", self.q, verdict.witness.prefix,
            str(Path(self.tmp.name) / "tq.json"),
            repeat_cycle=verdict.witness.cycle, cycle_rounds=3,
            expect_accept=False,
        )
        trap = run_two_process_crosscheck(trap_p, trap_q)
        self.assertTrue(trap.trap_confirmed)
        self.assertNotEqual(trap.p_pid, trap.q_pid)


if __name__ == "__main__":
    unittest.main()
