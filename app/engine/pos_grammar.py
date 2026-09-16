import logging
import os

log = logging.getLogger("hoomanise.spacy")

_nlp = None
_tried = False

_SPACY_VERBS = set()


def _load():
    global _nlp, _tried
    if _tried:
        return _nlp
    _tried = True
    import os as _os

    if not _os.environ.get("USE_SPACY_GRAMMAR", "").strip().lower() in ("1", "true", "yes", "on"):
        return None
    try:
        import spacy

        _nlp = spacy.load("en_core_web_sm", exclude=["ner", "lemmatizer"])
        log.info("spaCy grammar enabled")
    except Exception as e:
        log.warning("spaCy unavailable, using regex grammar: %s", type(e).__name__)
        _nlp = None
    return _nlp


def pos_issues(text: str) -> list[str]:
    """Dependency/POS-based grammar issues. Empty when spaCy is disabled."""
    nlp = _load()
    if nlp is None:
        return []
    problems: list[str] = []
    for sent in _iter_sents(text, nlp):
        problems.extend(_check_sentence(sent))
    return problems


def _iter_sents(text, nlp):
    from .resources import split_sentences

    text = text[:4000]
    for s in split_sentences(text):
        if len(s.split()) < 3:
            continue
        yield s, nlp(s)


def _check_sentence(pair):
    s, doc = pair
    problems = []
    verbs = [t for t in doc if t.pos_ in ("VERB", "AUX")]
    if len([t for t in doc]) > 10 and not any(t.dep_ in ("ROOT", "advcl", "conj", "ccomp") and t.pos_ in ("VERB", "AUX") for t in doc):
        problems.append(f"verbless sentence: {s[:60]!r}")
    subjects = [t for t in doc if t.dep_ in ("nsubj", "nsubjpass")]
    finite_verbs = [t for t in doc if t.pos_ == "VERB" and t.tag_ in ("VBZ", "VBP", "VBD")]
    if subjects and finite_verbs and len(subjects) > len(finite_verbs) + 2:
        problems.append(f"clause pile-up: {s[:60]!r}")
    for i in range(len(doc) - 1):
        if doc[i].tag_ == "DT" and doc[i + 1].tag_ == "DT" and doc[i].text.lower() == doc[i + 1].text.lower() and doc[i].text.lower() in ("a", "an", "the"):
            problems.append(f"doubled determiner in: {s[:60]!r}")
    for tok in doc:
        if tok.dep_ == "nsubj" and tok.head.tag_ in ("VBG",) and tok.head.i == tok.i + 1:
            problems.append(f"possible tense drift: {tok.text} {tok.head.text} in {s[:50]!r}")
    return problems
