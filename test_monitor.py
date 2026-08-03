import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "course_groups": [
                {
                    "name": "Técnico em Enfermagem",
                    "terms": [
                        "técnico em enfermagem",
                        "enfermagem",
                        "técnico de enfermagem",
                    ],
                },
                {
                    "name": "Administrador de Redes",
                    "terms": [
                        "administrador de redes",
                        "redes de computadores",
                        "infraestrutura de redes",
                    ],
                },
            ],
            "matching": {"fuzzy_threshold": 0.84},
        }

    def test_enfermagem_exata(self):
        result = monitor.find_course_matches(
            "Curso Técnico em Enfermagem", self.config
        )
        self.assertEqual(result[0]["group"], "Técnico em Enfermagem")

    def test_enfermagem_com_quebra(self):
        result = monitor.find_course_matches(
            "Técnico em Enfer-\n magem", self.config
        )
        self.assertTrue(result)

    def test_rede_relacionada(self):
        result = monitor.find_course_matches(
            "Formação em infraestrutura de redes", self.config
        )
        self.assertEqual(result[0]["group"], "Administrador de Redes")

    def test_sem_match(self):
        result = monitor.find_course_matches(
            "Curso de confeitaria profissional", self.config
        )
        self.assertEqual(result, [])

    def test_domínio_externo_bloqueado(self):
        with self.assertRaises(ValueError):
            monitor.senac_get("https://example.com/teste.pdf")


if __name__ == "__main__":
    unittest.main()
