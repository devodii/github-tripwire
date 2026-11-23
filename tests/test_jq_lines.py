import json

import pytest

from tripwire.hooks.jq_lines import JqLinesOptions, jq_lines


def run(data, query):
    text = data if isinstance(data, str) else json.dumps(data)
    return jq_lines(text, JqLinesOptions(query=query))


def test_strings_come_out_raw_with_emoji_and_accents_intact():
    out = run([{"who": "zoë", "n": 1}], '.[] | "💬 \\(.who) #\\(.n)"')
    assert out == "💬 zoë #1"


def test_one_line_per_result_and_non_strings_are_json():
    assert run({"a": [1, 2]}, ".a[]") == "1\n2"
    assert run({"a": {"k": "é"}}, ".a") == '{"k": "é"}'


def test_no_results_is_an_empty_string_not_an_error():
    assert run([], ".[]") == ""


def test_a_response_that_is_not_json_is_an_error_not_an_empty_snapshot():
    with pytest.raises(ValueError, match="not JSON"):
        run("<html>rate limited</html>", ".[]")


def test_a_query_that_fails_on_this_shape_is_an_error():
    with pytest.raises(ValueError, match="query failed"):
        run({"message": "API rate limit exceeded"}, ".[] | .user.login")


def test_an_invalid_program_is_rejected_when_options_are_parsed():
    with pytest.raises(ValueError, match="not a valid jq program"):
        JqLinesOptions(query=".[ | broken")
