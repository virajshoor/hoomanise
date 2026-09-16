# The Humanizer Engine (`app/engine/`)

Pure Python + stdlib only. No ML models, no network calls, deterministic given a seed.
Entry point: `pipeline.humanize(text, preset, intensity, seed) -> (humanized_text, stats)`.

## Why it works (threat model)

AI detectors read statistical signals. Each scorer in `analysis.py` implements one; each
transform in `transforms.py` degrades one or more. Score each text 0–1 AI-likelihood:

| Scorer (`analysis.py`) | Signal | Key logic |
|---|---|---|
| `perplexity_proxy` | Predictable word choice | `rare_token_share` (share of tokens ranked >1500 in the 10k frequency list, excluding AI-favorite words) + repeated-bigram rate. AI ≈ few rare tokens. |
| `burstiness` | Uniform sentence lengths | CV (std/mean) of sentence word counts. AI < 0.3, human 0.4–0.8. `_norm(cv, 0.70, 0.22)` (inverted range = high score when CV low). |
| `stylometry` | Style fingerprint | Per-1000-word rates: contractions, first-person, -ly adverbs, nominalizations, passive voice, em-dash, semicolon, comma; repeated openers; connective openers. Weighted blend; returns 0.5 neutral under 120 words. |
| `ai_phrase_density` | Known AI vocabulary | ~80 regex phrase rules + single-word table; per-vendor tally (gpt/claude/gemini/generic). `_norm(density, 2.0, 20.0)` per 1000 words. |
| `detectgpt_proxy` | Local likelihood maxima | Proxy: sentence-length spread + trigram collision rate (repetitive structure). |
| `classifier_proxy` | XGBoost/BERT hybrid features | Weighted blend: phrases 0.26, perplexity 0.22, stylometry 0.20, burstiness 0.18, detectgpt 0.14 (mirrors the top features of stylometric hybrid detectors). |
| `formatting_fingerprint` | Markdown/emoji/lead-ins | Counts markdown tokens, bullets, headers, emoji, suspicious unicode. |
| `watermark_resistance` | Generative watermarks | Reports `paraphrase_eligible_token_rate`; theoretical basis: SynthID/green-list signals are token-level statistical biases destroyed by synonym substitution + reordering. |

`overall_score(text)` → 0–100: classifier 0.24, stylometry 0.18, perplexity 0.16,
burstiness 0.16, ai_phrases 0.16, detectgpt 0.06, formatting 0.04.

`full_analysis(text)` → the per-method dict returned by `POST /v1/analyze`.

## v3.1 additions

- **Claim alignment** — beyond the bag-level check, each original sentence must retain at
  least 50% of its non-swappable content words (recall) in some output sentence. This
  catches whole-sentence meaning loss that bag comparison misses.
- **Active-scorer renormalization** — `overall_score` and `classifier_proxy` renormalize
  over scorers that are relevant to the text length, so short texts aren't dragged toward
  "human" (or "AI") by neutral placeholders. Short cliché-dense text now scores ~43
  (rewrites), a short human sample ~20 (gated).
- **Repair-only minimal path** — when text is clearly human (`score < 60%` of the gate),
  it is returned essentially untouched *except* deterministic grammar repairs
  (`REPAIR_PHRASES` + `repair_fragments`). Style never runs on human input; defects
  are always fixed.
- **Adaptive re-planning** — if all candidates fall outside the tier similarity window,
  two extra plans (gentler + deeper) are generated and re-ranked.
- **Register polish** — casual leakage into professional/academic output is rewritten
  back (`dig into → examine`, `tons of → many`, `super X → very X`).
- **Marker cap + opener dedup** — ≤2 marker-prefixed sentences per 100 words, and a final
  pass removes duplicate connective openers that scrubbing produced.
- **spaCy grammar gate** (`pos_grammar.py`, `USE_SPACY_GRAMMAR`) — verbless sentences,
  clause pile-ups, doubled determiners, tense-drift heuristics; regex fallback when
  disabled or unavailable.

## Hard guarantees (v2 rewrite)

1. **Meaning preservation** — every candidate rewrite passes `semantics.semantic_check`:
   numbers/quantities, negations, proper nouns, and content words must survive; nothing
   outside the engine's replacement vocabulary may be invented. On failure the pipeline
   retries at descending intensities (×0.7, ×0.45, ×0.25) and finally falls back to a
   minimal safe pass. Checked words exclude a `DROPPABLE` set (swap keys/outputs,
   connectives, markers — words the transforms legitimately replace).
2. **No artificial "human" mistakes** — no deliberate grammar errors, no random
   fragments (`repair_fragments` *fixes* verbless/dangling fragments deterministically),
   no filler sentences (`SHORT_TAGS` removed), no fake slang (slangy swaps are
   casual-register only), no arbitrary discourse markers (markers come from
   register-specific lists).
3. **Register ≠ intensity** — `preset` controls register (contraction factor, marker and
   aside lists, slang allowance) independently of `intensity`, which only scales amount.
4. **Formatting preservation** (`preserve_formatting=True` default) — markdown, bullets,
   headings, paragraph breaks, URLs, code spans/blocks, LaTeX, `(Author, year)` and
   `[12]` citations, and ALL-CAPS acronyms are protected via placeholder substitution
   before transforms and restored after. Sentence-level transforms run per paragraph
   (`map_paragraphs`) so structure never merges across lines. Set
   `preserve_formatting=false` for the legacy flatten-to-prose behavior.
5. **Idempotence** — text scoring below `HUMAN_SCORE_THRESHOLD` (default 35) gets only
   the safe low-tier rhythm pass; output never degrades across repeated passes
   (tests: `test_already_human_makes_minimal_changes`, `test_double_pass_does_not_destroy_prose`).
6. **Honest scoring** — the detector is labeled `internal_heuristic` everywhere it is
   exposed; `style_score` is advisory, not a validated AI detector.

## v3: structural rewriting & candidate ranking

At medium/high intensity the pipeline generates up to 4 candidates (base / structural /
deeper / lighter) and ranks them:

1. hard filters: `semantic_check` == 0 problems, `grammar.grammar_check` == 0,
   no lexical-diversity regression (`structure.lexical_diversity_regression`)
2. similarity inside the tier's target window (low 0.88–0.99, medium 0.80–0.95,
   high 0.72–0.93) — timid candidates (sim ≈ 1.0) are penalized
3. structural distance ≥ tier floor (low 0.03, medium 0.08, high 0.14) — distance is
   `structure.structural_distance`: sentence-length signature similarity, opener
   overlap, content-trigram overlap, subordinator-rate delta
4. lower internal style score wins ties

Structural transforms (enabled by the `structural` plan flag, medium+):
`flip_voice` (passive→active with irregular past map), `invert_one_of` ("X is one of
the Y" → "Among the Y is X"), `front_adverbial` ("A because B." → "Because B, A."),
high-tier `reorder_sentences` (anaphora-guarded adjacent swap).

`grammar.grammar_check` rejects: doubled words, banned collocations ("major to",
"put together decisions", …), stacked modals, em-dash rate > 3/1000, unbalanced
brackets/quotes, punctuation errors, verbless sentences (per comma-clause, via
stem-based `FINITE_VERB_RE`).

`FINITITE` note: verb detection is stem-based (+full -ize/-ate/-ify/-en forms);
extend the stem list in `transforms.py` when a real verb is flagged as verbless.

## Intensity tiers

| Tier | Intensity | What runs |
|---|---|---|
| low | 0–33 | AI-phrase/word scrubbing, contraction injection, light rhythm, fragment repair, punctuation |
| medium | 34–66 | + denominalization, structure patterns, synonym jitter, rare-word spice, sentence merge/split, opener diversification, register markers |
| high | 67–100 | + register asides, casual touches (casual only), heavier probabilities |

Calibrated similarity on raw AI prose: low ≈ 0.78, medium ≈ 0.70, high ≈ 0.61.

## Transform pipeline (`pipeline.humanize`, in order)

`prob = intensity / 100`. Each transform uses `rng` seeded by `seed` → deterministic.

0. `protect_spans` — code/URLs/citations/equations/acronyms → placeholders (restored at the end); when `preserve_formatting=false` only: `strip_emoji` + `strip_markdown`
1. `normalize_unicode` — homoglyph/zero-width/curly-quote normalization
2. `strip_casual_markers` (formal presets only, unconditional) — removes "Frankly,", "Honestly,", "Look," etc.
3. `scrub_ai_phrases(prob+0.25)` — `REPAIR_PHRASES` (always, prob 1.0: fixes artifacts like "it's major to") then `AI_PHRASES` template rules + `PASSIVE_FIXES`; recapitalizes
4. `scrub_ai_words(prob+0.2)` — `AI_WORDS` single-word swaps (delve→dig, leverage→use); proper nouns skipped
5. medium+: `denominalize` ("the integration of X into" → "integrating X into"), `break_structure_patterns` ("not just X but Y" → "X, but also Y"; tricolons only via "X, Y, and even Z" — never drops an item), `lexical_jitter` (protects `PROTECT_BIGRAMS` and proper nouns), `rare_word_spice` (register-gated)
6. `inject_contractions(c_prob)` — register-scaled (casual ×1.0, professional ×0.3, academic ×0.0)
7. `scrub_ai_phrases` again — catches phrases re-created by contraction injection
8. per paragraph: `repair_fragments` (verbless/dangling-fragment fixes, deterministic), `rewrite_burstiness` (merge <16-word pairs with varied joiners — never into a connective-initial sentence; split >18-word sentences only when the second clause has a subject+finite verb and the first keeps one), `diversify_openers`, `humanize_punctuation`, high-tier `inject_asides` (register list), medium+ `add_discourse_markers` (register list, never stacked)
9. casual+high: `casual_touches`; then `tidy` (whitespace/comma cleanup, connective-comma fix, capitalization), `restore_spans`

## Data tables (`resources.py`)

- `AI_WORDS` (~130 entries): single AI-favored word → 1–3 human alternatives
- `AI_PHRASES`: (compiled-regex, [templates]) — templates may use `{0}`, `{1}` for regex groups
- `SYNONYMS`, `RARE_SWAPS`: synonym tables (see 7–8)
- `CONTRACTIONS`: full→contracted map
- `PROTECT_BIGRAMS`: two-word technical phrases never split by synonym swaps
- `STRUCTURE_PATTERNS`: (regex, [templates]) for rhetorical AI patterns
- `REPAIR_PHRASES`: always-applied grammar repairs for legacy artifacts
- `MARKERS_CASUAL/MARKERS_FORMAL`, `ASIDES_CASUAL/ASIDES_FORMAL`: register-separated marker/aside lists
- `RARE_SWAPS_CASUAL_ONLY`: slangy swaps excluded from professional/academic
- `STOPWORDS`, `FUNCTION_WORDS`, `DROPPABLE_WORDS`: used by the semantic check and the function-word scorer
- `word_ranks()`: loads `data/wordfreq.txt` (top-10k English words, rank order) into a
  dict, lru_cached (~100 KB RAM)

## Known constraint

Aggressive rewrites frequently fail `semantic_check` and fall back — that is by design.
Benchmark on raw GPT-style prose: 67.6 → mean ~49 (meaning-preserving); the external
detector in development flagged this trade-off and the user requirement is explicit:
meaning preservation is a hard constraint, score improvement is secondary.

## Scoring calibration notes

- `_norm(v, lo, hi)` maps v into [0,1]; reversed ranges (hi < lo) invert.
  Several bug fixes here: reversed ranges previously returned 0, and the perplexity
  direction was initially inverted.
- Human calibration sample (`/tmp/sample_human.txt` in development): scores ~20.
  Raw AI benchmark samples: 58–76. Humanized: 22–46. If you tune scorer thresholds,
  re-run both and keep the gap.

## Determinism

Same `(text, preset, intensity, seed)` → same output. The API derives the seed from the
job UUID (low 32 bits) so results are reproducible from the job ID.
