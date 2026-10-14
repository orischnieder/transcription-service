import pytest
from faster_whisper.tokenizer import _LANGUAGE_CODES

from app.api.errors import AppError
from app.services.languages import SUPPORTED_LANGUAGES, normalize_language


def test_supported_languages_are_known_to_faster_whisper() -> None:
    # The API keeps its own list so it never imports the engine; this guards against drift.
    assert set(_LANGUAGE_CODES) >= SUPPORTED_LANGUAGES


@pytest.mark.parametrize("value", [None, "", "  ", "auto", "AUTO"])
def test_empty_or_auto_means_detect(value: str | None) -> None:
    assert normalize_language(value) is None


def test_code_is_normalized() -> None:
    assert normalize_language(" He ") == "he"


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(AppError) as exc_info:
        normalize_language("xx")

    assert exc_info.value.code == "invalid_language"
