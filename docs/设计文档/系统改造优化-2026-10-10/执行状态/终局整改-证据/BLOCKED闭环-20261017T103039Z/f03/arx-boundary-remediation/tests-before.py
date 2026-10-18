"""ARX regression rows must preserve their mathematical lags and float64 layout.

These semantic guards also pass on the previous scalar implementation. The
performance counterexample is the unchanged 30-second test in real Linux CI.
"""

from unittest.mock import patch

import numpy as np
import pytest

from app.services.tuning_identification.arx import identify_arx


@pytest.mark.parametrize(
    ("dtype", "stride", "na", "nb", "delay"),
    [
        (np.float32, 1, 1, 1, 0),
        (np.float64, 2, 2, 1, 5),
        (np.int64, 2, 1, 3, 2),
        (list, 1, 4, 2, 7),
        (np.float64, 1, 0, 1, 0),
        (np.float64, 2, 0, 0, 0),
    ],
)
def test_arx_rows_match_lag_equations(dtype, stride, na, nb, delay):
    rng = np.random.default_rng(20261017)
    source_u = rng.normal(size=100) * 8
    source_y = rng.normal(size=100) * 8
    if dtype is list:
        u = source_u[:40].tolist()
        y = source_y[:40].tolist()
    else:
        u = source_u.astype(dtype)[::stride][:40]
        y = source_y.astype(dtype)[::stride][:40]
    first = max(na, nb + delay)
    expected_matrix = np.array(
        [
            [float(-y[t - j - 1]) for j in range(na)] + [float(u[t - delay - j]) for j in range(nb)]
            for t in range(first, len(y))
        ],
        dtype=np.float64,
    )
    expected_response = np.array([float(y[t]) for t in range(first, len(y))])
    solve = np.linalg.lstsq
    captured = []

    def record(matrix, response, **kwargs):
        captured.append((matrix.copy(), response.copy(), matrix.flags.c_contiguous))
        return solve(matrix, response, **kwargs)

    with patch("numpy.linalg.lstsq", side_effect=record):
        result = identify_arx(u, y, delay, na=na, nb=nb)
    assert len(captured) == 1
    matrix, response, contiguous = captured[0]
    assert matrix.dtype == response.dtype == np.dtype("float64")
    assert contiguous
    assert matrix.shape == expected_matrix.shape
    assert matrix.tobytes() == expected_matrix.tobytes()
    assert response.tobytes() == expected_response.tobytes()
    expected_theta = solve(expected_matrix, expected_response, rcond=None)[0]
    np.testing.assert_array_equal(result.a_coeffs, expected_theta[:na])
    np.testing.assert_array_equal(result.b_coeffs, expected_theta[na:])
    assert result.n_samples == len(y) - first
    assert result.d == delay


@pytest.mark.parametrize("delay", [0, 1, 4, 17])
def test_arx_recovers_known_delay_equation(delay):
    rng = np.random.default_rng(20261017)
    u = rng.normal(size=240)
    y = np.zeros(len(u))
    for t in range(max(1, delay), len(y)):
        y[t] = 0.65 * y[t - 1] + 0.27 * u[t - delay]
    result = identify_arx(u, y, delay)
    np.testing.assert_allclose(result.a_coeffs, [-0.65], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(result.b_coeffs, [0.27], rtol=1e-12, atol=1e-12)
    assert result.residual_var < 1e-28
    assert result.r_squared == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("length", [0, 14])
def test_arx_insufficient_boundary_is_preserved(length):
    with pytest.raises(ValueError, match=f"ARX 数据不足：{length} 点，需 ≥ 15"):
        identify_arx(np.zeros(length), np.zeros(length), 3, na=2, nb=2)
