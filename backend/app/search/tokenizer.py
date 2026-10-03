"""Deterministic tokenizer for Phase 7 Search & Similarity.

Tokenizes text consistently for both indexed documents and user queries.
No external NLP dependencies — standard library only.
"""

import re
import unicodedata
from typing import List

# ---------------------------------------------------------------------------
# Stop-word list — small, deterministic, centralized.
# These are very common English function words that add noise to TF-IDF.
# Deliberately kept minimal to preserve technical terms like: api, oop, etc.
# ---------------------------------------------------------------------------
STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the",
    "and", "or", "but", "if", "in", "on", "at", "to", "for",
    "of", "with", "by", "as", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "shall", "can",
    "this", "that", "these", "those", "it", "its",
    "i", "you", "he", "she", "we", "they", "me", "him", "her", "us", "them",
    "not", "no", "nor",
    "from", "into", "through", "during", "before", "after",
    "about", "against", "between", "each", "further",
    "then", "once",
})


def _unicode_normalize(text: str) -> str:
    """NFKC-normalize and casefold the text for Unicode-safe comparison."""
    return unicodedata.normalize("NFKC", text).casefold()


def tokenize(text: str, remove_stop_words: bool = True) -> List[str]:
    """Tokenize text deterministically.

    Strategy:
    - Unicode NFKC-normalize and casefold.
    - Split on whitespace and common delimiters (``/``, ``\\``, ``-``, ``_``, ``.``).
    - Keep words and numbers; discard punctuation-only tokens.
    - Remove stop words from the list above (optional).
    - Tokens must be at least 1 character long.

    Technical terms like ``java``, ``python``, ``api``, ``oop``, ``react``,
    ``2026``, ``v2`` remain searchable.

    Args:
        text: Raw text to tokenize.
        remove_stop_words: Whether to filter common English stop words.

    Returns:
        List of normalised token strings in input order.
    """
    if not text:
        return []

    # 1. Unicode normalize + casefold
    text = _unicode_normalize(text)

    # 2. Split on delimiters: whitespace, /, \\, -, _, dot (but not dot inside
    #    version numbers like 3.14 — we handle that by splitting on MULTIPLE
    #    punctuation chars or boundary-only punctuation).
    #    Strategy: replace delimiter chars with a space, then split.
    text = re.sub(r"[/\\:;\|\(\)\[\]{}<>\"'`~@#$%^&*+=]", " ", text)
    text = re.sub(r"[_\-]", " ", text)

    # 3. Collapse whitespace
    words = text.split()

    tokens: List[str] = []
    for word in words:
        # Strip leading/trailing dots and commas (e.g. trailing period, comma)
        word = word.strip(".,!?")
        # Keep the word only if it contains at least one alphanumeric character
        if re.search(r"[a-z0-9]", word):
            tokens.append(word)

    # 4. Optionally remove stop words
    if remove_stop_words:
        tokens = [t for t in tokens if t not in STOP_WORDS]

    return tokens


def tokenize_filename(name: str, extension: str) -> List[str]:
    """Tokenize a filename stem into searchable terms.

    Separates letter-runs from digit-runs at letter/digit transitions so that
    ``java123`` → [``java``, ``123``].  Preserves extension as a token.

    Args:
        name: Full filename including extension.
        extension: Extension (with dot) to also include as a token.

    Returns:
        List of lowercase tokens.
    """
    if extension and name.lower().endswith(extension.lower()):
        stem = name[: -len(extension)]
    else:
        stem = name
        extension = ""

    # Split on non-alphanumeric and on letter↔digit transitions
    raw = re.findall(r"[a-zA-Z]+|[0-9]+", stem)
    tokens = [_unicode_normalize(t) for t in raw if t]

    # Always include the extension (without dot) as a searchable token
    if extension:
        ext_token = extension.lstrip(".").lower()
        if ext_token:
            tokens.append(ext_token)

    return tokens


def tokenize_path(relative_path: str) -> List[str]:
    """Extract folder-name tokens from a relative file path.

    Tokenizes each path segment individually using :func:`tokenize_filename`.
    The filename itself is excluded (only folder components are used).

    Args:
        relative_path: Relative file path using any path separator.

    Returns:
        List of lowercase folder-name tokens.
    """
    path = relative_path.replace("\\", "/")
    parts = path.split("/")
    # Exclude the last element (filename) — caller can add filename tokens separately
    folder_parts = parts[:-1]

    tokens: List[str] = []
    for part in folder_parts:
        tokens.extend(re.findall(r"[a-z0-9]+", _unicode_normalize(part)))
    return tokens
