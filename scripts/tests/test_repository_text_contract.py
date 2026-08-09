from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RepositoryTextContractTests(unittest.TestCase):
    def test_exact_build_inputs_are_checked_out_with_lf_endings(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("/.github/workflows/** text eol=lf", attributes.splitlines())
        self.assertIn("/scripts/** text eol=lf", attributes.splitlines())

    def test_quiche_locks_match_the_pinned_byte_digests(self) -> None:
        import hashlib
        import importlib.util

        script = ROOT / "scripts" / "prepare_pinned_conan.py"
        specification = importlib.util.spec_from_file_location(
            "prepare_pinned_conan_text_contract", script
        )
        assert specification is not None and specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        self.assertEqual(
            hashlib.sha256(module.QUICHE_LOCK.read_bytes()).hexdigest(),
            module.QUICHE_LOCK_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(module.QUICHE_RING_017_LOCK.read_bytes()).hexdigest(),
            module.QUICHE_RING_017_LOCK_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
