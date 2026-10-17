"""Timing accounting for repeated lazy nodes, overhead and cache hits."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("benchmark", Path(__file__).resolve().parents[1] / "scripts/benchmark_two_inputs.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TimingTests(unittest.TestCase):
    def test_partition_repeated_nodes_cached_nodes_and_terminal_gap(self):
        def e(kind, seconds, node=None, nodes=None):
            return dict(event=kind, monotonic=seconds, utc=str(seconds), prompt_id="p", node=node, nodes=nodes or [])
        events = [e("execution_start", 10), e("execution_cached", 10.5, nodes=["15"]),
                  e("executing", 11, "3"), e("executing", 14, "314"),
                  e("executing", 15, "18"), e("executing", 20, "314"),
                  e("executing", 22, None), e("execution_success", 23)]
        graph = {"3": dict(class_type="KSampler"), "18": dict(class_type="KSampler"),
                 "314": dict(class_type="ComfySwitchNode"), "15": dict(class_type="CLIPVisionLoader")}
        result = MODULE.calculate_timings(events, graph, "p")
        self.assertEqual(result["server_seconds"], 13)
        self.assertEqual(result["groups"]["overhead"], 2)
        self.assertEqual(result["groups"]["structure"], 3)
        self.assertEqual(result["groups"]["shape"], 5)
        nodes = {n["node_id"]: n for n in result["nodes"]}
        self.assertEqual(nodes["314"]["seconds"], 3)
        self.assertEqual(nodes["314"]["execution_chunks"], 2)
        self.assertTrue(nodes["15"]["cached"])
        self.assertEqual(nodes["15"]["seconds"], 0)
        self.assertEqual(sum(result["groups"].values()), 13)

    def test_failed_job_cannot_be_reported_as_success(self):
        events = [dict(event="execution_start", monotonic=0, utc="a", prompt_id="p"),
                  dict(event="execution_error", monotonic=2, utc="b", prompt_id="p")]
        with self.assertRaises(RuntimeError):
            MODULE.calculate_timings(events, {}, "p")


if __name__ == "__main__":
    unittest.main()
