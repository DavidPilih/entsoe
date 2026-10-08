import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import MagicMock
from unittest.mock import patch
import numpy as np
import pandas as pd
import prepare_data
import client


class PipelineTests(unittest.TestCase):
    def test_zero_solar_power_uses_fast_100_kw_fallback(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [(0.0, None), (110.0, None)]
        output = StringIO()
        with patch.object(prepare_data, 'fetch_consumption_device_id', return_value='solar-id'), \
             patch.object(prepare_data.psycopg2, 'connect', return_value=connection), \
             redirect_stdout(output):
            fallback = prepare_data.fetch_solar_parameters('03-enerArk')
            positive = prepare_data.fetch_solar_parameters('03-enerArk')
        self.assertEqual(fallback['rated_power_kw'], 100.0)
        self.assertEqual(positive['rated_power_kw'], 110.0)
        self.assertIn('uporabljam nadomestno moč 100 kW', output.getvalue())
        sql = cursor.execute.call_args_list[0].args[0]
        self.assertIn('ts_kv_latest', sql)
        self.assertNotIn('ts_kv t ', sql)

    def test_sql_queue_receives_native_float(self):
        fake_queue = MagicMock()
        with patch.object(client, 'db_queue', fake_queue):
            client.save_result('battery-id', [{
                'timestamp': '2026-10-08 21:15', 'value': np.float64(-0.57),
            }])
        device_id, rows = fake_queue.put.call_args.args[0]
        self.assertEqual(device_id, 'battery-id')
        self.assertIs(type(rows[0]['value']), float)
        self.assertEqual(rows[0]['value'], -0.57)

    def test_midnight_manual_and_telemetry_alignment(self):
        day = pd.Timestamp('2026-10-08', tz='Europe/Ljubljana')
        times = [day + pd.Timedelta(hours=23, minutes=45), day + pd.DateOffset(days=1)]
        manual = {client.convert_timestamp(times[0]): 1., client.convert_timestamp(times[1]): -1.}
        def prices(_file, start, end):
            return [(t.tz_localize(None), 100.) for t in times if start <= t < end]
        payload = dict(unique_id='03-enerArk', capacity=10., power=10., soc=.95,
                       date='2026-10-08', start_time='23:45', next_day=True,
                       use_sun_data=False, use_consumption=True, power_factor=.5)
        with patch.object(client, 'fetch_device_id', return_value='battery'), \
             patch.object(client, 'fetch_manual_schedule', return_value=manual), \
             patch.object(prepare_data, 'load_price_data', side_effect=prices), \
             patch.object(prepare_data, 'fetch_consumption_device_id', return_value='battery'), \
             patch.object(prepare_data, 'fetch_consumption_forecast', side_effect=lambda _, ts: [3.] * len(ts)), \
             patch.object(client, 'save_result') as save, patch.object(client, 'send_tb_device') as publish:
            result = client.process_request(payload, dry_run=True)
        save.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(len(result['data']), 2)
        self.assertAlmostEqual(result['data'][0]['power_setpoint[kW]'], 2.)
        self.assertAlmostEqual(result['data'][1]['power_setpoint[kW]'], -5.)
        self.assertAlmostEqual(result['energy_balance'][0]['soc'], 1.)
        self.assertAlmostEqual(result['energy_balance'][1]['soc'], .875)
        for cmd, balance in zip(result['data'], result['energy_balance']):
            self.assertAlmostEqual(cmd['power_setpoint[kW]'], balance['battery_kw'])
            point = next(p for p in result['telemetry'] if p['ts'] == cmd['timestamp'] and 'forecast_battery[kW]' in p['values'])
            self.assertEqual(point['values']['forecast_battery[kW]'], balance['battery_kw'])
            soc_point = next(p for p in result['telemetry'] if p['ts'] == cmd['timestamp'] + 900000 and 'forecasted_soc[%]' in p['values'])
            self.assertAlmostEqual(soc_point['values']['forecasted_soc[%]'], balance['soc'] * 100)
        self.assertFalse(any('schedule_auto' in p['values'] for p in result['telemetry']))

    def test_missing_solar_mapping_fails_before_publish(self):
        with self.assertRaisesRegex(ValueError, 'povezava'):
            prepare_data.fetch_solar_parameters('unconfigured-battery')


if __name__ == '__main__':
    unittest.main()
