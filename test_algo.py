import itertools
import unittest
from unittest.mock import patch
from algo import optimize_energy


class AlgoTests(unittest.TestCase):
    def run_model(self, prices, solar, load, soc=0., **kw):
        return optimize_energy([dict(time=str(i), price=p) for i, p in enumerate(prices)],
                               10., 10., soc, 0., 1., solar, load,
                               kw.pop('margin', .1), kw.pop('minimum_profit', .8), **kw)

    def assert_balance(self, rows, initial=0.):
        soc = initial
        for row in rows:
            self.assertAlmostEqual(row['solar_kw'] + row['grid_import_kw'] + max(0, -row['battery_kw']),
                                   row['consumption_kw'] + max(0, row['battery_kw']) + row['grid_export_kw'])
            self.assertLessEqual(abs(row['battery_kw']), 10 + 1e-8)
            self.assertGreaterEqual(row['soc'], -1e-8)
            self.assertLessEqual(row['soc'], 1 + 1e-8)
            soc += row['battery_kw'] * .25 / 10
            self.assertAlmostEqual(soc, row['soc'])

    def test_solar_continues_while_idle(self):
        rows = self.run_model([100], [33], [20], manual=[0])
        self.assertEqual(rows[0]['grid_kw'], -13)
        self.assertEqual(rows[0]['soc'], 0)
        self.assert_balance(rows)

    def test_grid_and_solar_can_charge_together(self):
        row = self.run_model([10], [23], [20], manual=[1])[0]
        self.assertEqual(row['solar_to_battery_kw'], 3)
        self.assertEqual(row['grid_to_battery_kw'], 7)
        self.assertEqual(row['grid_kw'], 7)

    def test_night_purchase(self):
        rows = self.run_model([10, 200], [0, 0], [0, 0])
        self.assertGreater(rows[0]['battery_kw'], 9.9)
        self.assertLess(rows[1]['battery_kw'], -9.9)
        self.assert_balance(rows)

    def test_sale_does_not_require_reserve(self):
        rows = self.run_model([200, 10], [0, 0], [0, 10], soc=.25)
        self.assertLess(rows[0]['grid_kw'], -9.9)
        self.assertGreater(rows[1]['grid_kw'], 9.9)
        self.assert_balance(rows, .25)

    def test_charge_penalty_unchanged(self):
        low = self.run_model([10, 20], [0, 0], [0, 0], minimum_profit=0)
        high = self.run_model([10, 20], [0, 0], [0, 0], minimum_profit=100)
        self.assertGreater(low[0]['battery_kw'], 0)
        self.assertEqual(high[0]['battery_kw'], 0)
        row = self.run_model([10], [0], [0], minimum_profit=5, manual=[1])[0]
        self.assertAlmostEqual(row['result_eur'], -(10 * 1.1 + 5) * 2.5 / 1000)

    def test_manual_limits_exact_soc(self):
        rows = self.run_model([-50, 100, 50], [0, 20, 0], [2, 5, 10], soc=.98765,
                              manual=[1, -1, 0])
        self.assertAlmostEqual(rows[0]['soc'], 1)
        self.assert_balance(rows, .98765)
        empty = self.run_model([50], [0], [10], manual=[-1])[0]
        self.assertEqual(empty['battery_kw'], 0)
        self.assertEqual(empty['grid_kw'], 10)

    def test_exact_initial_and_bounds(self):
        rows = self.run_model([-100, -100, 200, 200], [2]*4, [0]*4, soc=.98765)
        self.assert_balance(rows, .98765)

    def test_matches_exhaustive_small_problem(self):
        # Coarse states allow exhaustive comparison with independently computed cash flow.
        with patch('algo.ENERGY_STEPS', 2):
            rows = self.run_model([12, 95, -10], [0, 3, 0], [1, 0, 2], soc=.25)
        best = -float('inf')
        for actions in itertools.product([-10, -5, 0, 5, 10], repeat=3):
            energy, score = 2.5, 0
            for p, pv, load, battery in zip([12, 95, -10], [0, 3, 0], [1, 0, 2], actions):
                energy += battery * .25
                if not 0 <= energy <= 10:
                    break
                net = load + battery - pv
                score += (p * .9 * max(-net, 0) - p * 1.1 * max(net, 0) - .8 * max(battery, 0)) * .25 / 1000
            else:
                best = max(best, score)
        self.assertAlmostEqual(sum(r['result_eur'] for r in rows), best)

    def test_invalid_forecast_fails(self):
        with self.assertRaises(ValueError):
            self.run_model([10], [float('nan')], [0])


if __name__ == '__main__':
    unittest.main()
