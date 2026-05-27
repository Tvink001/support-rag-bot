"""Pure eval-metric helpers from test-data/run_eval.py (Prompt 11, §19)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "test-data" / "run_eval.py"
_spec = importlib.util.spec_from_file_location("run_eval", _PATH)
assert _spec is not None and _spec.loader is not None
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)


def test_precision_at_k() -> None:
    assert run_eval.precision_at_k([True, False, True, False, False], 5) == 0.4
    assert run_eval.precision_at_k([], 5) == 0.0


def test_hit_at_k() -> None:
    assert run_eval.hit_at_k([False, False, True], 10) == 1.0
    assert run_eval.hit_at_k([False, False, False], 10) == 0.0
    assert run_eval.hit_at_k([True], 0) == 0.0  # empty slice


def test_reciprocal_rank() -> None:
    assert run_eval.reciprocal_rank([False, True, False]) == 0.5
    assert run_eval.reciprocal_rank([True]) == 1.0
    assert run_eval.reciprocal_rank([False, False]) == 0.0


def test_is_relevant_is_case_insensitive() -> None:
    assert run_eval.is_relevant("Гарантия 24 месяца", ["24 месяца"]) is True
    assert run_eval.is_relevant("ГАРАНТИЯ 24 МЕСЯЦА", ["24 месяца"]) is True
    assert run_eval.is_relevant("нет совпадения", ["xyz"]) is False
