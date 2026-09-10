"""Conservative text normalization for native extraction and OCR output."""

from __future__ import annotations

import re
import unicodedata


_PUNCT_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\uff1a": ":",
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
    }
)


def normalize_invoice_text(text: str, *, ocr: bool = False) -> str:
    """Normalize layout noise without changing identifier or numeric meaning."""
    value = unicodedata.normalize("NFKC", text or "").translate(_PUNCT_TRANSLATION)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\t\f\v]+", " ", value)
    value = re.sub(r"[ ]{2,}", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)

    if ocr:
        # Fix only high-confidence OCR substitutions in numeric contexts.  A
        # global O/I/0 rewrite would corrupt invoice numbers and company names.
        value = re.sub(r"(?<=\d)[Oo](?=\d)", "0", value)
        value = re.sub(r"(?<=\d)[Il](?=[\d,.])", "1", value)
        value = re.sub(r"(?<=[\d,.])[Il](?=\d)", "1", value)
    return value.strip()


def compact_key(value: str) -> str:
    """Canonical comparison key for invoice references and header-like values."""
    return re.sub(r"\s+", "", normalize_invoice_text(value).casefold())
