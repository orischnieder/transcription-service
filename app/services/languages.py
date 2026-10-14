from app.api.errors import AppError

# ISO 639-1 (and a few longer) codes Whisper supports. "yue" (Cantonese) is left out on purpose:
# only large-v3 models know it, and the default model is small.
_LANGUAGE_CODES = """
af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw he
hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my ne
nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk
ur uz vi yi yo zh
"""
SUPPORTED_LANGUAGES = frozenset(_LANGUAGE_CODES.split())


def normalize_language(value: str | None) -> str | None:
    """Return a supported language code, or None for automatic detection."""
    if value is None:
        return None
    code = value.strip().lower()
    if code in ("", "auto"):
        return None
    if code not in SUPPORTED_LANGUAGES:
        raise AppError(
            "invalid_language",
            f"'{value}' is not a supported language code. Leave it empty for automatic detection.",
            422,
        )
    return code
