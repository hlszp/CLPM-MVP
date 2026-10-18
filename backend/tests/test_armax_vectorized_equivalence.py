"""ARMAX lag matrices preserve the scalar equation and zero-padding contract.

These guards pass on both implementations. Performance red evidence is the
unchanged 30-second annotated test in Linux CI, not a mathematical bug.
"""

from unittest.mock import patch

import numpy as np
import pytest

from app.services.tuning_identification.armax import identify_armax
from app.services.tuning_identification.arx import identify_arx


class _ScalarReadOffset(np.ndarray):
    def __getitem__(self, index):
        value = super().__getitem__(index)
        return value + 3 if isinstance(index, int) else value


@pytest.mark.parametrize(
    ("kind", "na", "nb", "nc", "delay", "stride"),
    [
        ("float32", 1, 1, 1, 0, 1),
        ("float64", 2, 2, 2, 3, 2),
        ("float64", 0, 1, 0, 0, 1),
        ("float64", 1, 0, 2, 0, 1),
        ("float64", 0, 0, 0, 0, 1),
        ("float64", 1, 1, 40, 1, 1),
        ("int64", 1, 1, 1, 0, 1),
        ("list", 2, 1, 2, 2, 1),
        ("tuple", 1, 2, 1, 2, 1),
        ("large-list", 1, 1, 1, 0, 1),
        ("subclass", 1, 1, 1, 0, 1),
        ("signed-zero", 1, 1, 1, 0, 1),
    ],
)
def test_armax_matrices_match_one_iteration_equations(kind, na, nb, nc, delay, stride):
    rng = np.random.default_rng(20261017)
    u = rng.normal(size=80)[::stride][:32]
    y = rng.normal(size=80)[::stride][:32] * 4
    if kind in {"list", "tuple", "large-list"}:
        y = y.tolist()
        u = u.tolist()
        if kind == "large-list":
            y[20] = -(2**63)
        elif kind == "tuple":
            y, u = tuple(y), tuple(u)
    elif kind == "subclass":
        y = y.view(_ScalarReadOffset)
    elif kind == "signed-zero":
        y = np.resize(np.array([-0.0, 0.0]), len(y))
    else:
        u, y = u.astype(kind), y.astype(kind)
    with np.errstate(over="ignore"):
        initial = identify_arx(u, y, delay, na=na, nb=nb)
        first = max(na, nb + delay)
        rows = len(y) - first
        equation_error = np.zeros(rows)
        for row, t in enumerate(range(first, len(y))):
            equation_error[row] = y[t]
            for j in range(na):
                equation_error[row] += initial.a_coeffs[j] * y[t - 1 - j]
            for j in range(nb):
                equation_error[row] -= initial.b_coeffs[j] * u[t - delay - j]
        # Initial C=0, so the first innovation is exactly the equation error.
        expected_c = np.zeros((rows, nc))
        expected_phi = np.zeros((rows, na + nc + nb))
        for row, t in enumerate(range(first, len(y))):
            for j in range(nc):
                expected_c[row, j] = equation_error[row - 1 - j] if row > j else 0.0
            for j in range(na):
                expected_phi[row, j] = -y[t - 1 - j]
            expected_phi[row, na : na + nc] = expected_c[row]
            for j in range(nb):
                expected_phi[row, na + nc + j] = u[t - delay - j]
        expected_y = np.array([float(y[t]) for t in range(first, len(y))])
    solve = np.linalg.lstsq
    captured = []

    def record(matrix, response, **kwargs):
        captured.append((matrix.copy(), response.copy(), matrix.flags.c_contiguous))
        return solve(matrix, response, **kwargs)

    with np.errstate(over="ignore"), patch("numpy.linalg.lstsq", side_effect=record):
        result = identify_armax(u, y, delay, na=na, nb=nb, nc=nc, max_iter=1)
    assert len(captured) == (3 if nc else 2)
    if nc:
        assert captured[1][0].tobytes() == expected_c.tobytes()
        assert captured[1][1].tobytes() == equation_error.tobytes()
    matrix, response, contiguous = captured[-1]
    assert contiguous and matrix.dtype == response.dtype == np.dtype("float64")
    assert matrix.tobytes() == expected_phi.tobytes()
    assert response.tobytes() == expected_y.tobytes()
    expected_theta = solve(expected_phi, expected_y, rcond=None)[0]
    np.testing.assert_array_equal(result.a_coeffs, expected_theta[:na])
    np.testing.assert_array_equal(result.b_coeffs, expected_theta[na + nc :])
    assert result.iterations == 1


def test_armax_short_input_preserves_rejection():
    with pytest.raises(IndexError, match="out of bounds"):
        identify_armax(np.arange(12.0), np.arange(40.0), 0)
