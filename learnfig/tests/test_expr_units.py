import math

import pytest

from learnfig import expr as E
from learnfig import units as U


def test_evaluate_and_free_names():
    assert E.free_names("v0**2*sin(2*theta)/g") == ["g", "theta", "v0"]
    val = E.evaluate("v0**2*sin(2*theta)/g", {"v0": 10, "theta": math.pi / 4, "g": 10})
    assert val == pytest.approx(10.0)
    assert E.evaluate("x <= 0 and t > 0.1", {"x": -1, "t": 1}) is True


@pytest.mark.parametrize("bad", ["__import__('os')", "x.real", "a[0]", "lambda: 1", "open('f')", "'s'"])
def test_rejects_unsafe_syntax(bad):
    with pytest.raises(E.ExprError):
        E.parse(bad)


def test_partial_derivative():
    assert E.partial("x**2", {"x": 3.0}, "x") == pytest.approx(6.0, rel=1e-4)


def test_unit_parsing():
    assert U.parse_unit("m/s^2").dims_str() == "m·s^-2"
    assert U.parse_unit("kg*m/s^2").same_dims(U.parse_unit("N"))
    assert U.parse_unit("km").factor == pytest.approx(1000)
    assert U.parse_unit("kg/(m*s^2)").same_dims(U.parse_unit("Pa"))
    assert U.parse_unit("1").dimensionless and U.parse_unit("").dimensionless
    r = U.parse_unit("rabbits/yr")
    assert r.custom and "[rabbits]" in r.dims_str()


def test_dimensional_consistency():
    env = {"m": U.parse_unit("kg"), "g": U.parse_unit("m/s^2"), "y": U.parse_unit("m"), "v": U.parse_unit("m/s")}
    assert U.unit_of_expression("0.5*m*v**2 + m*g*y", env).same_dims(U.parse_unit("J"))
    with pytest.raises(U.UnitError):
        U.unit_of_expression("v + y", env)
    with pytest.raises(U.UnitError):
        U.unit_of_expression("sin(y)", env)
    notes = []
    u = U.unit_of_expression("y <= 0", env, notes)
    assert u.dimensionless and notes == []
    U.unit_of_expression("y - 100", env, notes)
    assert notes and "literal 100" in notes[0]


def test_literals_do_not_scale_units():
    env = {"a": U.parse_unit("m"), "GM": U.parse_unit("m^3/s^2")}
    u = U.unit_of_expression("2*pi*sqrt(a**3/GM)", env)
    assert u.same_dims(U.parse_unit("s")) and u.factor == pytest.approx(1.0)


def test_constant_exponents_fold():
    env = {"m": U.parse_unit("kg"), "rho": U.parse_unit("kg/m^3")}
    assert U.unit_of_expression("(3*m/(4*pi*rho))**(1/3)", env).same_dims(U.parse_unit("m"))
    assert U.unit_of_expression("(m/rho)**(-1/3)", env).same_dims(U.parse_unit("1/m"))
