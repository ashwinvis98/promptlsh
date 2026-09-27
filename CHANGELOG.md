# Changelog

All notable changes to `promptlsh` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> **Digest stability.** Before 1.0.0 the digest formats (`plm1`, `pls1`, `pls1c`) are
> **not** guaranteed stable across releases. A digest stored under one 0.x version may
> not be comparable to one produced by another. From 1.0.0, any incompatible change to a
> digest's bytes will come with a new scheme tag (`plm2`, ...), never a silent change.

## [Unreleased]
### Changed
- **Corrected a published claim: RESULTS §2 is no longer presented as "cross-org
  correlation".** The ~2.9x figure is real but was measured on two random halves of a
  *single* high-redundancy corpus (HackAPrompt), which share wording by construction. It
  measures the exact-vs-fuzzy gap on closely-related material, not a cross-organisation
  rate. §2 is retitled and explicitly scoped; the number itself is unchanged.
- Added a **corrections log** to `RESULTS.md` so changes to published figures are visible
  rather than silently rewritten.

### Added
- **RESULTS §2b — genuine cross-feed correlation** across five independently collected
  public feeds (HackAPrompt, WildJailbreak, in-the-wild/Shen et al., AdvBench, HarmBench)
  at a matched 2,000-prompt cap, reported as rates in both directions.
  - Lexical cross-feed correlation is **~0** (≤0.07%): independent feeds don't share wording.
  - Genuine cross-org semantic signal is **~10–21%** (HackAPrompt ↔ in-the-wild).
  - The two larger overlaps — AdvBench↔HarmBench (28–39%) and WildJailbreak↔in-the-wild
    (12–25%) — are datasets built from one another, so they act as positive controls, not
    findings.
  - Feeds collecting different *artifact types* (jailbreak wrappers vs bare harmful
    requests) barely correlate regardless of provenance.
- **Controls for §2b**, so "no overlap" is distinguishable from "broken pipeline": a lexical
  positive control (45.3% recovery on lightly reworded copies vs 0.7% exact), a semantic
  positive control on known same-intent pairs (median cosine 0.765 — meaning the ≥0.80
  threshold is *stricter* than a typical true pair, so reported rates are a lower bound), and
  a 20k-pair null baseline (median 0.551; only 0.025% of random pairs reach ≥0.80).

## [0.3.1] - 2026-08-16

### Fixed
- **CJK / unsegmented-script near-duplicates.** 0.3.0's Unicode tokenizer fixed the
  all-zero collapse, but `\w+` swallows a whole CJK / Japanese / Thai sentence into a
  single token, so word-shingling degenerated to an exact-match hash and a *reworded* CJK
  prompt scored 0.0. Text containing unsegmented scripts is now shingled at the character
  level (bigrams), restoring near-duplicate sensitivity — a reworded Chinese prompt now
  scores as a near-duplicate (roughly 0.4–0.7 depending on how heavily it is reworded),
  not 0.0. Latin/Cyrillic/Arabic and the pinned English digest are unchanged.

### Changed
- Docs: corrected the size/fidelity framing — the lexical `plm1` (~1.1 KB at 128 perms) is
  dominated by an int8-quantised embedding on both size and recall, so its justification is
  zero ML dependency, not size. Re-ran the cross-org demo on the 128-perm default (2.9x).

## [0.3.0] - 2026-08-16

### Changed
- **Renamed `promptprint` → `promptlsh`.** The previous name collided with existing
  projects (a prompt-based biometrics study and an AI model-router, both "PromptPrint").
  `promptlsh` is free on PyPI/npm and names the method (locality-sensitive hashing). The
  import path, CLI, and the STIX property (`x_promptprint_digest` → `x_promptlsh_digest`)
  change accordingly.
- **Scheme tags renamed** to match the name and to name the LSH method used:
  `ppl1` → `plm1` (MinHash / lexical), `pps1` → `pls1` (SimHash / semantic),
  `pps1c` → `pls1c` (centered). **Digest bytes are unchanged**; only the scheme prefix
  differs, so a re-tagged 0.2.0 digest compares identically.
- Prior-art expanded to cite **0DIN**'s prompt-similarity SDK and jailbreak threat feed;
  novelty narrowed to the vendor-neutral, STIX-native, publicly-measured interchange layer
  (a vendor SDK cannot, by construction, be the cross-vendor exchange format).

## [0.2.0] - 2026-08-16

Review-driven correctness and interoperability fixes. **The digest bytes changed; 0.1.0
digests are not comparable to 0.2.0 digests.**

### Fixed
- **Multilingual collapse (correctness).** The lexical tokenizer matched only
  `[a-z0-9]+`, so any prompt with no ASCII alphanumerics (CJK, Cyrillic, Arabic,
  Devanagari, emoji-only) produced an all-zero digest, and all such digests compared as
  identical (1.0). Appending non-Latin script to a prompt also left its digest unchanged,
  a trivial evasion. Tokenisation is now Unicode-aware (`\w+`, case-folded) with a
  character-n-gram fallback, and an all-zero signature (only possible from empty input)
  is treated as non-comparable (0.0).
- **Cross-version determinism.** Permutation coefficients (lexical) and SimHash
  hyperplanes (semantic) were seeded via Python's `random` module, whose
  `randrange`/`gauss` carry no cross-version stability guarantee. Both are now derived
  from blake2b over the seed and index, so digests are reproducible on any Python version.

### Changed
- **Default `num_perm` 64 → 128** for lower-variance similarity estimates.
- **Semantic digest format now encodes model and reference-mean identity** and enforces
  it: `pls1:<model_id>:<n_bits>:<hex>` and `pls1c:<model_id>:<ref_id>:<n_bits>:<hex>`.
  `semantic_similarity` raises when comparing digests from different models, reference
  means, schemes, or bit-lengths, instead of returning a meaningless number.
- `parse_digest` now validates the declared `num_perm` against the actual slot count.
- The lexical hasher class `SemHasher` is renamed `LexicalHasher`; `SemHasher` remains as
  a deprecated alias.
- Documentation: privacy framing corrected to data-minimisation (not a formal guarantee).
- `RESULTS.md` §1 re-run on HackAPrompt with the fixed tokenizer: full-set exact-dup rate
  57.0% → 52.6%, random-slice collapse ~3x → ~1.8x (7.4% of prompts were previously
  all-zero-colliding). §3 gains an int8-quantised-embedding column showing quantisation
  holds the ceiling while the SimHash digest trades fidelity for a 32-byte size.

## [0.1.0] - 2026-08-15

First public release, renamed from `prompt-semhash` (which collided with the established
MinishLab `semhash` semantic-dedup library).

### Added
- Lexical MinHash digest (`plm1`), dependency-free, with a deterministic serialised format.
- Embedding-backed semantic SimHash digest (`pls1`, `pls1c` centered) behind a shared
  `compare` interface, with fastembed / ONNX backends.
- Evaluation harness and scripts; `RESULTS.md` with a full public-data evaluation.
