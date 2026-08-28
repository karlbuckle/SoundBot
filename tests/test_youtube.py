import pytest

from app.youtube import validate_youtube_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://music.youtube.com/watch?v=abc",
    ],
)
def test_accepts_youtube_urls(url: str) -> None:
    assert validate_youtube_url(url) == url


@pytest.mark.parametrize(
    "url",
    ["https://example.com/video", "file:///etc/passwd", "not a url"],
)
def test_rejects_non_youtube_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_youtube_url(url)
