"""Safe arithmetic expression evaluation over variable names.

Only a whitelisted subset of Python expression syntax is accepted, so a topic
spec written by a model or a student can never execute arbitrary code.
"""
from __future__ import annotations

import ast
import functools
import math
import operator
from typing import Any, Callable, Dict, Iterable, List, Set


class ExprError(ValueError):
    pass


def _safe_pow(a: float, b: float) -> float:
    if abs(b) > 1000:
        raise ExprError("exponent too large")
    try:
        r = a ** b
    except (OverflowError, ZeroDivisionError):
        return math.inf
    if isinstance(r, complex):
        return math.nan
    return r


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _step(x: float) -> float:
    return 1.0 if x >= 0 else 0.0


def _safe_log(x: float, base: float = math.e) -> float:
    if x <= 0:
        return -math.inf if x == 0 else math.nan
    return math.log(x, base)


def _safe_sqrt(x: float) -> float:
    return math.sqrt(x) if x >= 0 else math.nan


FUNCS: Dict[str, Callable[..., Any]] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "exp": math.exp, "log": _safe_log, "ln": _safe_log,
    "log10": lambda x: _safe_log(x, 10), "log2": lambda x: _safe_log(x, 2),
    "sqrt": _safe_sqrt, "abs": abs, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "round": round,
    "sign": _sign, "clip": _clip, "step": _step, "heaviside": _step,
    "pow": _safe_pow,
}
CONSTS: Dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}

_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: _safe_pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_CMP = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call,
    ast.Compare, ast.BoolOp, ast.IfExp, ast.Load, ast.And, ast.Or, ast.Not,
    ast.USub, ast.UAdd,
) + tuple(_BIN) + tuple(_CMP)


def parse(src: str) -> ast.Expression:
    if not isinstance(src, str) or not src.strip():
        raise ExprError("empty expression")
    return _parse_cached(src.strip())


@functools.lru_cache(maxsize=4096)
def _parse_cached(src: str) -> ast.Expression:
    try:
        tree = ast.parse(src.strip(), mode="eval")
    except SyntaxError as exc:  # pragma: no cover - message passthrough
        raise ExprError(f"syntax error in {src!r}: {exc.msg}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExprError(f"disallowed syntax {type(node).__name__} in {src!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
                raise ExprError(f"unknown function in {src!r}")
            if node.keywords:
                raise ExprError("keyword arguments are not allowed")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise ExprError(f"only numeric literals are allowed in {src!r}")
    return tree


def free_names(src: str) -> List[str]:
    tree = parse(src)
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in FUNCS and node.id not in CONSTS:
            names.add(node.id)
    return sorted(names)


def evaluate(src: str | ast.Expression, env: Dict[str, Any]) -> Any:
    tree = parse(src) if isinstance(src, str) else src
    return _eval(tree.body, env)


def _eval(node: ast.AST, env: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            val = env[node.id]
            if val is None:
                raise ExprError(f"variable {node.id!r} has no value")
            return val
        if node.id in CONSTS:
            return CONSTS[node.id]
        raise ExprError(f"unknown name {node.id!r}")
    if isinstance(node, ast.BinOp):
        return _BIN[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.Not):
            return not v
        return v
    if isinstance(node, ast.Call):
        args = [_eval(a, env) for a in node.args]
        return FUNCS[node.func.id](*args)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        vals = (_eval(v, env) for v in node.values)
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    raise ExprError(f"unsupported node {type(node).__name__}")


def partial(src: str, env: Dict[str, Any], name: str, rel: float = 1e-5) -> float:
    """Central-difference partial derivative d(expr)/d(name) at env."""
    x0 = float(env[name])
    h = abs(x0) * rel if x0 != 0 else rel
    up = dict(env, **{name: x0 + h})
    dn = dict(env, **{name: x0 - h})
    try:
        return (float(evaluate(src, up)) - float(evaluate(src, dn))) / (2 * h)
    except (ArithmeticError, ValueError, TypeError):
        return math.nan


def constant_value(node: ast.AST) -> float | None:
    """Numeric value of a constant sub-expression (literals, pi/e, + - * / ** of those), else None.

    Used for exponents: ``R**(1/3)`` and ``x**(-0.5)`` are dimensionally checkable."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in CONSTS:
        return float(CONSTS[node.id])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = constant_value(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = constant_value(node.left), constant_value(node.right)
        if a is None or b is None:
            return None
        try:
            return float(_BIN[type(node.op)](a, b))
        except (ArithmeticError, ValueError, OverflowError):
            return None
    return None


def names_in(exprs: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for e in exprs:
        out.update(free_names(e))
    return out
