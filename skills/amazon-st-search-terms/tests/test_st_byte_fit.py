from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "st_byte_fit.py"
SPEC = importlib.util.spec_from_file_location("st_byte_fit", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {SCRIPT_PATH}")
ST_BYTE_FIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ST_BYTE_FIT)


class StByteFitTests(unittest.TestCase):
    def test_spaces_are_not_amazon_counted(self) -> None:
        result = ST_BYTE_FIT.fit_terms(["alpha", "beta"], 250)

        self.assertEqual(result["search_terms"], "alpha beta")
        self.assertEqual(result["amazon_counted_bytes"], 9)
        self.assertEqual(result["raw_utf8_bytes"], 10)

    def test_exclusive_250_byte_boundary(self) -> None:
        fits = ST_BYTE_FIT.fit_terms(["a" * 249], 250)
        over = ST_BYTE_FIT.fit_terms(["b" * 250], 250)

        self.assertEqual(fits["amazon_counted_bytes"], 249)
        self.assertEqual(fits["kept_terms"], ["a" * 249])
        self.assertEqual(over["amazon_counted_bytes"], 0)
        self.assertEqual(over["removed_terms"], ["b" * 250])

    def test_multibyte_words_and_relevant_numbers_are_preserved(self) -> None:
        result = ST_BYTE_FIT.fit_terms(["保温杯", "3d", "2in1", "12oz"], 250)

        self.assertEqual(result["search_terms"], "保温杯 3d 2in1 12oz")
        self.assertEqual(result["amazon_counted_bytes"], 19)
        self.assertEqual(result["raw_utf8_bytes"], 22)

    def test_normalization_and_global_deduplication(self) -> None:
        result = ST_BYTE_FIT.fit_terms(
            ["Alpha-beta", "ALPHA", "gamma_delta", "!!!"], 250
        )

        self.assertEqual(result["search_terms"], "alpha beta gamma delta")
        self.assertEqual(result["kept_terms"], ["alpha", "beta", "gamma", "delta"])
        self.assertEqual(result["removed_terms"], [])

    def test_shorter_later_words_can_use_remaining_capacity(self) -> None:
        result = ST_BYTE_FIT.fit_terms(["abcdefghij", "abc", "def"], 10)

        self.assertEqual(result["search_terms"], "abc def")
        self.assertEqual(result["amazon_counted_bytes"], 6)
        self.assertEqual(result["removed_terms"], ["abcdefghij"])

    def test_cli_terms_accepts_leading_hyphens(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--limit",
                "40",
                "--terms",
                "-foo",
                "-bar",
            ],
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["search_terms"], "foo bar")

    def test_cli_rejects_options_after_terms(self) -> None:
        misplaced_options = [
            ["--limit", "10"],
            ["--limit=10"],
            ["--stdin"],
            ["--terms", "bar"],
            ["-h"],
            ["--help"],
        ]

        for option_args in misplaced_options:
            with self.subTest(option_args=option_args):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT_PATH),
                        "--terms",
                        "foo",
                        *option_args,
                    ],
                    text=True,
                    capture_output=True,
                )

                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn("must appear before --terms", completed.stderr)


if __name__ == "__main__":
    unittest.main()
