import random
import re

from .resources import (
    AI_PHRASES,
    AI_WORDS,
    ASIDES_CASUAL,
    ASIDES_FORMAL,
    CONNECTIVE_STARTS,
    CONTRACTIONS,
    MARKERS_CASUAL,
    MARKERS_FORMAL,
    MARKERS_HUMAN_OPENERS,
    NOMINALIZATION_FIXES,
    PASSIVE_FIXES,
    PROTECT_BIGRAMS,
    RARE_SWAPS,
    RARE_SWAPS_CASUAL_ONLY,
    STRUCTURE_PATTERNS,
    SYNONYMS,
    UNICODE_FIXES,
    WORD_RE,
    split_sentences,
)


def _apply_random(text, pattern, replacements, rng, prob):
    def sub(m):
        if rng.random() > prob:
            return m.group(0)
        tpl = rng.choice(replacements)
        try:
            out = tpl.format(*[g if g is not None else "" for g in m.groups()])
        except Exception:
            return m.group(0)
        return out

    return re.sub(pattern, sub, text, flags=re.IGNORECASE)


def _next_word(text: str, m) -> str:
    tail = text[m.end():].lstrip()
    return tail.split(" ", 1)[0].lower() if tail else ""


def _bigram_protected(text: str, m, lw: str) -> bool:
    return f"{lw} {_next_word(text, m)}" in PROTECT_BIGRAMS


def _hyphenated(text: str, m) -> bool:
    before = text[m.start() - 1] if m.start() > 0 else ""
    after = text[m.end()] if m.end() < len(text) else ""
    return before == "-" or after == "-"


def _sentence_start(text: str, start: int) -> bool:
    if start == 0:
        return True
    before = text[max(0, start - 2) : start]
    return bool(re.search(r"(?:^|[.!?]\s|\n)\s*$", text[:start])) or "\n" in before and before.strip() == ""


def _is_proper_noun(text: str, m) -> bool:
    w = m.group(0)
    if not w[0].isupper():
        return False
    return not _sentence_start(text, m.start())


def _recapitalize(text):
    def cap(m):
        return m.group(1) + m.group(2).upper()
    return re.sub(r"(^|[.!?]\s+|\n)([a-z])", cap, text)


def normalize_unicode(text):
    for k, v in UNICODE_FIXES.items():
        text = text.replace(k, v)
    return text


def strip_emoji(text):
    import app.engine.resources as res

    return res.EMOJI_RE.sub("", text)


def strip_markdown(text, rng, prob):
    lines = text.split("\n")
    out_lines = []
    list_buffer = []

    def flush_list():
        if not list_buffer:
            return
        items = list_buffer[:]
        list_buffer.clear()
        if len(items) == 1:
            out_lines.append(items[0])
            return
        starters = ["First, ", "Then, ", "Next, ", "After that, ", "Finally, "]
        for i, item in enumerate(items):
            starter = starters[min(i, len(starters) - 1)]
            cleaned = item[0].lower() + item[1:] if item and item[0].isupper() else item
            out_lines.append(starter + cleaned)
        if len(items) >= 2:
            if rng.random() < 0.5 and len(items) <= 4:
                pieces = [p.rstrip(".") for p in items]
                joined = pieces[0][0].upper() + pieces[0][1:] + ", plus " + ", ".join(pieces[1:-1]) + (" and " + pieces[-1] if len(pieces) > 2 else "") + "."
                out_lines.append(joined)

    for line in lines:
        stripped = line.rstrip()
        bullet = re.match(r"^\s*(?:[-*+•]|\d+[.)])\s+(.*)$", stripped)
        header = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if header:
            flush_list()
            h = header.group(1).strip().strip("*_")
            if h:
                lead = rng.choice(["Now, about {h}. ", "So, {h}. ", "First up: {h}. ", "Let's talk {h}. "])
                out_lines.append(lead.format(h=h.lower()))
            continue
        if bullet:
            item = bullet.group(1).strip()
            item = re.sub(r"\*\*([^*]+)\*\*", r"\1", item)
            if item and not item.endswith((".", "!", "?")):
                item += "."
            list_buffer.append(item)
            continue
        flush_list()
        line2 = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line2 = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", line2)
        line2 = re.sub(r"__([^_]+)__", r"\1", line2)
        line2 = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", line2)
        line2 = re.sub(r"`([^`]+)`", r"\1", line2)
        line2 = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line2)
        line2 = re.sub(r"^\s*>\s?", "", line2)
        line2 = re.sub(r"\|", ", ", line2)
        line2 = re.sub(r"^\s*[-*_]{3,}\s*$", "", line2)
        out_lines.append(line2)
    flush_list()
    return "\n".join(o for o in out_lines if o is not None)


def scrub_ai_phrases(text, rng, prob):
    from .resources import REPAIR_PHRASES
    for pat, reps in REPAIR_PHRASES:
        text = _apply_random(text, pat, reps, rng, 1.0)
    for pat, reps in AI_PHRASES:
        text = _apply_random(text, pat, reps, rng, prob)
    for pat, reps in PASSIVE_FIXES:
        text = _apply_random(text, pat, reps, rng, prob * 0.8)
    return _recapitalize(text)


def denominalize(text, rng, prob):
    for pat, reps in NOMINALIZATION_FIXES:
        text = _apply_random(text, pat, reps, rng, prob)
    return text


def strip_casual_markers(text, rng, prob):
    return re.sub(r"(^|[.!?]\s+|\n)(Frankly|Honestly|Look|To be fair|Plus)\b[,]?\s*", r"\1 ", text)


def scrub_ai_words(text, rng, prob):
    def sub(m):
        w = m.group(0)
        lw = w.lower()
        if lw not in AI_WORDS:
            return w
        if _hyphenated(text, m):
            return w
        if _is_proper_noun(text, m):
            return w
        if rng.random() > prob:
            return w
        rep = rng.choice(AI_WORDS[lw])
        if rep == lw:
            return w
        if m.start() == 0 or (m.start() > 0 and text[max(0, m.start() - 2) : m.start()].endswith(". ")):
            rep = rep[0].upper() + rep[1:]
        return rep

    pattern = re.compile(
        r"\b(" + "|".join(sorted((re.escape(k) for k in AI_WORDS), key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )
    return pattern.sub(sub, text)


def break_structure_patterns(text, rng, prob):
    for pat, reps in STRUCTURE_PATTERNS:
        text = _apply_random(text, pat, reps, rng, prob)
    tricolon = re.compile(r"\b([a-z]{3,}), ([a-z]{3,}), and ([a-z]{3,})\b")

    def sub_tri(m):
        if rng.random() > prob * 0.4:
            return m.group(0)
        a, b, c = m.group(1), m.group(2), m.group(3)
        return f"{a}, {b}, and even {c}"

    text = tricolon.sub(sub_tri, text)
    return text


def lexical_jitter(text, rng, prob):
    def sub(m):
        w = m.group(0)
        lw = w.lower()
        if lw not in SYNONYMS:
            return w
        if _bigram_protected(text, m, lw) or _hyphenated(text, m):
            return w
        if _is_proper_noun(text, m):
            return w
        if rng.random() > prob:
            return w
        rep = rng.choice(SYNONYMS[lw])
        if w[0].isupper():
            rep = rep[0].upper() + rep[1:]
        return rep

    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in SYNONYMS) + r")\b",
        re.IGNORECASE,
    )
    return pattern.sub(sub, text)


def rare_word_spice(text, rng, prob, preset):
    if preset == "academic":
        return text

    def sub(m):
        w = m.group(0)
        lw = w.lower()
        if lw not in RARE_SWAPS:
            return w
        if preset != "casual" and lw in RARE_SWAPS_CASUAL_ONLY or (
            preset != "casual" and rng.choice(RARE_SWAPS[lw]) in RARE_SWAPS_CASUAL_ONLY
        ):
            return w
        if _bigram_protected(text, m, lw) or _hyphenated(text, m):
            return w
        if _is_proper_noun(text, m):
            return w
        if rng.random() > prob:
            return w
        candidates = [r for r in RARE_SWAPS[lw] if preset == "casual" or r not in RARE_SWAPS_CASUAL_ONLY]
        if not candidates:
            return w
        rep = rng.choice(candidates)
        if w[0].isupper():
            rep = rep[0].upper() + rep[1:]
        return rep

    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in RARE_SWAPS) + r")\b",
        re.IGNORECASE,
    )
    return pattern.sub(sub, text)


def inject_contractions(text, rng, prob):
    if prob <= 0:
        return text
    for full, cont in CONTRACTIONS.items():
        pat = re.compile(r"\b" + re.escape(full) + r"\b(?=\s+to\b)" if full in ("we have", "you have", "they have") else r"\b" + re.escape(full) + r"\b", re.IGNORECASE)

        def sub(m, cont=cont):
            if rng.random() > prob:
                return m.group(0)
            return cont

        text = pat.sub(sub, text)
    return text


def humanize_punctuation(text, rng, prob):
    text = re.sub(r"\s*[—–]\s*", lambda m: rng.choice([", ", ". ", ", "]), text)

    def semi_sub(m):
        if rng.random() > prob:
            return m.group(0)
        nxt = m.group(1)
        if nxt and nxt[0].isupper():
            return ". " + nxt
        return ", " + nxt

    text = re.sub(r";\s*([A-Za-z`]*)", semi_sub, text)
    text = re.sub(r"\.{3,}", lambda m: ("..." if rng.random() < 0.5 else "."), text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:])(?=[A-Za-z])", r"\1 ", text)
    return text


CONNECTIVE_SPLIT_RE = re.compile(r", (?=(?:and|but|so|because|while|although) )|; ")
BARE_VERB_START_RE = re.compile(
    r"^\s*(?:enhance|simplify|identify|uncover|improve|make|take|enable|help|reduce|increase|deliver|drive|create|build|offer|inform|support|ensure|use)\b",
    re.IGNORECASE,
)
FINITE_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being|am|has|have|had|do|does|did|can|could|will|would|shall|should|may|might|must|got|getting|seems?|appears?|remains?)\b|\b(?:represent|constitut|involv|requir|demand|provid|deliver|remain|appear|becom|generat|produc|enabl|allow|mean|reflect|shape|driv|fuel|power|support|offer|carri|bring|leav|keep|turn|start|begin|end|continu|mov|work|play|serv|act|exist|depend|matter|benefit|build|develop|emerg|lead|point|refer|relat|stem|arise|occur|happen|come|go|get|give|take|show|look|seem|feel|sound|stay|stand|lie|sit|hold|us|need|want|help|grow|ris|fall|chang|affect|includ|cover|span|contain|differ|match|align|strengthen|weaken|improv|wors|accelerat|slow|open|clos|expand|reduc|increas|balanc|transform|adapt|respond|react|contribut|distribut|operat|function|perform|achiev|attain|reach|exceed|meet|fac|avoid|embrac|adopt|implement|integr|leverag|util|ensur|guarante|secur|protect|prevent|promot|advanc|simplifi|enhanc|identifi|uncov|analyz|organiz|say|tell|ask|answer|learn|teach|write|read|hear|see|watch|spend|sav|earn|pay|cost|buy|sell|trad|invest|compet|win|los|succeed|fail|attempt|try|decid|choose|prefer|combine|combin|connect|communicat|collaborat|cooperat|share|send|receiv|accept|reject|argu|agree|disagre|claim|suggest|recommend|propose|report|reveal|disclos|confirm|deny|doub|explain|describ|defin|clarifi|illustrat|demonstrat|prove|justifi|justify|measur|estimat|calculat|predict|forecast|assum|infer|impli|indicate|signifi|suggest|influenc|impact|alter|shift|drive)(?:s|es|ed)?\b|\b(?:simplify|utilize|curate|optimize|streamline|facilitate|diversify|notify|amplify|clarify|justify|satisfy|classify|qualify|modify|verify|intensify|realize|recognize|organize|prioritize|maximize|minimize|emphasize|authorize|analyze|summarize|criticize|customize|standardize|categorize|revolutionize|digitize|educate|communicate|cooperate|collaborate|demonstrate|illustrate|duplicate|indicate|dedicate|activate|motivate|evaluate|calculate|articulate|accommodate|celebrate|tolerate|navigate|cultivate|elevate|alleviate|mitigate|consolidate|formulate|regulate|speculate|translate|dominate|resonate|originate|fascinate|concentrate|compensate|accumulate|accelerate|escalate|nominate|donate|vibrate|widen|deepen|broaden|quicken|soften|threaten|lighten|harden|shorten|lengthen|handle|tackle|strengthen|converge|diverge|submerge|come|comes|came|flag|flags|flagged|solve|resolve|dissolve|involve|revolve|absolve|devolve)(?:s|es|ed|d)?\b",
    re.IGNORECASE,
)


def _split_clause_ok(second: str) -> bool:
    if not FINITE_VERB_RE.search(second):
        return False
    if BARE_VERB_START_RE.match(second):
        return False
    return True


def rewrite_burstiness(text, rng, prob):
    sents = split_sentences(text)
    if len(sents) < 3:
        return text
    out = []
    i = 0
    while i < len(sents):
        s = sents[i]
        wcount = len(WORD_RE.findall(s))
        if (
            i + 1 < len(sents)
            and wcount < 16
            and len(WORD_RE.findall(sents[i + 1])) < 16
            and not re.match(r"^(?:However|Moreover|Furthermore|Additionally|Also|Plus|But|And|So|Still|Yet|Ultimately|Meanwhile|Besides|In conclusion|On top of that|In the end|All in all|That said|In practice|To close|Wrapping up)\b", sents[i + 1], re.IGNORECASE)
            and rng.random() < prob
        ):
            joiner = rng.choice([", and ", ", but ", ", so ", ", though "])
            merged = s.rstrip(".!?") + joiner + sents[i + 1][0].lower() + sents[i + 1][1:]
            out.append(merged)
            i += 2
            continue
        if wcount > 18 and rng.random() < prob:
            pieces = None
            for m in CONNECTIVE_SPLIT_RE.finditer(s):
                second = s[m.end():]
                if len(WORD_RE.findall(second)) > 5 and _split_clause_ok(second):
                    pieces = [s[: m.start()], second]
                    break
            if (
                pieces
                and len(WORD_RE.findall(pieces[0])) > 6
                and FINITE_VERB_RE.search(pieces[0])
            ):
                first = pieces[0].rstrip(",") + "."
                second = pieces[1][0].upper() + pieces[1][1:]
                out.append(first)
                out.append(second if second.endswith((".", "!", "?")) else second + ".")
                i += 1
                continue
        out.append(s)
        i += 1
    return " ".join(out)


STARTER_WORDS = {
    "also", "plus", "and", "but", "so", "still", "yet", "now", "then",
    "honestly", "look", "frankly", "that", "this", "it", "the", "a", "an", "if", "say",
}

CONNECTIVE_SYNONYMS = {
    "also": ["plus", "and", "on top of that"],
    "plus": ["also", "and", "besides"],
    "and": ["plus", "also"],
    "moreover": ["also", "plus"],
    "furthermore": ["also", "plus"],
    "additionally": ["plus", "on top of that"],
    "but": ["still", "yet"],
    "however": ["still", "but"],
    "so": ["and so", "that's why"],
}

CONNECTIVE_FIRST = {
    "moreover", "furthermore", "additionally", "also", "plus", "and", "but", "so",
    "however", "therefore", "consequently", "still", "yet", "then", "besides", "meanwhile",
}


def diversify_openers(text, rng, prob):
    text = re.sub(r"\b(And|But|So|Plus|Still|Yet|Now|Also),?\s+(?:also|plus|moreover|furthermore|additionally|further)\b[,:]?\s*", r"\1, ", text, flags=re.IGNORECASE)
    sents = split_sentences(text)
    out = []
    last_starts = []
    for s in sents:
        m = CONNECTIVE_STARTS.match(s)
        if m and rng.random() < prob:
            s = s[m.end():]
            s = s[0].lower() + s[1:] if s and s[0].isupper() and (len(s) < 2 or s[1].islower()) else s
        ws = s.split()
        start = ws[0].lower().strip(",.;:!?\"'") if ws else ""
        if (
            start in [x.lower().strip(",.;:!?\"'") for x in last_starts[-3:]]
            and len(ws) > 3
            and rng.random() < prob
        ):
            if start in CONNECTIVE_SYNONYMS and rng.random() < 0.8:
                used = {x.lower().strip(",.;:!?") for x in last_starts[-3:]}
                options = [o for o in CONNECTIVE_SYNONYMS[start] if o not in used] or CONNECTIVE_SYNONYMS[start]
                rep = rng.choice(options)
                rest = s[len(ws[0]):]
                s = rep[0].upper() + rep[1:] + "," + (rest if rest.startswith(" ") else " " + rest.lstrip(", "))
                last_starts.append(rep)
            elif start not in STARTER_WORDS:
                prefix = rng.choice(MARKERS_HUMAN_OPENERS)
                s = prefix + " " + s[0].lower() + s[1:]
                last_starts.append(prefix)
            else:
                last_starts.append(ws[0])
        else:
            last_starts.append(ws[0] if ws else "")
        out.append(s)
    return " ".join(out)


_ALL_ASIDES = ASIDES_CASUAL + ASIDES_FORMAL


def inject_asides(text, rng, prob, asides):
    if not asides or prob <= 0:
        return text
    sents = split_sentences(text)
    out = []
    for s in sents:
        if any(a.strip(", ") in s for a in _ALL_ASIDES):
            out.append(s)
            continue
        if len(WORD_RE.findall(s)) > 14 and rng.random() < prob:
            anchor = None
            for m in re.finditer(r",\s", s):
                if m.start() > 15 and len(WORD_RE.findall(s[m.end():])) > 5:
                    anchor = m.end()
                    break
            aside = rng.choice(asides).strip()
            if aside.startswith(","):
                aside = aside[1:].strip()
            if anchor:
                s = s[:anchor] + aside + ", " + s[anchor:]
            else:
                s = s.rstrip(".!?") + ", " + aside + "."
        out.append(s)
    return " ".join(out)


MARKER_SKIP_STARTS = (
    "Honestly", "Look", "Now", "That said", "Still", "Plus", "Frankly", "To be fair",
    "Lately", "These days", "Right now", "Also", "And", "But", "So", "Yet", "First", "Then", "Granted",
    "In practice", "Even so", "Broadly",
)


def add_discourse_markers(text, rng, prob, markers):
    if not markers or prob <= 0:
        return text
    sents = split_sentences(text)
    out = []
    rate = prob * 0.5
    all_markers = set(markers) | {"Honestly,", "Look,", "That said,", "In practice,", "Even so,", "Plus,", "Still,", "To be fair,"}
    connector_phrases = ("on top of that", "all in all", "in short", "at the end of the day", "in the end", "by the way")
    eligible = [
        i for i, s in enumerate(sents)
        if len(WORD_RE.findall(s)) > 12
        and not s.startswith(MARKER_SKIP_STARTS)
        and not any(m.strip(",").lower() in s[:60].lower() for m in all_markers)
        and not any(ph in s[:60].lower() for ph in connector_phrases)
    ]
    chosen = set(rng.sample(eligible, max(1, int(len(sents) * rate * 0.2)))) if eligible else set()
    for i, s in enumerate(sents):
        first = s.split()[0].lower().strip(",.;:!?") if s.split() else ""
        if i in chosen and not s.startswith(MARKER_SKIP_STARTS) and first not in CONNECTIVE_FIRST:
            s = rng.choice(markers) + " " + s[0].lower() + s[1:]
        out.append(s)
    return " ".join(out)


def casual_touches(text, rng, prob):
    def sub(m):
        if rng.random() > prob * 0.4:
            return m.group(0)
        return "kind of"

    text = re.sub(r"\bsomewhat\b|\brather\b", sub, text, flags=re.IGNORECASE)
    text = re.sub(r"\bvery (\w+)", lambda m: ("super " + m.group(1) if rng.random() < prob * 0.3 else m.group(0)), text)
    return text


REGISTER_FIXES = [
    (r"\b(?:dig into|get into|dig through)\b", ["examine", "look at"]),
    (r"\b(?:tons of|loads of)\b", ["many", "plenty of"]),
    (r"\b(?:kick off|get going|get rolling|kick things off)\b", ["begin", "start"]),
    (r"\b(?:dirt-cheap|spendy)\b", ["inexpensive"]),
    (r"\b(?:kooky|off-kilter)\b", ["odd"]),
    (r"\b(?:mind-numbing|dry as dust)\b", ["tedious"]),
    (r"\b(?:wiped|running on fumes|slammed|swamped)\b", ["overloaded"]),
    (r"\b(?:miffed|bummed|spooked|tickled|over the moon)\b", ["pleased"]),
    (r"\b(?:whopping|hefty|puny|teeny)\b", ["substantial"]),
    (r"\bdownright\b", ["quite"]),
    (r"\bsuper (\w+)\b", ["very {0}"]),
    (r"\bkind of\b", ["somewhat"]),
    (r"\bmove\b", ["sell"]),
]


def repair_only(text, rng):
    from .resources import REPAIR_PHRASES

    for pat, reps in REPAIR_PHRASES:
        text = _apply_random(text, pat, reps, rng, 1.0)
    text = repair_fragments(text, rng)
    return tidy(text)


def register_polish(text, rng, prob, preset):
    if preset == "casual":
        return text
    rate = 1.0 if preset == "academic" else 0.85
    for pat, reps in REGISTER_FIXES:
        text = _apply_random(text, pat, reps, rng, prob * rate)
    return text


_ALL_MARKER_WORDS = None


def _marker_words():
    global _ALL_MARKER_WORDS
    if _ALL_MARKER_WORDS is None:
        from .resources import MARKERS_CASUAL, MARKERS_FORMAL

        _ALL_MARKER_WORDS = tuple(
            m.strip(",") for m in list(MARKERS_CASUAL) + list(MARKERS_FORMAL)
        )
    return _ALL_MARKER_WORDS


def cap_markers(text, max_per_100: float = 2.0):
    sents = split_sentences(text)
    n_words = max(1, len(WORD_RE.findall(text)))
    allowed = max(1, round(n_words / 100 * max_per_100))
    marker_words = _marker_words()
    marked = [i for i, s in enumerate(sents) if s.split() and s.split()[0].strip(",") in marker_words]
    excess = len(marked) - allowed
    if excess <= 0:
        return text
    for i in marked[-excess:] if excess < len(marked) else marked:
        s = sents[i]
        first = s.split()[0]
        if first.strip(",") in marker_words:
            rest = s[len(first):]
            s = rest.lstrip() 
            s = s[0].lower() + s[1:] if s and s[0].isupper() else s
            sents[i] = s
    return _recapitalize(" ".join(sents))

_PROTECT_PATTERNS = None


def _protect_patterns():
    global _PROTECT_PATTERNS
    if _PROTECT_PATTERNS is None:
        _PROTECT_PATTERNS = [
            re.compile(r"```.*?```", re.S),
            re.compile(r"`[^`\n]+`"),
            re.compile(r"\$\$[^$]+\$\$|\$[^$\n]+\$"),
            re.compile(r"https?://\S+"),
            re.compile(r"\[[^\]]+\]\([^)]+\)"),
            re.compile(r"\(\s*[A-Z][A-Za-z]+(?: et al\.?)?,?\s+\d{4}[a-z]?\s*\)"),
            re.compile(r"\[\d{1,3}(?:,\s*\d{1,3})*\]"),
            re.compile(r"\b[A-Z]{2,}\b"),
        ]
    return _PROTECT_PATTERNS


def protect_spans(text):
    spans = []
    for pat in _protect_patterns():
        def sub(m):
            spans.append(m.group(0))
            return f"\x00{len(spans) - 1}\x00"
        text = pat.sub(sub, text)
    return text, spans


def restore_spans(text, spans):
    for i, span in enumerate(spans):
        text = text.replace(f"\x00{i}\x00", span)
    return text


_PARA_SPLIT_RE = re.compile(r"(\n\n+|\n)")

CONTRAST_OPEN_RE = re.compile(r"^(?:However|That said|Still|But|Meanwhile|On the other hand|Even so)\b")


def split_paragraphs(text, rng, prob):
    parts = _PARA_SPLIT_RE.split(text)
    out = []
    for part in parts:
        if part and not _PARA_SPLIT_RE.fullmatch(part):
            sents = split_sentences(part)
            if len(sents) >= 4 and rng.random() < prob:
                for i in range(1, len(sents)):
                    if CONTRAST_OPEN_RE.match(sents[i]) and len(WORD_RE.findall(sents[i])) > 4:
                        head = " ".join(sents[:i])
                        tail = " ".join(sents[i:])
                        out.append(head)
                        out.append("\n\n")
                        out.append(tail)
                        part = None
                        break
            if part is not None:
                out.append(part)
        else:
            out.append(part)
    return "".join(out)


def map_paragraphs(text, fn):
    parts = _PARA_SPLIT_RE.split(text)
    out = []
    for part in parts:
        if part and not _PARA_SPLIT_RE.fullmatch(part):
            out.append(fn(part))
        else:
            out.append(part)
    return "".join(out)


IRREGULAR_PAST = {
    "written": "wrote", "made": "made", "built": "built", "given": "gave",
    "taken": "took", "seen": "saw", "shown": "showed", "known": "knew",
    "found": "found", "held": "held", "sent": "sent", "set": "set",
    "put": "put", "done": "did", "said": "said", "told": "told",
    "brought": "brought", "bought": "bought", "thought": "thought",
    "caught": "caught", "taught": "taught", "left": "left", "felt": "felt",
    "kept": "kept", "meant": "meant", "met": "met", "paid": "paid",
    "lost": "lost", "sold": "sold", "run": "ran", "risen": "rose",
    "driven": "drove", "eaten": "ate", "fallen": "fell", "forgotten": "forgot",
    "hidden": "hid", "broken": "broke", "chosen": "chose", "drawn": "drew",
    "grown": "grew", "thrown": "threw", "worn": "wore", "born": "bore",
    "spoken": "spoke", "stolen": "stole", "begun": "began", "won": "won",
    "held": "held", "understood": "understood", "heard": "heard",
}

PASSIVE_BY_RE = re.compile(
    r"\b([A-Z]?[\w][\w\s,]{1,60}?)\s+"
    r"(?:(?:is|are|was|were)\s+|(?:has|have|had)\s+been\s+)"
    r"([a-z]+(?:ed|ne|wn|de|un|it|ut|ilt|ten|ade|one|aid|old|eed))\s+by\s+"
    r"([\w][\w\s]{1,40}?)\s*(?=[.,;])",
    re.IGNORECASE,
)


def flip_voice(text, rng, prob):
    def sub(m):
        if rng.random() > prob:
            return m.group(0)
        subject, participle, agent = m.group(1).strip(), m.group(2).lower(), m.group(3).strip()
        if " by " in f" {agent} " or len(agent.split()) > 6:
            return m.group(0)
        past = IRREGULAR_PAST.get(participle, participle)
        if past == participle and not participle.endswith("ed"):
            return m.group(0)
        out = f"{agent} {past} {subject}"
        if m.group(0)[:1].isupper():
            out = out[0].upper() + out[1:]
        words = out.split(" ")
        if len(words) > 2:
            det = words[-1] if len(words[-1].split()) == 1 else words[-1]
            for k in range(len(words) - 1, 1, -1):
                w = words[k]
                if w in ("The", "A", "An", "This", "That", "These", "Those", "His", "Her", "Their", "Its", "My", "Our", "Your") and k + 1 < len(words) and words[k + 1][:1].islower():
                    words[k] = w.lower()
                elif w[:1].isupper() and w[1:].islower() and k > 2 and not (words[k - 1] in ".!?" or words[k - 1].endswith((".", "!?"))):
                    if k == len(words) - 1 and words[k - 1] not in (".", "!", "?"):
                        words[k] = w.lower()
            out = " ".join(words)
        return out

    return PASSIVE_BY_RE.sub(sub, text)


ADVERBIAL_FRONT_RE = re.compile(
    r"^(.{15,}?)(?:,)?\s+(because|since|although|though|even though)\s+([^.!?]+[.!?])$",
    re.IGNORECASE,
)


def front_adverbial(text, rng, prob):
    sents = split_sentences(text)
    out = []
    for s in sents:
        m = ADVERBIAL_FRONT_RE.match(s)
        if m and rng.random() < prob:
            main, conj, clause = m.group(1).strip().rstrip(","), m.group(2).lower(), m.group(3).strip().rstrip(".!?")
            if len(WORD_RE.findall(clause)) > 3 and len(WORD_RE.findall(main)) > 3:
                main_low = main[0].lower() + main[1:] if main[:1].isupper() and not main.isupper() else main
                s = f"{conj[0].upper()}{conj[1:]} {clause}, {main_low}."
        out.append(s)
    return " ".join(out)


ONE_OF_RE = re.compile(
    r"\b([\w][\w\s]{2,50}?)\s+(is|are)\s+one of the\s+([\w][\w\s]{2,44})\b",
    re.IGNORECASE,
)


def invert_one_of(text, rng, prob):
    def sub(m):
        if rng.random() > prob:
            return m.group(0)
        subj, verb, rest = m.group(1).strip(), m.group(2).lower(), m.group(3).strip()
        if subj[:1].isupper() and not subj.isupper():
            subj = subj[0].lower() + subj[1:]
        out = f"Among the {rest} {verb} {subj}"
        if m.group(0)[:1].isupper():
            out = out[0].upper() + out[1:]
        return out

    return ONE_OF_RE.sub(sub, text)


ANAPHORA_RE = re.compile(r"^(?:This|That|These|Those|It|They|He|She|His|Her|Their|Its|Such)\b")
PRONOUN_RE = re.compile(
    r"\b(?:it|its|they|them|their|he|she|his|her|him|this|that|these|those|which|who)\b",
    re.IGNORECASE,
)


def reorder_sentences(text, rng, prob):
    sents = split_sentences(text)
    if len(sents) < 3:
        return text
    for i in range(len(sents) - 1):
        a, b = sents[i], sents[i + 1]
        if rng.random() < prob and not ANAPHORA_RE.match(b) and not ANAPHORA_RE.match(a):
            if PRONOUN_RE.search(" ".join(b.split()[:6])):
                continue
            conj_b = re.match(r"^(?:However|Moreover|Furthermore|Also|Plus|But|And|So|Still|Yet|Then|Finally|Ultimately)\b", b, re.IGNORECASE)
            if conj_b:
                continue
            sents[i], sents[i + 1] = b, a
            break
    return " ".join(sents)


BARE_VERBS = {
    "enhance", "improve", "increase", "reduce", "drive", "boost", "streamline",
    "optimize", "make", "take", "enable", "help", "ensure", "create", "build",
    "offer", "inform", "support", "deliver", "identify", "uncover", "use",
}


def repair_fragments(text, rng):
    sents = split_sentences(text)
    if not sents:
        return text

    def has_verb(s):
        return bool(FINITE_VERB_RE.search(s))

    repaired = []
    i = 0
    while i < len(sents):
        s = sents[i].strip()
        if not s:
            i += 1
            continue
        nxt = sents[i + 1].strip() if i + 1 < len(sents) else None

        # trailing dangling verb-pair: "..., simplify processes, enhance efficiency."
        m = re.search(r", ([a-z]+) ([a-z][a-z\s]{2,40})[.]?$", s)
        if (
            m
            and m.group(1) in BARE_VERBS
            and " and " not in m.group(0)
            and has_verb(s[: m.start()])
        ):
            s = s[: m.start()] + ", and " + m.group(1) + " " + m.group(2)
            if s and s[-1] not in ".!?":
                s += "."
            repaired.append(s)
            i += 1
            continue

        # leading bare-verb fragment: "And make more informed decisions." -> attach to previous
        m2 = re.match(r"^(?:And|Plus|Also)\s+([a-z]+) ([a-z][a-z\s]{2,40})[.?!]?$", s)
        if (
            m2
            and m2.group(1) in BARE_VERBS
            and repaired
            and not s.endswith((",", ";"))
        ):
            prev = repaired[-1]
            if prev[-1] in ".!?":
                prev = prev[:-1]
            repaired[-1] = prev + ", and " + s[0].lower() + s[1:]
            if repaired[-1][-1] not in ".!?":
                repaired[-1] += "."
            i += 1
            continue

        # verbless list fragment with commas: "Issues such as A, B, job displacement." -> attach to next
        if (
            ", " in s
            and not has_verb(s)
            and nxt is not None
            and FINITE_VERB_RE.search(nxt)
            and not re.match(r"^(?:However|Moreover|Furthermore|Still|Yet|Meanwhile|Nevertheless)\b", nxt, re.IGNORECASE)
        ):
            nxt_body = re.sub(r"^(?:And|Plus|Also)\b[,:]?\s*", "", nxt)
            joined = s.rstrip(".!?") + ", and " + nxt_body
            repaired.append(joined)
            i += 2
            continue

        repaired.append(s)
        i += 1
    return " ".join(repaired)


OPENERS_DEDUP = ("also", "plus", "and", "but", "so", "still", "yet", "however", "moreover",
                 "furthermore", "additionally", "consequently", "besides", "meanwhile", "ultimately")


def dedup_openers(text, rng):
    sents = split_sentences(text)
    seen = set()
    for i, s in enumerate(sents):
        parts = s.split()
        if not parts:
            continue
        first = parts[0].strip(",.;:!?\"'").lower()
        if first in OPENERS_DEDUP and first in seen:
            options = [o for o in CONNECTIVE_SYNONYMS.get(first, []) if o not in seen and o != first]
            if options:
                rep = rng.choice(options)
                rest = s[len(parts[0]):]
                sents[i] = rep[0].upper() + rep[1:] + "," + (rest if rest.startswith(" ") else " " + rest.lstrip(", "))
                seen.add(rep.lower().strip(","))
                continue
        seen.add(first)
    return " ".join(sents)


def tidy(text):
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^[ \t]+|[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"(^|[.!?]\s+|\n)((?:Also|Plus|Moreover|However|Besides|Meanwhile|Still|Now|Ultimately|Overall|In fact)\b)(\s+)(?=[A-Z\x00])",
        r"\1\2, ",
        text,
    )
    text = re.sub(
        r"\b(and|but|so|yet|still|also|plus|And|But|So|Yet|Still|Also|Plus),?\s+((?:and|but|so|yet|still|also|plus|And|But|So|Yet|Still|Also|Plus|However|Moreover|Furthermore|Ultimately|Besides|Meanwhile)\b)[,:]?\s*",
        r"\1 ",
        text,
    )
    if "\n" not in text:
        sents = split_sentences(text)
        rebuilt = []
        for s in sents:
            s = s.strip()
            if s:
                rebuilt.append(s[0].upper() + s[1:] if s[0].islower() else s)
        text = " ".join(rebuilt)
    text = re.sub(r"(?:, |(?<=[.!?])\s)((?:And|But|So|Plus|Still|Yet)\b)(?=[\s,])", lambda m: m.group(0)[:1].replace(",", "") + m.group(1).lower(), text)
    text = re.sub(r", (and|but|so|plus|still|yet)(?=,)", r", \1", text)

    _LOWER_SAFE = {
        "Don't", "Doesn't", "Didn't", "Won't", "Can't", "Shouldn't", "Wouldn't",
        "Couldn't", "Isn't", "Aren't", "Wasn't", "Weren't", "Hasn't", "Haven't",
        "This", "That", "It", "We", "They", "You", "Our", "Their", "My", "His",
        "Her", "Its", "There", "These", "Those", "Then", "The", "A", "An", "If",
        "Everyone", "People", "Teams", "Users", "Companies", "Businesses",
        "By", "In", "At", "On", "From", "With", "For", "As", "When", "While",
        "Society", "Organizations", "Businesses", "Companies", "People",
        "Developers", "Teams", "Users", "Customers", "Technology", "Systems",
        "Tools", "Data", "Services", "Products", "Markets", "Leaders",
    }

    def _lower_common(m):
        w = m.group(2)
        if w == "I":
            return m.group(0)
        if w in _LOWER_SAFE:
            return m.group(1) + w.lower()
        if w.endswith(("ing", "ed")):
            from .resources import word_ranks

            if w.lower() in word_ranks():
                return m.group(1) + w.lower()
        return m.group(0)

    text = re.sub(r"(, (?:and|but|so|plus|though) )([A-Z][a-z']{1,20})(?=\b)", _lower_common, text)
    text = re.sub(r"\bi\b", "I", text)
    return _recapitalize(text).strip()
