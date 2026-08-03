import importlib
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("NTFY_TOPIC", "topico-teste")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import monitor


class MonitorTests(unittest.TestCase):
    def test_normalize_for_search(self):
        self.assertIn(
            "enfermagem",
            monitor.normalize_for_search("Técnico em Enfer-\n magem"),
        )

    def test_ntfy_success(self):
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None

        with patch.object(monitor.SESSION, "post", return_value=response):
            result = monitor.send_notification(
                "Edital", "https://example.com/a.pdf",
                {"course_rows": [], "snippet": "enfermagem"},
            )
        self.assertTrue(result)

    def test_ntfy_http_error(self):
        response = MagicMock()
        response.text = "erro"
        error = monitor.requests.HTTPError("falhou", response=response)
        response.raise_for_status.side_effect = error

        with patch.object(monitor.SESSION, "post", return_value=response):
            result = monitor.send_notification(
                "Edital", "https://example.com/a.pdf",
                {"course_rows": [], "snippet": "enfermagem"},
            )
        self.assertFalse(result)

    def test_callmebot_explicit_error(self):
        response = MagicMock()
        response.status_code = 200
        response.text = "Invalid APIKEY"
        response.raise_for_status.return_value = None

        with patch.object(monitor, "CALLMEBOT_PHONE", "5586000000000"), \
             patch.object(monitor, "CALLMEBOT_APIKEY", "123"), \
             patch.object(monitor.SESSION, "get", return_value=response):
            result = monitor.send_whatsapp_notification(
                "Edital", "https://example.com/a.pdf",
                {"course_rows": [], "snippet": "enfermagem"},
            )
        self.assertFalse(result)

    def test_senac_get_rejects_external_domain(self):
        with self.assertRaises(ValueError):
            monitor.senac_get("https://example.com/arquivo.pdf")


if __name__ == "__main__":
    unittest.main()
