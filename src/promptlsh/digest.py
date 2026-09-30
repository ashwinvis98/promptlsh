"""A lexical similarity digest for adversarial prompts.

``promptlsh`` computes a MinHash signature over shingles so that near-duplicate
prompts produce *comparable* digests: two prompts that share phrasing land close
together, and a platform can cluster them without re-reading the raw text.

This is the **lexical baseline**. It catches rewording that preserves shared word
sequences (the common case for copy-paste-and-tweak jailbreaks); it does **not**
capture full semantic paraphrase where the wording changes but the meaning does
not. That is the embedding-based direction (``pls1``, see ``embedding.py``). This
module is deliberately dependency-free so the baseline is trivial to run and audit.

--------------------------------------------------------------------------------
TWO SCHEMES
--------------------------------------------------------------------------------

``plm2`` — **current default.** Canonicalises as NFKC → strip format characters →
case fold → NFKC, then shingles and MinHashes. Carries ``num_perm``, ``shingle_size``
and ``seed`` on the wire, so a digest built with different parameters is rejected
rather than silently compared to a plausible-looking number.

    plm2:<num_perm>:<shingle_size>:<seed>:<hex>:<hex>:...

``plm1`` — **legacy, frozen.** Case folds and nothing else. Retained so digests
already in circulation stay parseable and comparable. Do not emit new ones.

    plm1:<num_perm>:<hex>:<hex>:...

Why the break: ``plm1`` applied no Unicode normalisation, so NFC and NFD encodings
of visually identical text produced different digests — measured at 0.000 similarity
for Korean and Vietnamese, 0.18–0.24 for French, German and Spanish. For a format
whose entire purpose is two parties computing comparable digests, that defeats the
premise, and it was a free evasion: send NFD, defeat an NFC index. ``plm1`` also let
a single zero-width character split a token and move the digest. ``plm2`` fixes both.

Full normative specification, including the measurements behind that decision, is in
SPEC-digest.md. Conformance vectors for both schemes are in tests/vectors/.

Each ``<hex>`` is one 32-bit MinHash slot. Two digests of the same scheme and
parameters are compared slot-by-slot; the fraction of matching slots estimates the
Jaccard similarity of the two prompts' shingle sets.
"""

from __future__ import annotations

import re
import unicodedata
from hashlib import blake2b

_MERSENNE = (1 << 61) - 1          # large prime for the (a*h + b) mod p permutation
_MAX_HASH = 1 << 32                # slots are reduced to 32 bits for compact serialization
_DEFAULT_NUM_PERM = 128
_DEFAULT_SHINGLE = 3
# Character n-grams (for unsegmented scripts + emoji/punctuation-only input) use bigrams:
# CJK "words" are typically 1-2 characters, so bigrams are the standard granularity and
# retain materially more near-duplicate signal than 3-grams (on a reworded zh
# prompt-injection pair: bigram ~0.43 vs trigram ~0.27; exact Jaccard 0.45 vs 0.30).
_CHAR_SHINGLE = 2

_SCHEME_V1 = "plm1"
_SCHEME_V2 = "plm2"
_SCHEME = _SCHEME_V2               # what new digests use
_SCHEMES = (_SCHEME_V1, _SCHEME_V2)

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Scripts with no whitespace word boundaries (CJK ideographs, Japanese kana, Thai).
# ``\w+`` swallows a whole sentence in these into one or two tokens, so word-shingling
# degenerates into an exact-match hash and near-duplicates score 0. Text containing these
# is shingled at the **character** level instead, which restores near-duplicate sensitivity.
# Modern Korean is space-segmented, so Hangul is deliberately absent.
_UNSEGMENTED_RE = re.compile(
    "[\u3040-\u30ff"   # Hiragana + Katakana
    "\u3400-\u4dbf"    # CJK Extension A
    "\u4e00-\u9fff"    # CJK Unified Ideographs
    "\uf900-\ufaff"    # CJK Compatibility Ideographs
    "\u0e00-\u0e7f]"   # Thai
)


# --------------------------------------------------------------------------- #
# Canonicalisation
# --------------------------------------------------------------------------- #

def canonicalise(text: str, scheme: str = _SCHEME) -> str:
    """Return the canonical form of *text* for *scheme*.

    ``plm2``: NFKC → drop format characters (category ``Cf``) → case fold → NFKC.

    The trailing NFKC is not redundant. Case folding can denormalise — it maps some
    characters to sequences that are themselves not NFKC — so without it
    ``canonicalise(canonicalise(x)) != canonicalise(x)`` for a handful of inputs, and an
    implementation that normalised at a different point would disagree with this one.
    Idempotence is asserted in the test suite.

    ``Cf`` is dropped because zero-width and other invisible characters otherwise split a
    token and move the digest at no cost to an attacker.

    ``plm1``: case fold only. Frozen; this is the defect ``plm2`` exists to fix.
    """
    t = text or ""
    if scheme == _SCHEME_V1:
        return t.casefold()
    t = unicodedata.normalize("NFKC", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf")
    t = t.casefold()
    return unicodedata.normalize("NFKC", t)


def normalize(text: str) -> list[str]:
    """Case-fold and tokenise to Unicode word tokens (``plm1`` semantics).

    Kept for backwards compatibility and because the ``plm1`` conformance vectors record
    its output. For ``plm2`` tokenisation, canonicalise first: ``tokenise(canonicalise(t))``.
    """
    return _WORD_RE.findall((text or "").casefold())


def tokenise(canon: str) -> list[str]:
    """Tokenise text that is ALREADY canonical. Does not case-fold again.

    Word characters are Unicode categories ``Lu Ll Lt Lm Lo Nd Nl No`` plus U+005F,
    which is exactly what Python's ``\\w`` matches — verified exhaustively over every
    Unicode scalar value. Reimplementations must spell the categories out: ``\\w`` is
    ASCII-only in Go by default and in JavaScript always.
    """
    return _WORD_RE.findall(canon or "")


# --------------------------------------------------------------------------- #
# Shingling
# --------------------------------------------------------------------------- #

def _char_shingles(canon: str) -> set[str]:
    """Character n-grams over already-canonical text with all whitespace removed."""
    chars = "".join((canon or "").split())
    if not chars:
        return set()
    if len(chars) < _CHAR_SHINGLE:
        return {chars}
    return {chars[i : i + _CHAR_SHINGLE] for i in range(len(chars) - _CHAR_SHINGLE + 1)}


def _shingles_canon(canon: str, k: int) -> set[str]:
    """Shingle set for already-canonical text. Branches evaluated in this order:

    1. Unsegmented script present anywhere → character n-grams for the WHOLE input.
    2. ``n >= k`` tokens → word k-shingles joined by a single space.
    3. ``0 < n < k`` tokens → one shingle, all tokens joined.
    4. no tokens → character n-grams.
    """
    if _UNSEGMENTED_RE.search(canon or ""):
        return _char_shingles(canon)
    tokens = tokenise(canon)
    if len(tokens) >= k:
        return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}
    if tokens:
        return {" ".join(tokens)}
    return _char_shingles(canon)


def _shingles(text: str, k: int, scheme: str = _SCHEME_V1) -> set[str]:
    """Shingle set for RAW text. Canonicalises first.

    Defaults to ``plm1`` so existing callers and the plm1 vectors are unaffected.
    """
    return _shingles_canon(canonicalise(text, scheme), k)


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #

def _base_hash(shingle: str) -> int:
    return int.from_bytes(blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big")


def _coeff(seed: int, salt: str, i: int, *, nonzero: bool) -> int:
    """Derive a permutation coefficient deterministically from a cryptographic hash.

    Using blake2b over ``(seed, salt, index)`` rather than the ``random`` module makes
    the coefficients — and therefore every digest — reproducible across *any* Python
    version. ``random.randrange``/``gauss`` carry no cross-version stability guarantee;
    blake2b does.
    """
    h = int.from_bytes(
        blake2b(f"{seed}:{salt}:{i}".encode("utf-8"), digest_size=8).digest(), "big"
    )
    return (h % (_MERSENNE - 1)) + 1 if nonzero else h % _MERSENNE


class LexicalHasher:
    """Deterministic MinHash digester.

    Permutation coefficients are derived from a fixed seed via blake2b, so any two
    installations — on any Python version — produce **identical** digests for the same
    text. That reproducibility is a hard requirement for cross-instance correlation.

    ``scheme`` selects canonicalisation and wire format; defaults to ``plm2``. Pass
    ``scheme="plm1"`` only to reproduce legacy digests.
    """

    def __init__(
        self,
        num_perm: int = _DEFAULT_NUM_PERM,
        shingle_size: int = _DEFAULT_SHINGLE,
        seed: int = 1,
        scheme: str = _SCHEME,
    ) -> None:
        if scheme not in _SCHEMES:
            raise ValueError(f"unknown scheme {scheme!r}; expected one of {_SCHEMES}")
        self.num_perm = num_perm
        self.shingle_size = shingle_size
        self.seed = seed
        self.scheme = scheme
        self._a = [_coeff(seed, "a", i, nonzero=True) for i in range(num_perm)]
        self._b = [_coeff(seed, "b", i, nonzero=False) for i in range(num_perm)]

    def signature(self, text: str) -> list[int]:
        """Return the MinHash signature (a list of ``num_perm`` 32-bit ints)."""
        shingles = _shingles_canon(canonicalise(text, self.scheme), self.shingle_size)
        if not shingles:
            return [0] * self.num_perm
        mins = [_MAX_HASH - 1] * self.num_perm
        for shingle in shingles:
            h = _base_hash(shingle)
            for i in range(self.num_perm):
                v = ((self._a[i] * h + self._b[i]) % _MERSENNE) % _MAX_HASH
                mins[i] = min(mins[i], v)
        return mins

    def digest(self, text: str) -> str:
        """Return the serialised digest string for *text* under this hasher's scheme."""
        sig = self.signature(text)
        slots = ":".join(format(v, "08x") for v in sig)
        if self.scheme == _SCHEME_V1:
            return f"{_SCHEME_V1}:{self.num_perm}:{slots}"
        return f"{_SCHEME_V2}:{self.num_perm}:{self.shingle_size}:{self.seed}:{slots}"


# Backwards-compatible alias for the pre-0.1 name. Deprecated; use LexicalHasher.
SemHasher = LexicalHasher


# --------------------------------------------------------------------------- #
# Parsing and comparison
# --------------------------------------------------------------------------- #

def parse_digest_full(digest_str: str) -> tuple[str, dict, list[int]]:
    """Parse any lexical digest into ``(scheme, params, slots)``.

    Validates that the declared ``num_perm`` matches the actual slot count, so a
    truncated or malformed digest fails loudly rather than comparing as a shorter one.
    """
    parts = (digest_str or "").split(":")
    if not parts or parts[0] not in _SCHEMES:
        raise ValueError(f"not a lexical promptlsh digest: {digest_str!r}")
    scheme = parts[0]
    if scheme == _SCHEME_V1:
        if len(parts) < 3:
            raise ValueError(f"malformed {scheme} digest: {digest_str!r}")
        declared = int(parts[1])
        body = parts[2:]
        params = {"num_perm": declared, "shingle_size": None, "seed": None}
    else:
        if len(parts) < 5:
            raise ValueError(f"malformed {scheme} digest: {digest_str!r}")
        declared = int(parts[1])
        params = {
            "num_perm": declared,
            "shingle_size": int(parts[2]),
            "seed": int(parts[3]),
        }
        body = parts[4:]
    slots = [int(x, 16) for x in body]
    if len(slots) != declared:
        raise ValueError(f"declared num_perm {declared} != {len(slots)} slots")
    return scheme, params, slots


def parse_digest(digest_str: str) -> list[int]:
    """Parse a lexical digest back into its list of slot values."""
    return parse_digest_full(digest_str)[2]


def similarity(digest_a: str, digest_b: str) -> float:
    """Estimate Jaccard similarity (0.0–1.0) from two lexical digests.

    Raises if the two digests are not comparable: different schemes, different
    ``num_perm``, or — for ``plm2``, which carries them — different ``shingle_size`` or
    ``seed``. ``plm1`` cannot express the last two, which is why ``plm2`` exists.

    An all-zero signature only arises from genuinely empty input; it is treated as
    non-comparable (0.0) so degenerate inputs never collide at 1.0.
    """
    scheme_a, params_a, sa = parse_digest_full(digest_a)
    scheme_b, params_b, sb = parse_digest_full(digest_b)
    if scheme_a != scheme_b:
        raise ValueError(f"different schemes: {scheme_a!r} vs {scheme_b!r}")
    if len(sa) != len(sb):
        raise ValueError("digest length mismatch (different num_perm)")
    for key in ("shingle_size", "seed"):
        if params_a[key] is not None and params_a[key] != params_b[key]:
            raise ValueError(
                f"different {key}: {params_a[key]} vs {params_b[key]} — not comparable"
            )
    if not sa or not any(sa) or not any(sb):
        return 0.0
    return sum(1 for x, y in zip(sa, sb) if x == y) / len(sa)


_default = LexicalHasher()
_default_v1 = LexicalHasher(scheme=_SCHEME_V1)


def digest(text: str) -> str:
    """Compute the default (``plm2``) digest for *text*."""
    return _default.digest(text)


def digest_plm1(text: str) -> str:
    """Compute a legacy ``plm1`` digest. For reproducing existing digests only."""
    return _default_v1.digest(text)


def similarity_text(text_a: str, text_b: str) -> float:
    """Convenience: estimate similarity directly from two prompt strings."""
    return similarity(_default.digest(text_a), _default.digest(text_b))
