import unittest
from dart3s.structure_budget import select_structure, StructureBudgetExceeded


class StructureBudgetTests(unittest.TestCase):
    def run_selection(self, values, seed=56, limit=4096, retries=2):
        self.calls, self.reports = [], []
        initial = {'count': values[0], 'cells': object()}
        def regenerate(s):
            self.calls.append(s)
            return {'count': values[len(self.calls)], 'cells': object()}
        result = select_structure(initial, seed=seed, limit=limit, retries=retries,
            count=lambda v: v['count'], regenerate=regenerate, record=self.reports.append)
        return initial, result

    def test_accepted_original_unchanged_no_resample(self):
        original, result = self.run_selection([3000])
        self.assertIs(original, result)
        self.assertEqual(self.calls, [])

    def test_retry_only_structure_and_record_effective_seed(self):
        _, result = self.run_selection([7000, 6500, 3800])
        self.assertEqual(result['count'], 3800)
        self.assertEqual(self.calls, [57, 58])
        self.assertEqual(self.reports[-1]['accepted_seed'], 58)

    def test_over_budget_stops_after_bound(self):
        with self.assertRaises(StructureBudgetExceeded):
            self.run_selection([7000, 6000, 5000])
        self.assertEqual(self.calls, [57, 58])
        self.assertEqual(self.reports[-1]['status'], 'rejected')

    def test_empty_rejected_not_accepted_as_cheap(self):
        with self.assertRaises(StructureBudgetExceeded):
            self.run_selection([0], retries=0)

    def test_disabled_preserves_original(self):
        original, result = self.run_selection([20000], limit=0)
        self.assertIs(result, original)

    def test_seed_wrap(self):
        self.run_selection([7000, 3000], seed=2**64-1)
        self.assertEqual(self.calls, [0])

    def test_cancel_during_retry_propagates(self):
        def cancelled():
            raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            select_structure(0, seed=56, limit=100, retries=2, count=lambda v: v,
                regenerate=lambda s: 1, record=lambda r: None, check_cancel=cancelled)


if __name__ == '__main__':
    unittest.main()
