"""
Coercion guards for the diesel site-history read path.

The fill-up log is dirty in the field: litres and amounts arrive as bare
numbers, Rand-prefixed strings, comma decimals, and "N/A". A history spans
years of that, so a total must never be able to 500 the request. The values
exercised here are taken from a real customer fill-up log.
"""

from app.services.report_support import coerce_diesel_gen_no, coerce_diesel_number


class TestCoerceDieselNumber:
    def test_accepts_plain_numbers(self) -> None:
        assert coerce_diesel_number(22) == 22.0
        assert coerce_diesel_number(22.51) == 22.51
        assert coerce_diesel_number(0) == 0.0

    def test_strips_rand_prefix(self) -> None:
        assert coerce_diesel_number("R21.28") == 21.28
        assert coerce_diesel_number("R0.00") == 0.0
        assert coerce_diesel_number("R 1244.65") == 1244.65

    def test_reads_comma_as_decimal_separator(self) -> None:
        # "R563,30" is R563.30, not R56330.
        assert coerce_diesel_number("R563,30") == 563.30

    def test_reads_comma_as_thousands_separator_when_a_dot_is_present(self) -> None:
        assert coerce_diesel_number("R1,244.65") == 1244.65

    def test_returns_zero_for_unusable_values(self) -> None:
        for value in (None, "", "   ", "N/A", "-", "Not refueled", [], {}, True):
            assert coerce_diesel_number(value) == 0.0, value

    def test_numeric_strings_survive_the_round_trip(self) -> None:
        assert coerce_diesel_number("22.51") == 22.51
        assert coerce_diesel_number(" 677.1 ") == 677.1


class TestCoerceDieselGenNo:
    def test_reads_explicit_generator_numbers(self) -> None:
        assert coerce_diesel_gen_no(1) == (1, False)
        assert coerce_diesel_gen_no(2) == (2, False)
        assert coerce_diesel_gen_no("1") == (1, False)
        assert coerce_diesel_gen_no("2") == (2, False)

    def test_reads_labelled_generators(self) -> None:
        assert coerce_diesel_gen_no("Gen 1") == (1, False)
        assert coerce_diesel_gen_no("Generator 2") == (2, False)

    def test_defaults_to_generator_one_and_flags_it(self) -> None:
        # A single-generator site frequently omits gen_no entirely.
        for value in (None, "", "N/A", [], {}, True):
            gen_no, inferred = coerce_diesel_gen_no(value)
            assert (gen_no, inferred) == (1, True), value

    def test_out_of_range_numbers_fall_back_to_generator_one(self) -> None:
        # A site has one or two generators; anything else is bad data, not a
        # third bucket.
        assert coerce_diesel_gen_no(3) == (1, True)
        assert coerce_diesel_gen_no(0) == (1, True)
