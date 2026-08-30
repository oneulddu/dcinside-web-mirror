import pytest

from scripts.public_smoke_url import build_public_smoke_url


def test_build_public_smoke_url_targets_recent_page_and_preserves_base_path():
    assert build_public_smoke_url("https://mirror.example") == "https://mirror.example/recent"
    assert build_public_smoke_url("https://mirror.example/app/") == "https://mirror.example/app/recent"


def test_build_public_smoke_url_allows_unset_value():
    assert build_public_smoke_url("") == ""
    assert build_public_smoke_url(None) == ""


@pytest.mark.parametrize(
    "value",
    [
        "http://mirror.example",
        "https://user:pass@mirror.example",
        "https://mirror.example:8443",
        "https://mirror.example?next=/read",
        "https://mirror.example/#fragment",
        "https://mirror.example/ bad",
        "not-a-url",
    ],
)
def test_build_public_smoke_url_rejects_non_public_base_shapes(value):
    with pytest.raises(ValueError, match="public https base URL"):
        build_public_smoke_url(value)
