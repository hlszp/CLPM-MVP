"""G3 ARMAX 收敛资格排序键单测（2026-10-17 非作者 REWORK 补充）."""

from app.services.tuning_identification.pipeline import (
    _candidate_sort_key,
    _is_model_converged,
)
from app.services.tuning_identification.types import (
    AlgorithmConfidenceLevel,
    CandidateModel,
    IdentifyMethod,
    ModelEvidence,
    ModelParams,
    ModelType,
)


def _make_candidate(
    confidence: AlgorithmConfidenceLevel,
    fitting_score: float,
    residual_passed: bool = True,
    reason_codes: list | None = None,
) -> CandidateModel:
    return CandidateModel(
        params=ModelParams(model_type=ModelType.FOPDT, K=1.0, tau=10.0, theta=2.0),
        fitting_score=fitting_score,
        confidence=confidence,
        identify_method=IdentifyMethod.HISTORICAL_ARMAX,
        residual_test_passed=residual_passed,
        excitation_score=80.0,
        evidence=ModelEvidence(reason_codes=reason_codes or []),
    )


class TestIsModelConverged:
    def test_no_armax_not_converged_code_returns_true(self):
        c = _make_candidate(AlgorithmConfidenceLevel.E, 6.91, reason_codes=[])
        assert _is_model_converged(c) is True

    def test_armax_not_converged_code_returns_false(self):
        c = _make_candidate(AlgorithmConfidenceLevel.E, 10.25, reason_codes=["ARMAX_NOT_CONVERGED"])
        assert _is_model_converged(c) is False

    def test_other_codes_do_not_affect_convergence(self):
        c = _make_candidate(
            AlgorithmConfidenceLevel.E, 8.0, reason_codes=["SIGN_MISMATCH", "LOW_SNR"]
        )
        assert _is_model_converged(c) is True


class TestCandidateSortKeyConvergencePreference:
    def test_converged_beats_nonconverged_same_confidence(self):
        """同级可信度：收敛模型胜出，即使 fitting_score 更低."""
        converged = _make_candidate(AlgorithmConfidenceLevel.E, 6.91)
        nonconverged = _make_candidate(
            AlgorithmConfidenceLevel.E, 10.25, reason_codes=["ARMAX_NOT_CONVERGED"]
        )
        assert _candidate_sort_key(converged) > _candidate_sort_key(nonconverged)

    def test_nonconverged_higher_confidence_still_wins(self):
        """跨级缺口（已知边界）：未收敛@D 仍胜 收敛@E——文档显式登记."""
        nonconverged_d = _make_candidate(
            AlgorithmConfidenceLevel.D, 10.25, reason_codes=["ARMAX_NOT_CONVERGED"]
        )
        converged_e = _make_candidate(AlgorithmConfidenceLevel.E, 6.91)
        assert _candidate_sort_key(nonconverged_d) > _candidate_sort_key(converged_e)

    def test_both_converged_fitting_score_decides(self):
        """两模型均收敛：fitting_score 决胜."""
        a = _make_candidate(AlgorithmConfidenceLevel.E, 9.0)
        b = _make_candidate(AlgorithmConfidenceLevel.E, 6.0)
        assert _candidate_sort_key(a) > _candidate_sort_key(b)

    def test_both_nonconverged_fitting_score_decides(self):
        """两模型均未收敛：fitting_score 决胜."""
        a = _make_candidate(AlgorithmConfidenceLevel.E, 9.0, reason_codes=["ARMAX_NOT_CONVERGED"])
        b = _make_candidate(AlgorithmConfidenceLevel.E, 6.0, reason_codes=["ARMAX_NOT_CONVERGED"])
        assert _candidate_sort_key(a) > _candidate_sort_key(b)
