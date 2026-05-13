from __future__ import annotations

from app.impl.sm_client import SliceManagerClient


def test_sm_client_has_new_rest_methods() -> None:
    """SliceManagerClient exposes the five new REST endpoint methods."""
    for method in (
        "create_slice",
        "delete_slice",
        "associate_slice",
        "dissociate_slice",
        "change_slice",
    ):
        assert callable(getattr(SliceManagerClient, method, None)), (
            f"SliceManagerClient.{method} not found"
        )


def test_sm_client_does_not_expose_legacy_sanitize() -> None:
    """_sanitize_payload (old whitelist helper) must be gone."""
    assert not hasattr(SliceManagerClient, "_sanitize_payload")


def test_sm_client_does_not_expose_legacy_post() -> None:
    """_post (old generic POST helper) must be gone."""
    assert not hasattr(SliceManagerClient, "_post")
