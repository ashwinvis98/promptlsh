"""Compare every format you could put on the wire for one prompt.

This is the evaluation behind RESULTS.md section 3 - the size/fidelity curve - and it is
the measurement that argues against this library's own SimHash digest. It was previously
only in an uncommitted scratch script, which meant the central number could not be
reproduced from this repo. It lives here now.

Two experiments:

  1. SAME-ATTACK MATCHING (recall@1) on WildJailbreak `adversarial_harmful` pairs. Each row
     has a `vanilla` request and its jailbroken `adversarial` rewrite: same intent, very
     different surface. For each vanilla request, is its true rewrite the top match among N
     candidates? Run for five wire formats:

       lexical plm1     128-permutation MinHash over word shingles   ~1.1 KB
       cosine ceiling   raw float32 embedding, not a digest          ~1.5 KB
       int8-quant       per-vector symmetric int8 quantisation         384 B
       digest pls1      256-bit SimHash over the embedding              32 B
       digest pls1c     the same, mean-centered                         32 B

  2. REORDER ROBUSTNESS. Word-shuffling is the known failure mode of a word-shingle
     MinHash: it destroys every shingle while preserving meaning. This measures how much
     the *semantic* digest resists the same attack, which the lexical one cannot.

Determinism: fixed seeds throughout (dataset shuffle, SimHash hyperplanes, word shuffle).
Re-running gives identical numbers.

Requirements:
    pip install promptlsh[fastembed] datasets
    WildJailbreak is a gated dataset - accept its terms on HuggingFace first.
    --with-0din additionally needs onnxruntime + tokenizers and downloads the domain model.

Usage:
    python eval/wire_formats.py
    python eval/wire_formats.py --with-0din
    python eval/wire_formats.py --rows 500 --skip-reorder
"""

from __future__ import annotations

import argparse
import os
import random
import sys

import numpy as np

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - only needed behind some corporate TLS setups
    pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from promptlsh.digest import LexicalHasher  # noqa: E402

SEED_DATA = 3          # dataset shuffle
SEED_PLANES = 1        # SimHash hyperplanes
SEED_SHUFFLE = 11      # word-order shuffle
NBITS = 256

# ----------------------------------------------------------------- similarity helpers


def recall_at_1(sim: np.ndarray, n: int) -> float:
    """Fraction of the first n rows whose argmax lands on the diagonal (the true pair)."""
    s = sim[:n, :n]
    return float(np.mean(s.argmax(axis=1) == np.arange(n)))


def _unit(E: np.ndarray) -> np.ndarray:
    return E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)


def cosine(Ev: np.ndarray, Ea: np.ndarray, mean: np.ndarray | None = None) -> np.ndarray:
    V = Ev - mean if mean is not None else Ev
    A = Ea - mean if mean is not None else Ea
    return _unit(V) @ _unit(A).T


def int8_cosine(Ev: np.ndarray, Ea: np.ndarray) -> np.ndarray:
    """Per-vector symmetric int8 quantise -> dequantise -> cosine.

    One scale factor per vector, so the wire cost is one int8 per dimension (plus a float
    scale): 384 bytes for a 384-dim model, versus 1536 for float32.
    """
    def q(E: np.ndarray) -> np.ndarray:
        scale = np.abs(E).max(axis=1, keepdims=True) / 127.0 + 1e-12
        return np.round(E / scale).astype(np.int8).astype(np.float64) * scale
    return cosine(q(Ev), q(Ea))


def simhash_bits(E: np.ndarray, mean: np.ndarray | None = None) -> np.ndarray:
    """Random-hyperplane LSH: sign of the projection onto NBITS fixed Gaussian planes."""
    if mean is not None:
        E = E - mean
    rng = np.random.default_rng(SEED_PLANES)
    H = rng.standard_normal((NBITS, E.shape[1]))
    return (E @ H.T >= 0).astype(np.float64)


def bit_agreement(Bv: np.ndarray, Ba: np.ndarray) -> np.ndarray:
    """Fraction of matching bits between every pair. Random baseline is 0.5, not 0."""
    return (Bv @ Ba.T + (1 - Bv) @ (1 - Ba).T) / NBITS


def agreement_to_cosine(a: float) -> float:
    """SimHash: P(bits match) = 1 - theta/pi, so cos(theta) recovers the angle."""
    return float(np.cos(np.pi * (1.0 - a)))


# ----------------------------------------------------------------- data + embeddings


def load_pairs(n_rows: int) -> tuple[list[str], list[str]]:
    from datasets import load_dataset
    ds = load_dataset("allenai/wildjailbreak", "train",
                      delimiter="\t", keep_default_na=False)["train"]
    rows = [r for r in ds
            if r.get("data_type") == "adversarial_harmful"
            and r.get("vanilla") and r.get("adversarial")]
    random.Random(SEED_DATA).shuffle(rows)
    rows = rows[:n_rows]
    return [r["vanilla"] for r in rows], [r["adversarial"] for r in rows]


def embed_bge(texts: list[str]) -> np.ndarray:
    from fastembed import TextEmbedding
    return np.array(list(TextEmbedding("BAAI/bge-small-en-v1.5").embed(texts)))


def embed_0din(texts: list[str]) -> np.ndarray:
    import onnxruntime as ort
    from huggingface_hub import snapshot_download
    from tokenizers import Tokenizer
    d = snapshot_download("0dinai/jailbreak-embeddings-base-onnx",
                          allow_patterns=["onnx/*", "*.json"])
    tok = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
    tok.enable_truncation(max_length=512)
    tok.enable_padding(pad_id=1, pad_token="<pad>")
    sess = ort.InferenceSession(os.path.join(d, "onnx", "model.onnx"),
                                providers=["CPUExecutionProvider"])
    out = []
    for i in range(0, len(texts), 16):
        enc = tok.encode_batch(texts[i:i + 16])
        ids = np.array([x.ids for x in enc], dtype=np.int64)
        mask = np.array([x.attention_mask for x in enc], dtype=np.int64)
        res = sess.run(None, {"input_ids": ids, "attention_mask": mask})
        tokemb = next(x for x in res if x.ndim == 3)
        mm = mask[:, :, None].astype(np.float32)
        out.append((tokemb * mm).sum(1) / np.clip(mm.sum(1), 1e-9, None))
    return np.vstack(out)


def lexical_sim_matrix(a: list[str], b: list[str]) -> np.ndarray:
    h = LexicalHasher()
    A = np.array([h.signature(t) for t in a])
    B = np.array([h.signature(t) for t in b])
    return (A[:, None, :] == B[None, :, :]).mean(axis=2)


# ----------------------------------------------------------------- experiments


def experiment_recall(van, adv, models, pools):
    print("\n" + "=" * 86)
    print("1. SAME-ATTACK MATCHING - recall@1 on WildJailbreak vanilla <-> adversarial pairs")
    print("=" * 86)
    print("   wire size:   plm1 ~1.1 KB | ceiling ~1.5 KB | int8 384 B | pls1/pls1c 32 B\n")

    lex = lexical_sim_matrix(van, adv)
    hdr = (f"{'model':11} {'N':>5} {'plm1':>8} {'ceiling':>9} {'int8':>8} "
           f"{'pls1':>8} {'pls1c':>8}   {'int8 vs ceiling':>16}")
    print(hdr)
    print("-" * len(hdr))
    results = {}
    for label, EV, EA in models:
        for n in pools:
            ceil = recall_at_1(cosine(EV, EA), n)
            q8 = recall_at_1(int8_cosine(EV, EA), n)
            dig = recall_at_1(bit_agreement(simhash_bits(EV), simhash_bits(EA)), n)
            m = np.vstack([EV[:n], EA[:n]]).mean(0)
            digc = recall_at_1(bit_agreement(simhash_bits(EV, mean=m),
                                             simhash_bits(EA, mean=m)), n)
            lx = recall_at_1(lex, n)
            delta = q8 - ceil
            print(f"{label:11} {n:>5} {lx:>8.3f} {ceil:>9.3f} {q8:>8.3f} "
                  f"{dig:>8.3f} {digc:>8.3f}   {delta:>+16.3f}")
            results[(label, n)] = dict(plm1=lx, ceiling=ceil, int8=q8,
                                       pls1=dig, pls1c=digc)
    return results


def experiment_reorder(texts, embed_fn, label, n=300):
    """Shuffle word order, preserving the bag of words. Kills shingles, keeps meaning."""
    print("\n" + "=" * 86)
    print("2. REORDER ROBUSTNESS - same words, shuffled order")
    print("=" * 86)
    print("   Word-shingle MinHash has no defence here by construction. The question is")
    print("   whether the embedding-derived digest degrades as badly.\n")

    sample = texts[:n]
    rng = random.Random(SEED_SHUFFLE)
    shuffled = []
    for t in sample:
        w = t.split()
        if len(w) > 3:
            rng.shuffle(w)
        shuffled.append(" ".join(w))

    h = LexicalHasher()
    lex_pairs = [
        (np.array(h.signature(a)) == np.array(h.signature(b))).mean()
        for a, b in zip(sample, shuffled)
    ]

    Ea, Eb = embed_fn(sample), embed_fn(shuffled)
    cos_pairs = (_unit(Ea) * _unit(Eb)).sum(axis=1)
    Ba, Bb = simhash_bits(Ea), simhash_bits(Eb)
    agree_pairs = ((Ba == Bb).mean(axis=1))

    print(f"   model: {label}   n = {len(sample)} prompts, each compared to its own shuffle\n")
    print(f"   {'metric':38} {'mean':>8} {'median':>8}")
    print("   " + "-" * 56)
    print(f"   {'plm1 slot match (1.0 = identical)':38} "
          f"{np.mean(lex_pairs):>8.3f} {np.median(lex_pairs):>8.3f}")
    print(f"   {'raw embedding cosine':38} "
          f"{np.mean(cos_pairs):>8.3f} {np.median(cos_pairs):>8.3f}")
    print(f"   {'pls1 bit agreement (0.5 = random)':38} "
          f"{np.mean(agree_pairs):>8.3f} {np.median(agree_pairs):>8.3f}")
    print(f"   {'pls1 implied cosine':38} "
          f"{agreement_to_cosine(float(np.mean(agree_pairs))):>8.3f}")
    return dict(plm1=float(np.mean(lex_pairs)), cosine=float(np.mean(cos_pairs)),
                pls1_agreement=float(np.mean(agree_pairs)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=1000)
    ap.add_argument("--with-0din", action="store_true",
                    help="also evaluate the domain-tuned 0din model (downloads ~1 GB)")
    ap.add_argument("--skip-reorder", action="store_true")
    args = ap.parse_args()

    print(f"loading WildJailbreak adversarial_harmful pairs (rows={args.rows}, "
          f"seed={SEED_DATA}) ...")
    van, adv = load_pairs(args.rows)
    print(f"  {len(van)} pairs")

    print("embedding with bge-small ...")
    models = [("bge-small", embed_bge(van), embed_bge(adv))]
    if args.with_0din:
        print("embedding with 0din (domain-tuned) ...")
        models.append(("0din", embed_0din(van), embed_0din(adv)))

    experiment_recall(van, adv, models, pools=(400, 1000))

    if not args.skip_reorder:
        experiment_reorder(adv, embed_bge, "bge-small")

    print("\nnotes")
    print("  * int8 vs ceiling near 0.000 means quantisation costs essentially nothing,")
    print("    which is the finding that argues against shipping a 32-byte hash.")
    print("  * 0din is substantially in-distribution here: its card reports pre-training on")
    print("    161,396 WildJailbreak pairs, and this eval runs on WildJailbreak pairs.")
    print("    Quote bge-small as the clean reference.")


if __name__ == "__main__":
    main()
