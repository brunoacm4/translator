# coding: utf-8

"""
JSON Merge Patch (RFC 7396), as required by 3GPP TS 29.122 for PATCH bodies
(``application/merge-patch+json``).
"""

from __future__ import annotations

import copy
from typing import Any


def apply_merge_patch(target: Any, patch: Any) -> Any:
    """
    Apply *patch* to *target* and return the result; neither input is modified.

    - A member set to ``null`` (``None``) removes that member from the target.
    - A member whose value is an object is merged recursively.
    - Any other value replaces the target's value.
    - A patch that is not an object replaces the target entirely.

    Examples:
        >>> apply_merge_patch({"a": 1, "b": 2}, {"b": None, "c": 3})
        {'a': 1, 'c': 3}
        >>> apply_merge_patch({"q": {"ul": 1, "dl": 2}}, {"q": {"ul": None}})
        {'q': {'dl': 2}}
    """
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = apply_merge_patch(result.get(key), value)
    return result
