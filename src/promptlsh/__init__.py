"""promptlsh: a similarity digest (fingerprint) for adversarial prompts.

Digest schemes sharing one compare interface:

- ``plm2`` — lexical MinHash over word-shingles (``digest``, dependency-free). **Default.**
  Canonicalises NFKC → strip format characters → case fold, and carries its parameters
  on the wire. See SPEC-digest.md.
- ``plm1`` — the previous lexical scheme (``digest_plm1``). Legacy and frozen: it applied
  no Unicode normalisation, so NFC and NFD forms of the same text produced different
  digests. Retained only so existing digests stay parseable.
- ``pls1`` — semantic SimHash over an embedding (``semantic_digest``, optional extra);
  ``pls1c`` is the mean-centered variant.

Use :func:`compare` to score two digests of the *same* scheme without caring which.
"""

from .digest import (
    LexicalHasher,
    SemHasher,
    canonicalise,
    digest,
    digest_plm1,
    normalize,
    parse_digest,
    parse_digest_full,
    similarity,
    similarity_text,
    tokenise,
)
from .embedding import (
    SemanticHasher,
    fit_reference_mean,
    parse_semantic_digest,
    semantic_digest,
    semantic_similarity,
)

__version__ = "0.4.0"


def compare(digest_a: str, digest_b: str) -> float:
    """Score two digests of the same scheme (``plm1`` lexical or ``pls1`` semantic)."""
    scheme_a = digest_a.split(":", 1)[0]
    scheme_b = digest_b.split(":", 1)[0]
    if scheme_a != scheme_b:
        raise ValueError(f"digests use different schemes: {scheme_a!r} vs {scheme_b!r}")
    if scheme_a in ("plm1", "plm2"):
        return similarity(digest_a, digest_b)
    if scheme_a in ("pls1", "pls1c"):
        return semantic_similarity(digest_a, digest_b)
    raise ValueError(f"unknown digest scheme: {scheme_a!r}")


__all__ = [
    "LexicalHasher",
    "SemHasher",
    "SemanticHasher",
    "__version__",
    "compare",
    "digest",
    "fit_reference_mean",
    "normalize",
    "parse_digest",
    "parse_semantic_digest",
    "semantic_digest",
    "semantic_similarity",
    "similarity",
    "similarity_text",
]
