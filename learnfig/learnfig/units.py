"""Dimensional analysis for topic specs.

A ``Unit`` is a scale factor plus a vector of exponents over base dimensions.
SI base dimensions are built in; any unknown token (``rabbits``, ``USD``,
``drones``) becomes its own custom dimension, so ecology or economics specs get
the same consistency checks as physics.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from . import expr as E


class UnitError(ValueError):
    pass


Dims = Tuple[Tuple[str, Fraction], ...]


def _norm(d: Dict[str, Fraction]) -> Dims:
    return tuple(sorted((k, Fraction(v)) for k, v in d.items() if v != 0))


@dataclass(frozen=True)
class Unit:
    factor: float = 1.0
    dims: Dims = ()
    wild: bool = False  # bare numeric literal: adopts the other operand's dims
    custom: bool = False  # involves a non-SI custom dimension
    value: Optional[float] = None  # literal value when wild (0 is dimensionally neutral)

    @property
    def dimensionless(self) -> bool:
        return not self.dims

    def same_dims(self, other: "Unit") -> bool:
        return self.dims == other.dims

    def __mul__(self, other: "Unit") -> "Unit":
        d = dict(self.dims)
        for k, v in other.dims:
            d[k] = d.get(k, Fraction(0)) + v
        return Unit(self.factor * other.factor, _norm(d), self.wild and other.wild,
                    self.custom or other.custom)

    def __truediv__(self, other: "Unit") -> "Unit":
        return self * other ** Fraction(-1)

    def __pow__(self, p) -> "Unit":
        p = Fraction(p).limit_denominator(1000)
        return Unit(self.factor ** float(p), _norm({k: v * p for k, v in self.dims}), self.wild,
                    self.custom)

    def dims_str(self) -> str:
        if not self.dims:
            return "1"
        parts = []
        for k, v in self.dims:
            if v == 1:
                parts.append(k)
            else:
                exp = str(v) if v.denominator == 1 else f"({v})"
                parts.append(f"{k}^{exp}")
        return "·".join(parts)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return self.dims_str()


DIMLESS = Unit()

_BASE = {"m": "m", "kg": "kg", "s": "s", "A": "A", "K": "K", "mol": "mol", "cd": "cd"}

_DERIVED: Dict[str, Tuple[float, Dict[str, int]]] = {
    "g": (1e-3, {"kg": 1}),
    "t": (1e3, {"kg": 1}),
    "N": (1.0, {"kg": 1, "m": 1, "s": -2}),
    "J": (1.0, {"kg": 1, "m": 2, "s": -2}),
    "W": (1.0, {"kg": 1, "m": 2, "s": -3}),
    "Pa": (1.0, {"kg": 1, "m": -1, "s": -2}),
    "bar": (1e5, {"kg": 1, "m": -1, "s": -2}),
    "atm": (101325.0, {"kg": 1, "m": -1, "s": -2}),
    "Hz": (1.0, {"s": -1}),
    "C": (1.0, {"A": 1, "s": 1}),
    "V": (1.0, {"kg": 1, "m": 2, "s": -3, "A": -1}),
    "ohm": (1.0, {"kg": 1, "m": 2, "s": -3, "A": -2}),
    "Ω": (1.0, {"kg": 1, "m": 2, "s": -3, "A": -2}),
    "F": (1.0, {"kg": -1, "m": -2, "s": 4, "A": 2}),
    "T": (1.0, {"kg": 1, "s": -2, "A": -1}),
    "Wb": (1.0, {"kg": 1, "m": 2, "s": -2, "A": -1}),
    "L": (1e-3, {"m": 3}),
    "ha": (1e4, {"m": 2}),
    "min": (60.0, {"s": 1}),
    "h": (3600.0, {"s": 1}),
    "hr": (3600.0, {"s": 1}),
    "day": (86400.0, {"s": 1}),
    "d": (86400.0, {"s": 1}),
    "yr": (3.15576e7, {"s": 1}),
    "year": (3.15576e7, {"s": 1}),
    "eV": (1.602176634e-19, {"kg": 1, "m": 2, "s": -2}),
    "cal": (4.184, {"kg": 1, "m": 2, "s": -2}),
    "rad": (1.0, {}),
    "sr": (1.0, {}),
    "deg": (math.pi / 180.0, {}),
    "°": (math.pi / 180.0, {}),
    "%": (0.01, {}),
    "percent": (0.01, {}),
    "ppm": (1e-6, {}),
    "°C": (1.0, {"K": 1}),
    "degC": (1.0, {"K": 1}),
    "1": (1.0, {}),
    "dimensionless": (1.0, {}),
    "none": (1.0, {}),
    "unitless": (1.0, {}),
}

_PREFIXES = {
    "Y": 1e24, "Z": 1e21, "E": 1e18, "P": 1e15, "T": 1e12, "G": 1e9, "M": 1e6,
    "k": 1e3, "h": 1e2, "da": 1e1, "d": 1e-1, "c": 1e-2, "m": 1e-3, "u": 1e-6,
    "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "a": 1e-18,
}

_ATOM_RE = re.compile(r"[A-Za-z°µμΩ%][A-Za-z_°µμΩ%]*")
_TOKEN_RE = re.compile(r"\s*(\*\*|\^|[*/()·]|-?\d+(?:\.\d+)?|[A-Za-z°µμΩ%][A-Za-z_°µμΩ%]*)")


def _atom(name: str) -> Unit:
    if name in _BASE:
        return Unit(1.0, _norm({name: Fraction(1)}))
    if name in _DERIVED:
        f, d = _DERIVED[name]
        return Unit(f, _norm({k: Fraction(v) for k, v in d.items()}))
    for pre, pf in sorted(_PREFIXES.items(), key=lambda kv: -len(kv[0])):
        if name.startswith(pre) and len(name) > len(pre):
            rest = name[len(pre):]
            if rest in _BASE or rest in _DERIVED:
                base = _atom(rest)
                return Unit(base.factor * pf, base.dims)
    # custom dimension (counts of things, currency, ...)
    return Unit(1.0, _norm({f"[{name}]": Fraction(1)}), custom=True)


class _Parser:
    def __init__(self, text: str):
        self.tokens: List[str] = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            m = _TOKEN_RE.match(text, pos)
            if not m:
                raise UnitError(f"cannot parse unit {text!r}")
            self.tokens.append(m.group(1))
            pos = m.end()
        self.i = 0

    def peek(self) -> Optional[str]:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> str:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def parse(self) -> Unit:
        u = self.expr()
        if self.peek() is not None:
            raise UnitError(f"unexpected token {self.peek()!r}")
        return u

    def expr(self) -> Unit:
        u = self.term()
        while True:
            tok = self.peek()
            if tok in ("*", "·"):
                self.take()
                u = u * self.term()
            elif tok == "/":
                self.take()
                u = u / self.term()
            elif tok is not None and tok not in (")", "^", "**") and not _is_number(tok):
                u = u * self.term()  # implicit multiplication: "kg m"
            else:
                return u

    def term(self) -> Unit:
        tok = self.peek()
        if tok is None:
            raise UnitError("unexpected end of unit")
        if tok == "(":
            self.take()
            u = self.expr()
            if self.peek() != ")":
                raise UnitError("missing ')' in unit")
            self.take()
        elif _is_number(tok):
            self.take()
            u = Unit(float(tok), ())
        else:
            self.take()
            u = _atom(tok)
            # trailing digits glued to the atom: m2, s-2
        nxt = self.peek()
        if nxt in ("^", "**"):
            self.take()
            p = self.take()
            if not _is_number(p):
                raise UnitError(f"bad exponent {p!r}")
            u = u ** Fraction(p)
        elif nxt is not None and _is_number(nxt) and self.i > 0 and self.tokens[self.i - 1] not in ("*", "/", "("):
            # "s-2" or "m2" style
            self.take()
            u = u ** Fraction(nxt)
        return u


def _is_number(tok: Optional[str]) -> bool:
    if tok is None:
        return False
    try:
        float(tok)
        return True
    except ValueError:
        return False


def parse_unit(text: Optional[str]) -> Unit:
    if text is None:
        return DIMLESS
    text = str(text).strip()
    if text in ("", "1", "-", "dimensionless", "none", "unitless"):
        return DIMLESS
    return _Parser(text).parse()


# --------------------------------------------------------------------------
# dimensional evaluation of expressions
# --------------------------------------------------------------------------

_DIMLESS_FUNCS = {"sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh",
                  "exp", "log", "ln", "log10", "log2", "step", "heaviside", "sign"}
_SAME_FUNCS = {"abs", "floor", "ceil", "round"}


def unit_of_expression(src: str, env: Dict[str, Unit], notes: Optional[List[str]] = None) -> Unit:
    """Dimensions of ``src`` given the dimensions of its free names.

    Raises UnitError for inconsistent sums/comparisons or dimensioned function
    arguments.  Bare numeric literals are "wild" and adopt the dimensions of the
    other operand in + - and comparisons; a non-zero literal used that way is
    recorded in ``notes`` because it hides an implicit unit.
    """
    tree = E.parse(src)
    notes = notes if notes is not None else []
    return _dims(tree.body, env, notes, src)


def _combine_additive(a: Unit, b: Unit, node: ast.AST, src: str, notes: List[str], op: str) -> Unit:
    if a.wild and b.wild:
        return Unit(1.0, (), wild=True, value=(a.value if a.value is not None else b.value))
    if a.wild or b.wild:
        lit = a if a.wild else b
        other = b if a.wild else a
        if lit.value not in (None, 0.0) and not other.dimensionless:
            notes.append(f"{src!r}: literal {lit.value:g} is used to {op} a quantity in {other.dims_str()}; "
                         f"the literal silently carries those units")
        return Unit(other.factor, other.dims, custom=other.custom)
    if not a.same_dims(b):
        raise UnitError(f"{src!r}: cannot {op} {a.dims_str()} and {b.dims_str()}")
    return Unit(a.factor, a.dims, custom=a.custom or b.custom)


def _dims(node: ast.AST, env: Dict[str, Unit], notes: List[str], src: str) -> Unit:
    if isinstance(node, ast.Constant):
        val = float(node.value) if isinstance(node.value, (int, float)) else 1.0
        return Unit(1.0, (), wild=True, value=val)
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in E.CONSTS:
            return Unit(1.0, (), wild=True)
        raise UnitError(f"{src!r}: unknown name {node.id!r}")
    if isinstance(node, ast.UnaryOp):
        return _dims(node.operand, env, notes, src)
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            base = _dims(node.left, env, notes, src)
            p = E.constant_value(node.right)
            if p is None:
                exp_u = _dims(node.right, env, notes, src)
                if not exp_u.dimensionless:
                    raise UnitError(f"{src!r}: exponent must be dimensionless")
                if base.dimensionless or base.wild:
                    return Unit(1.0, (), wild=base.wild)
                raise UnitError(f"{src!r}: non-constant exponent of a quantity in {base.dims_str()}")
            if base.wild:
                return Unit(1.0, (), wild=True)
            return base ** Fraction(p).limit_denominator(1000)
        a = _dims(node.left, env, notes, src)
        b = _dims(node.right, env, notes, src)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return _combine_additive(a, b, node, src, notes, "add" if isinstance(node.op, ast.Add) else "subtract")
        if isinstance(node.op, (ast.Mult,)):
            r = a * b
            return Unit(r.factor, r.dims, wild=a.wild and b.wild, custom=r.custom)
        if isinstance(node.op, (ast.Div, ast.FloorDiv)):
            r = a / b
            return Unit(r.factor, r.dims, wild=a.wild and b.wild, custom=r.custom)
        if isinstance(node.op, ast.Mod):
            return _combine_additive(a, b, node, src, notes, "take modulo of")
    if isinstance(node, ast.Call):
        name = node.func.id
        args = [_dims(a, env, notes, src) for a in node.args]
        if name in _DIMLESS_FUNCS:
            for a in args:
                if not (a.dimensionless or a.wild):
                    raise UnitError(f"{src!r}: {name}() needs a dimensionless argument, got {a.dims_str()}")
            return DIMLESS
        if name == "sqrt":
            return args[0] ** Fraction(1, 2) if not args[0].wild else Unit(1.0, (), wild=True)
        if name == "pow":
            p = E.constant_value(node.args[1])
            if p is None:
                raise UnitError(f"{src!r}: pow() exponent must be a constant")
            return args[0] ** Fraction(p).limit_denominator(1000)
        if name in _SAME_FUNCS:
            return args[0]
        if name in ("min", "max", "clip"):
            out = args[0]
            for a in args[1:]:
                out = _combine_additive(out, a, node, src, notes, "compare")
            return out
        if name == "atan2":
            _combine_additive(args[0], args[1], node, src, notes, "compare")
            return DIMLESS
        raise UnitError(f"{src!r}: unknown function {name}")
    if isinstance(node, ast.Compare):
        left = _dims(node.left, env, notes, src)
        for comp in node.comparators:
            right = _dims(comp, env, notes, src)
            _combine_additive(left, right, node, src, notes, "compare")
            left = right
        return DIMLESS
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            _dims(v, env, notes, src)
        return DIMLESS
    if isinstance(node, ast.IfExp):
        _dims(node.test, env, notes, src)
        a = _dims(node.body, env, notes, src)
        b = _dims(node.orelse, env, notes, src)
        return _combine_additive(a, b, node, src, notes, "choose between")
    raise UnitError(f"{src!r}: unsupported syntax")


def pretty_unit(text: Optional[str]) -> str:
    """Human-friendly unit label for figures (``m/s^2`` -> ``m/s²``)."""
    if text is None or str(text).strip() in ("", "1", "none", "dimensionless"):
        return ""
    s = str(text)
    sup = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
    s = re.sub(r"\*\*|\^", "", re.sub(r"(\^|\*\*)(-?\d+)", lambda m: m.group(2).translate(sup), s))
    return s.replace("*", "·").replace("deg", "°")
