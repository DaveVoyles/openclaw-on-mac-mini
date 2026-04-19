"""Security tests for scheduler_advanced condition evaluation."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from scheduler_advanced import evaluate_condition
except ImportError:
    pytest.skip("scheduler_advanced not available", allow_module_level=True)


def test_evaluate_simple_true():
    assert evaluate_condition("x > 0", {"x": 1}) is True


def test_evaluate_simple_false():
    assert evaluate_condition("x > 10", {"x": 1}) is False


def test_evaluate_rejects_builtins():
    with pytest.raises((ValueError, NameError, TypeError)):
        evaluate_condition("__import__('os').system('echo')", {})


def test_evaluate_rejects_unknown_variable():
    with pytest.raises(ValueError, match="Unknown variable"):
        evaluate_condition("unknown_var > 0", {"x": 1})


def test_evaluate_rejects_too_long_expression():
    long_expr = "x > " + "0 and x > " * 60 + "0"
    with pytest.raises(ValueError, match="too long"):
        evaluate_condition(long_expr, {"x": 1})


def test_evaluate_boolean_and():
    assert evaluate_condition("x > 0 and y < 10", {"x": 1, "y": 5}) is True


def test_evaluate_boolean_or():
    assert evaluate_condition("x > 100 or y < 10", {"x": 1, "y": 5}) is True


def test_evaluate_rejects_function_call():
    with pytest.raises((ValueError, NameError)):
        evaluate_condition("len(x) > 0", {"x": [1, 2]})


def test_evaluate_comparison_operators():
    ctx = {"a": 5, "b": 10}
    assert evaluate_condition("a < b", ctx) is True
    assert evaluate_condition("a <= b", ctx) is True
    assert evaluate_condition("a == b", ctx) is False
    assert evaluate_condition("a != b", ctx) is True
