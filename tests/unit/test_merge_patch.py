from __future__ import annotations

import pytest

from app.utils.merge_patch import apply_merge_patch


@pytest.mark.parametrize(
    ("target", "patch", "expected"),
    [
        # Examples from RFC 7396, Appendix A
        ({"a": "b"}, {"a": "c"}, {"a": "c"}),
        ({"a": "b"}, {"b": "c"}, {"a": "b", "b": "c"}),
        ({"a": "b"}, {"a": None}, {}),
        ({"a": "b", "b": "c"}, {"a": None}, {"b": "c"}),
        ({"a": ["b"]}, {"a": "c"}, {"a": "c"}),
        ({"a": "c"}, {"a": ["b"]}, {"a": ["b"]}),
        ({"a": {"b": "c"}}, {"a": {"b": "d", "c": None}}, {"a": {"b": "d"}}),
        ({"a": [{"b": "c"}]}, {"a": [1]}, {"a": [1]}),
        ({"e": None}, {"a": 1}, {"e": None, "a": 1}),
        ({}, {"a": {"bb": {"ccc": None}}}, {"a": {"bb": {}}}),
    ],
)
def test_rfc7396_examples(target, patch, expected) -> None:
    assert apply_merge_patch(target, patch) == expected


def test_inputs_are_not_modified() -> None:
    target = {"tscQosReq": {"reqGbrUl": "5 Mbps", "reqMbrUl": "10 Mbps"}}
    patch = {"tscQosReq": {"reqGbrUl": None}}

    result = apply_merge_patch(target, patch)

    assert result == {"tscQosReq": {"reqMbrUl": "10 Mbps"}}
    assert target == {"tscQosReq": {"reqGbrUl": "5 Mbps", "reqMbrUl": "10 Mbps"}}
    assert patch == {"tscQosReq": {"reqGbrUl": None}}
