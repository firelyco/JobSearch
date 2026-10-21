import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from src import private_fit_store as store

class TransportRetries(unittest.TestCase):
 def test_transient_read_error_retries_and_recovers(self):
  send=Mock(side_effect=[SimpleNamespace(status_code=500),SimpleNamespace(status_code=200)])
  with patch.object(store.time,'sleep'):
   self.assertEqual(store._send(send).status_code,200)
  self.assertEqual(send.call_count,2)
 def test_not_found_and_auth_failures_are_not_retried(self):
  for status in [404,401,403]:
   send=Mock(return_value=SimpleNamespace(status_code=status))
   self.assertEqual(store._send(send).status_code,status)
   self.assertEqual(send.call_count,1)
 def test_transient_failure_is_bounded(self):
  send=Mock(return_value=SimpleNamespace(status_code=503))
  with patch.object(store.time,'sleep'):
   self.assertEqual(store._send(send).status_code,503)
  self.assertEqual(send.call_count,3)
 def test_connection_error_can_recover(self):
  send=Mock(side_effect=[store.requests.Timeout(),SimpleNamespace(status_code=200)])
  with patch.object(store.time,'sleep'):
   self.assertEqual(store._send(send).status_code,200)
