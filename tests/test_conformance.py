"""Conformance tests: the implementation must match tests/vectors/plm1.json exactly.

This supersedes the single inline pinned digest that used to live in test_digest.py. That
vector was pure ASCII at default settings, which covered one of the four shingler paths and
is the reason the Unicode normalisation defect went unnoticed for so long.

The vector file is the normative artifact, not this test. A reimplementation in any language
reads `tests/vectors/plm1.json`, computes a digest from each `input_codepoints` under the
given `params`, and is conformant when every one matches. This file is just the Python
implementation taking its own exam.

Runnable with pytest or directly:  python tests/test_conformance.py
"""

from __future__ import annotations

import json
import pathlib

from promptlsh.digest import (
    _CHAR_SHINGLE,
    _DEFAULT_NUM_PERM,
    _DEFAULT_SHINGLE,
    _MAX_HASH,
    _MERSENNE,
    _SCHEME,
    LexicalHasher,
    _base_hash,
    _coeff,
    _shingles,
    normalize,
    parse_digest,
    similarity,
)

VECTORS = pathlib.Path(__file__).resolve().parent / "vectors" / "plm1.json"
_DATA = json.loads(VECTORS.read_text(encoding="utf-8"))


def _hasher(v: dict) -> LexicalHasher:
    p = v["params"]
    return LexicalHasher(num_perm=p["num_perm"], shingle_size=p["shingle_size"], seed=p["seed"])


def _text(v: dict) -> str:
    """Rebuild the input from codepoints, not from input_display.

    Deliberate: reading the display string back out of JSON could hand us a normalised copy,
    which is precisely the bug class these vectors exist to pin down.
    """
    return "".join(chr(c) for c in v["input_codepoints"])


def test_vector_file_is_present_and_declares_the_scheme():
    assert _DATA["scheme"] == _SCHEME
    assert _DATA["vectors"], "no vectors in the file"


def test_every_vector_digest_matches():
    failures = []
    for v in _DATA["vectors"]:
        got = _hasher(v).digest(_text(v))
        if got != v["digest"]:
            failures.append(f"{v['id']}: expected {v['digest'][:40]}... got {got[:40]}...")
    assert not failures, "digest mismatch:\n  " + "\n  ".join(failures)


def test_codepoints_and_utf8_agree():
    """The two encodings of the input in the file must describe the same string."""
    for v in _DATA["vectors"]:
        assert _text(v).encode("utf-8").hex() == v["input_utf8_hex"], v["id"]


def test_recorded_tokens_and_shingles_match():
    for v in _DATA["vectors"]:
        text = _text(v)
        assert normalize(text) == v["tokens"], v["id"]
        sh = _shingles(text, v["params"]["shingle_size"])
        assert sorted(sh) == v["shingles_sorted"], v["id"]
        assert len(sh) == v["shingle_count"], v["id"]


def test_declared_constants_match_the_implementation():
    c = _DATA["constants"]
    assert c["scheme_tag"] == _SCHEME
    assert c["default_num_perm"] == _DEFAULT_NUM_PERM
    assert c["default_shingle_size"] == _DEFAULT_SHINGLE
    assert c["char_shingle_size"] == _CHAR_SHINGLE
    assert c["mersenne_prime"] == str(_MERSENNE)
    assert c["slot_modulus"] == str(_MAX_HASH)
    assert c["slot_init_value"] == str(_MAX_HASH - 1)


def test_derived_examples_match():
    d = _DATA["derived_examples"]
    assert d["base_hash('ignore previous instructions')"] == str(
        _base_hash("ignore previous instructions"))
    assert d["coeff(seed=1, salt='a', i=0, nonzero=True)"] == str(_coeff(1, "a", 0, nonzero=True))
    assert d["coeff(seed=1, salt='b', i=0, nonzero=False)"] == str(
        _coeff(1, "b", 0, nonzero=False))
    h = LexicalHasher()
    assert d["a[0:3] for seed=1"] == [str(x) for x in h._a[:3]]
    assert d["b[0:3] for seed=1"] == [str(x) for x in h._b[:3]]


def test_slot_count_and_width_are_as_declared():
    for v in _DATA["vectors"]:
        slots = parse_digest(v["digest"])
        assert len(slots) == v["params"]["num_perm"], v["id"]
        for hexpart in v["digest"].split(":")[2:]:
            assert len(hexpart) == _DATA["constants"]["slot_hex_width"], v["id"]
            assert hexpart == hexpart.lower(), f"{v['id']}: hex must be lowercase"
        assert all(0 <= s < _MAX_HASH for s in slots), v["id"]


def test_all_zero_flag_is_accurate_and_non_comparable():
    for v in _DATA["vectors"]:
        slots = parse_digest(v["digest"])
        assert (not any(slots)) == v["all_zero"], v["id"]
        if v["all_zero"]:
            # the contract: degenerate input must NOT compare equal to itself
            assert similarity(v["digest"], v["digest"]) == 0.0, v["id"]


def test_all_four_shingle_paths_are_covered():
    paths = {v["shingle_path"] for v in _DATA["vectors"]}
    expected = {
        "word-shingle",
        "word-shingle (single, fewer tokens than k)",
        "char-ngram (unsegmented script)",
        "char-ngram (no word tokens)",
    }
    assert expected <= paths, f"uncovered paths: {expected - paths}"


def test_digests_of_different_num_perm_are_rejected_not_compared():
    a = next(v for v in _DATA["vectors"] if v["id"] == "ascii-baseline")["digest"]
    b = next(v for v in _DATA["vectors"] if v["id"] == "num-perm-32")["digest"]
    try:
        similarity(a, b)
    except ValueError:
        return
    raise AssertionError("comparing different num_perm must raise, not return a number")


# --- the pinned defects ------------------------------------------------------------ #
# These assert the CURRENT broken behaviour on purpose. They are the tripwire: fixing
# normalisation makes them fail, which forces a scheme bump rather than a silent change to
# every digest in circulation. See SPEC-digest.md, "Known defects".

def test_defect_nfc_nfd_still_differ():
    nfc = next(v for v in _DATA["vectors"] if v["id"] == "nfc-french")
    nfd = next(v for v in _DATA["vectors"] if v["id"] == "nfd-french")
    assert _text(nfc) != _text(nfd), "the two vectors must be different byte sequences"
    assert nfc["digest"] != nfd["digest"], (
        "NFC and NFD now agree - the normalisation defect appears to be fixed. That is a "
        "BREAKING change to the wire format: bump the scheme tag (plm2), regenerate "
        "tests/vectors/, and update SPEC-digest.md before removing this test."
    )


def test_defect_zero_width_injection_moves_the_digest():
    base = next(v for v in _DATA["vectors"] if v["id"] == "ascii-baseline")
    zwsp = next(v for v in _DATA["vectors"] if v["id"] == "zwsp-injected")
    assert base["digest"] != zwsp["digest"], (
        "zero-width injection no longer moves the digest - format characters are now being "
        "stripped. Breaking change: bump the scheme tag and regenerate the vectors."
    )
    sim = similarity(base["digest"], zwsp["digest"])
    assert sim < 0.6, f"expected a large drop from one invisible character, got {sim}"


def test_known_defects_are_documented_with_vectors():
    for d in _DATA["known_defects"]:
        assert d["vectors"], f"defect {d['id']} names no vectors"
        ids = {v["id"] for v in _DATA["vectors"]}
        for vid in d["vectors"]:
            assert vid in ids, f"defect {d['id']} references unknown vector {vid}"


def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed over {len(_DATA['vectors'])} vectors")


if __name__ == "__main__":
    _run_all()
