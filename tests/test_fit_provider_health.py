import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
from src import fit_provider_health as health
from src.llm_client import LLMResponse

class ProviderHealthTests(unittest.TestCase):
    def invoke(self, response=None, error=None):
        output = io.StringIO()
        with patch.object(health.fit, 'load_json', return_value={'evidence_review_model': 'test-model'}), patch.object(health.llm_client, 'build_client'), patch.object(health.llm_client, 'call', return_value=response, side_effect=error), contextlib.redirect_stdout(output):
            code = health.main()
        return code, json.loads(output.getvalue())

    def test_usable_response_records_usage(self):
        code, result = self.invoke(LLMResponse('OK', 12, 2, 'test-model'))
        self.assertEqual(code, 0)
        self.assertEqual(result['input_tokens'], 12)
        self.assertEqual(result['output_tokens'], 2)

    def test_empty_response_fails(self):
        code, result = self.invoke(LLMResponse('', 12, 256, 'test-model'))
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'error')

    def test_provider_error_redacts_key(self):
        with patch.dict(os.environ, {'NVIDIA_API_KEY': 'unit-test-secret-value'}):
            code, result = self.invoke(error=RuntimeError('invalid unit-test-secret-value'))
        self.assertEqual(code, 1)
        self.assertNotIn('unit-test-secret-value', result['message'])

if __name__ == '__main__':
    unittest.main()
