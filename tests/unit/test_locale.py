"""Unit tests for locale files: key parity and placeholder parity (R33)."""

import os
import re
import unittest


def parse_simple_yaml(filepath: str) -> dict[str, str]:
    """Parse flat key-value YAML without external dependencies."""
    data = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                data[key] = val
    return data


class TestLocaleParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        en_path = os.path.join(base_dir, "locales", "en.yaml")
        fa_path = os.path.join(base_dir, "locales", "fa.yaml")

        cls.en = parse_simple_yaml(en_path)
        cls.fa = parse_simple_yaml(fa_path)

    def test_key_parity(self):
        """fa.yaml and en.yaml must have exact identical keys (R33)."""
        en_keys = set(self.en.keys())
        fa_keys = set(self.fa.keys())

        missing_in_fa = en_keys - fa_keys
        missing_in_en = fa_keys - en_keys

        self.assertEqual(
            missing_in_fa,
            set(),
            f"Keys in en.yaml missing from fa.yaml: {missing_in_fa}",
        )
        self.assertEqual(
            missing_in_en,
            set(),
            f"Keys in fa.yaml missing from en.yaml: {missing_in_en}",
        )

    def test_placeholder_parity(self):
        """Every key must have identical placeholders across both languages (R33)."""
        for key in self.en:
            en_placeholders = set(re.findall(r"\{([a-zA-Z0-9_]+)\}", self.en[key]))
            fa_placeholders = set(re.findall(r"\{([a-zA-Z0-9_]+)\}", self.fa[key]))

            self.assertEqual(
                en_placeholders,
                fa_placeholders,
                f"Placeholder mismatch for key '{key}': en={en_placeholders}, fa={fa_placeholders}",
            )


if __name__ == "__main__":
    unittest.main()
