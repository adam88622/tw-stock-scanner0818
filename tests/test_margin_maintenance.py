"""純函式測試（移植自 0718，指向 scanners.margin_maintenance，無網路）。"""

from __future__ import annotations

import math

import pytest

from scanners.margin_maintenance import (
    CodeError,
    classify_warning,
    compute_maintenance_ratio,
    roll_cost,
    trim_recent_continuous,
    validate_stock_code,
)


class TestComputeMaintenanceRatio:
    def test_normal_manual_calc(self):
        assert compute_maintenance_ratio(120.0, 100.0, 0.6) == 200.0

    def test_normal_manual_calc_2(self):
        result = compute_maintenance_ratio(166.67, 100.0, 0.6)
        assert result == round(166.67 / 60.0 * 100, 2)
        assert result == 277.78

    def test_default_rate_matches_config(self):
        assert compute_maintenance_ratio(120.0, 100.0) == 200.0

    def test_n_avg_zero_returns_none(self):
        assert compute_maintenance_ratio(100.0, 0.0) is None

    def test_n_avg_negative_returns_none(self):
        assert compute_maintenance_ratio(100.0, -5.0) is None

    def test_n_avg_none_returns_none(self):
        assert compute_maintenance_ratio(100.0, None) is None

    def test_price_none_returns_none(self):
        assert compute_maintenance_ratio(None, 100.0) is None

    def test_both_none_returns_none(self):
        assert compute_maintenance_ratio(None, None) is None

    def test_result_never_inf_or_nan(self):
        for price, n_avg in [(None, 0.0), (100.0, None), (None, None), (100.0, 0.0)]:
            result = compute_maintenance_ratio(price, n_avg)
            assert result is None
            assert result != float("inf")
            if result is not None:
                assert not math.isnan(result)


class TestClassifyWarning:
    def test_below_danger_threshold(self):
        assert classify_warning(129.9) == "danger"

    def test_at_danger_boundary_is_warn(self):
        assert classify_warning(130.0) == "warn"

    def test_just_below_safe_boundary_is_warn(self):
        assert classify_warning(166.66) == "warn"

    def test_at_safe_boundary_is_safe(self):
        assert classify_warning(166.67) == "safe"

    def test_well_above_safe_is_safe(self):
        assert classify_warning(200.0) == "safe"

    def test_none_is_na(self):
        assert classify_warning(None) == "na"

    def test_zero_is_danger(self):
        assert classify_warning(0.0) == "danger"

    def test_negative_is_danger(self):
        assert classify_warning(-10.0) == "danger"


class TestTrimRecentContinuous:
    def test_no_breakpoint_returns_unchanged(self):
        closes = [100.0, 101.0, 99.5, 102.0, 103.0]
        trimmed, was_trimmed = trim_recent_continuous(closes)
        assert trimmed == closes
        assert was_trimmed is False

    def test_split_breakpoint_keeps_only_segment_after_event(self):
        closes = [306.0, 300.0, 302.0, 298.0, 12.2, 12.1, 12.15]
        trimmed, was_trimmed = trim_recent_continuous(closes)
        assert was_trimmed is True
        assert trimmed == [12.2, 12.1, 12.15]

    def test_split_breakpoint_reverse_case_price_jump_up(self):
        closes = [10.0, 10.1, 9.9, 100.0, 101.0]
        trimmed, was_trimmed = trim_recent_continuous(closes)
        assert was_trimmed is True
        assert trimmed == [100.0, 101.0]

    def test_empty_sequence(self):
        trimmed, was_trimmed = trim_recent_continuous([])
        assert trimmed == []
        assert was_trimmed is False

    def test_single_element_sequence(self):
        trimmed, was_trimmed = trim_recent_continuous([42.0])
        assert trimmed == [42.0]
        assert was_trimmed is False

    def test_two_elements_no_breakpoint(self):
        trimmed, was_trimmed = trim_recent_continuous([100.0, 101.0])
        assert trimmed == [100.0, 101.0]
        assert was_trimmed is False

    def test_zero_value_triggers_protective_trim(self):
        closes = [50.0, 0.0, 100.0]
        trimmed, was_trimmed = trim_recent_continuous(closes)
        assert was_trimmed is True
        assert trimmed == [100.0]

    def test_negative_value_triggers_protective_trim(self):
        closes = [50.0, -1.0, 100.0]
        trimmed, was_trimmed = trim_recent_continuous(closes)
        assert was_trimmed is True
        assert trimmed == [100.0]

    def test_custom_threshold_default_trims_but_relaxed_does_not(self):
        closes = [100.0, 140.0]
        trimmed_default, was_trimmed_default = trim_recent_continuous(closes)
        assert was_trimmed_default is True
        assert trimmed_default == [140.0]

        trimmed_relaxed, was_trimmed_relaxed = trim_recent_continuous(
            closes, max_step=0.5)
        assert was_trimmed_relaxed is False
        assert trimmed_relaxed == closes


class TestRollCost:
    def test_verified_2327_case(self):
        # 反向工程對答案：2327 於 2026-06-30 由 886.7276 滾到 912.84
        got = roll_cost(886.7276, buy=5572, balance=54043, close=1140.0)
        assert round(got, 2) == 912.84

    def test_balance_zero_returns_prev(self):
        assert roll_cost(100.0, buy=10, balance=0, close=50.0) == 100.0

    def test_zero_buy_unchanged(self):
        assert roll_cost(100.0, buy=0, balance=5000, close=50.0) == 100.0

    def test_buy_equals_balance_becomes_close(self):
        assert roll_cost(100.0, buy=5000, balance=5000, close=50.0) == 50.0

    def test_buy_exceeds_balance_clamped(self):
        assert roll_cost(100.0, buy=9999, balance=5000, close=50.0) == 50.0

    def test_moves_toward_close(self):
        got = roll_cost(100.0, buy=500, balance=5000, close=50.0)  # w=0.1
        assert got == pytest.approx(95.0)


class TestValidateStockCode:
    def test_normal_four_digit_code(self):
        assert validate_stock_code("2330") == "2330"

    def test_normalizes_surrounding_whitespace(self):
        assert validate_stock_code("  2330  ") == "2330"

    def test_warrant_prefix_91_raises(self):
        with pytest.raises(CodeError) as exc_info:
            validate_stock_code("9100")
        assert exc_info.value.code == "9100"

    def test_warrant_prefix_91_any_suffix_raises(self):
        with pytest.raises(CodeError):
            validate_stock_code("9199")

    def test_three_digit_code_raises(self):
        with pytest.raises(CodeError):
            validate_stock_code("233")

    def test_five_digit_code_raises(self):
        with pytest.raises(CodeError):
            validate_stock_code("23300")

    def test_non_numeric_raises(self):
        with pytest.raises(CodeError):
            validate_stock_code("abcd")

    def test_empty_string_raises(self):
        with pytest.raises(CodeError):
            validate_stock_code("")

    def test_whitespace_only_raises(self):
        with pytest.raises(CodeError):
            validate_stock_code("   ")

    def test_mixed_alnum_raises(self):
        with pytest.raises(CodeError):
            validate_stock_code("23a0")

    def test_error_carries_reason_and_original_code(self):
        with pytest.raises(CodeError) as exc_info:
            validate_stock_code("abc")
        assert exc_info.value.code == "abc"
        assert exc_info.value.reason
