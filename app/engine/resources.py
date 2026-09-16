import os
import re
from functools import lru_cache

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

AI_WORDS = {
    "delve": ["dig", "get into", "dig into"],
    "delves": ["digs", "gets into"],
    "delving": ["digging", "getting into"],
    "tapestry": ["mix", "patchwork"],
    "realm": ["area", "field", "space"],
    "landscape": ["scene", "market", "setup"],
    "leverage": ["use", "tap", "put to work"],
    "leverages": ["uses", "taps"],
    "leveraging": ["using", "tapping"],
    "utilize": ["use"],
    "utilizes": ["uses"],
    "utilizing": ["using"],
    "utilization": ["use"],
    "facilitate": ["help", "make easier"],
    "facilitates": ["helps"],
    "underscore": ["show", "make clear"],
    "underscores": ["shows", "makes clear"],
    "underscoring": ["showing"],
    "testament": ["proof", "sign"],
    "pivotal": ["key", "central"],
    "multifaceted": ["layered", "many-sided"],
    "holistic": ["whole-picture", "all-around"],
    "robust": ["solid", "sturdy", "hardy"],
    "seamless": ["smooth"],
    "seamlessly": ["smoothly"],
    "foster": ["build", "grow", "nurture"],
    "fosters": ["builds", "grows"],
    "fostering": ["building", "growing"],
    "navigate": ["work through", "deal with", "handle"],
    "navigating": ["working through", "dealing with"],
    "embark": ["start", "set out"],
    "unlock": ["open up", "get at"],
    "elevate": ["lift", "raise", "boost"],
    "harness": ["use", "put to work"],
    "paramount": ["critical", "key"],
    "quintessential": ["classic", "textbook"],
    "meticulously": ["carefully"],
    "meticulous": ["careful"],
    "comprehensively": ["thoroughly", "fully"],
    "comprehensive": ["thorough", "full"],
    "innovative": ["fresh", "new"],
    "transformative": ["game-changing", "big"],
    "synergy": ["teamwork", "combined effect"],
    "paradigm": ["model", "framework"],
    "beacon": ["guide", "marker"],
    "captivating": ["gripping", "striking"],
    "breathtaking": ["stunning", "striking"],
    "awe-inspiring": ["stunning"],
    "unwavering": ["steady", "firm"],
    "steadfast": ["steady", "firm"],
    "astute": ["sharp", "shrewd"],
    "profound": ["deep"],
    "invaluable": ["hugely useful", "worth its weight"],
    "indispensable": ["must-have", "essential"],
    "flourish": ["thrive", "do well"],
    "endeavor": ["effort", "project"],
    "commence": ["start", "begin"],
    "ascertain": ["find out", "work out"],
    "elucidate": ["clarify", "spell out"],
    "augment": ["add to", "boost"],
    "bolster": ["shore up", "support"],
    "culminate": ["end up", "wrap up"],
    "delineate": ["sketch out", "lay out"],
    "disseminate": ["spread", "share"],
    "exemplify": ["show", "typify"],
    "galvanize": ["spark", "rally"],
    "inundate": ["flood", "swamp"],
    "pertinent": ["relevant", "on point"],
    "salient": ["key", "standing out"],
    "commendable": ["admirable", "solid"],
    "noteworthy": ["worth a mention", "notable"],
    "versatile": ["flexible", "handy"],
    "notable": ["striking", "worth a mention"],
    "crucial": ["key", "critical"],
    "vital": ["key", "critical"],
    "essential": ["key", "necessary"],
    "furthermore": ["also", "plus", "on top of that"],
    "moreover": ["also", "plus", "on top of that"],
    "additionally": ["also", "plus"],
    "consequently": ["so", "as a result"],
    "subsequently": ["later", "after that"],
    "nevertheless": ["still", "even so"],
    "notwithstanding": ["despite that", "still"],
    "whilst": ["while"],
    "hence": ["so", "that's why"],
    "thus": ["so", "that way"],
    "therefore": ["so"],
    "individuals": ["people"],
    "numerous": ["plenty of", "a lot of"],
    "various": ["a few different", "all sorts of"],
    "significant": ["big", "real", "marked"],
    "substantial": ["sizeable", "big"],
    "ensure": ["make sure"],
    "ensures": ["makes sure"],
    "ensuring": ["making sure"],
    "optimize": ["tune", "sharpen"],
    "streamline": ["tighten", "simplify"],
    "empower": ["back", "arm", "let"],
    "resonate": ["hit home", "land"],
    "resonates": ["hits home"],
    "vibrant": ["lively", "loud"],
    "bustling": ["busy"],
    "meticulousness": ["care"],
    "encompasses": ["covers", "spans"],
    "encompass": ["cover", "span"],
    "showcase": ["show off", "put on display"],
    "unleash": ["let loose", "set off"],
    "amplify": ["crank up", "boost"],
    "compelling": ["gripping", "strong"],
    "invaluablely": ["hugely usefully"],
    "garner": ["gather", "pick up"],
    "spearhead": ["lead"],
    "tailored": ["custom", "made-to-fit"],
    "bespoke": ["custom", "made-to-fit"],
    "cutting-edge": ["the latest", "modern", "advanced"],
    "state-of-the-art": ["top-tier", "latest"],
    "groundbreaking": ["game-changing", "new"],
    "revolutionize": ["shake up", "remake"],
    "revolutionizing": ["shaking up"],
    "game-changer": ["big deal", "shake-up"],
    "realm-of": ["in"],
}

REPAIR_PHRASES = [
    (r"\b(?:it's|it is) major to (?:recognize|understand|acknowledge|note|see) that\s*", ["we have to accept that ", "the truth is that "]),
    (r"\b(?:it's|it is) major to\b", ["it really matters to", "we really need to"]),
]

AI_PHRASES = [
    (r"\bit(?:'s| is) (?:important|worth) (?:noting|to note|mentioning) that\s*", ["keep in mind, ", "worth remembering: ", ""]),
    (r"\bit should (?:be )?noted that\s*", ["keep in mind, ", ""]),
    (r"\bin today'?s ((?:[a-z-]+ )*?)(world|landscape|society|market|era|environment)\b,?\s*", ["these days, in the {0}{1}, ", "lately, in this {0}{1}, "]),
    (r"\bin today'?s (world|landscape|society|market|era|environment)\b,?\s*", ["these days, ", "right now, "]),
    (r"\bin the (?:digital|modern|current) (?:age|era|landscape)\b,?\s*", ["these days, ", "today, "]),
    (r"\bin the realm of\b", ["in", "when it comes to"]),
    (r"\bat the end of the day\b", ["at the end of the day", "when all's said and done"]),
    (r"\bin conclusion\b,?\s*", ["so, all in all, ", "wrapping up, ", "to close, "]),
    (r"\bin summary\b,?\s*", ["so, in short, ", "to sum up, "]),
    (r"\bit goes without saying that\s*", ["needless to say, ", ""]),
    (r"\bplays? a (?:crucial|vital|pivotal|key|significant) role in\b", ["does a lot of the work in", "matters a lot for", "is central to"]),
    (r"\ba testament to\b", ["proof of", "a sign of"]),
    (r"\bfoster (growth|innovation|creativity|collaboration)\b", ["fuel {0}", "drive {0}", "spark {0}"]),
    (r"\bthe (?:myriad|plethora) of\b", ["all the", "the range of"]),
    (r"\bdelve into\b", ["dig into", "get into", "dig through"]),
    (r"\bembark on (?:a|the|an) [a-z]+ (?:journey|adventure)\b", ["get started", "kick things off"]),
    (r"\bunlock (?:the )?(?:full )?potential of\b", ["get more out of", "make the most of"]),
    (r"\bnavigate the (?:complexities|challenges|nuances) of\b", ["work through", "deal with", "handle"]),
    (r"\bwhen it comes to the (?:topic|subject|question) of\b", ["on", "about"]),
    (r"\bit is (?:widely|generally) (?:acknowledged|accepted|known) that\s*", ["most people agree, ", "everyone knows, "]),
    (r"\bone might argue that\s*", ["you could argue ", "some would say "]),
    (r"\bit can (?:be seen|be argued) that\s*", ["you could say ", "arguably, "]),
    (r"\bthe (?:ever-evolving|ever-changing|rapidly evolving) (?:landscape|world|nature) of\b", ["how fast", "the pace of"]),
    (r"\b(?:move|look) (?:forward|ahead) to a (?:bright|better|promising) future\b", ["see where this goes", "watch this space"]),
    (r"\bas we (?:move|look) (?:forward|ahead)\b,?\s*", ["going forward, ", "from here, "]),
    (r"\bin the grand (?:scheme|tapestry) of\b", ["overall, in", "big-picture, in"]),
    (r"\ba (?:rich|vibrant|diverse) tapestry of\b", ["a mix of", "a blend of"]),
    (r"\btake a (?:deep )?dive into\b", ["dig into", "get into"]),
    (r"\bthe bottom line is that\s*", ["bottom line: ", "simply put, "]),
    (r"\bin the (?:end|final analysis)\b,?\s*", ["at the end of the day, ", "ultimately, "]),
    (r"\bfirst and foremost\b,?\s*", ["first off, ", "to start, "]),
    (r"\bneedless to say\b,?\s*", ["obviously, ", ""]),
    (r"(?:^|(?<=[.!?]\s))Overall\b,?\s*", ["All told, ", "On the whole, "]),
    (r"(?:^|(?<=[.!?]\s))Notably\b,?\s*", ["And note this: ", "In fact, "]),
    (r"(?:^|(?<=[.!?]\s))Importantly\b,?\s*", ["And here's the thing: ", ""]),
    (r"\balternatively\b,?\s*", ["or", "another way: "]),
    (r"\bmore specifically\b,?\s*", ["to be exact, ", "closer up, "]),
    (r"\bthis (?:underscores|highlights|demonstrates|illustrates) the (?:importance|significance|need) of\b", ["this shows why we need", "this makes the case for"]),
    (r"\bopens? (?:up )?(?:a|the) (?:world|realm|range) of (?:possibilities|opportunities)\b", ["opens doors", "creates room"]),
    (r"\bthe key (?:takeaway|point) (?:here )?is that\s*", ["the point is, ", "here's the thing: "]),
    (r"\bwhat (?:sets? .+ apart|makes? .+ unique) is\b", ["what's different here is", "the difference comes down to"]),
    (r"\ba game-changer\b", ["a big deal", "a real shift"]),
    (r"\bonly time will tell\b", ["we'll see", "who knows"]),
    (r"\bhas emerged as\b", ["has become", "has turned into"]),
    (r"\boffers? a wide range of\b", ["offers many", "has all sorts of"]),
    (r"\bin a matter of seconds\b", ["in seconds", "almost instantly"]),
    (r"\bimmense potential\b", ["huge potential"]),
    (r"\bbroader needs of society\b", ["wider needs of society"]),
    (r"\bvaluable insights\b", ["useful insights"]),
    (r"\bvast amounts of\b", ["huge amounts of"]),
    (r"\brapidly transforming\b", ["quickly changing"]),
    (r"\btransforming the way\b", ["changing the way"]),
    (r"\bthe way we live and work\b", ["how we live and work"]),
    (r"\bplenty of\b", ["many", "a good number of"]),
    (r"\bcontinues? to evolve\b", ["keeps evolving", "keeps changing"]),
    (r"\bcontinues? to grow\b", ["keeps growing"]),
    (r"\bcontinues? to develop\b", ["keeps developing"]),
    (r"\bcontinues? to expand\b", ["keeps expanding"]),
    (r"\bit(?:'s| is) (?:important|crucial|essential|vital) to (?:recognize|understand|acknowledge|note) that\s*", ["you have to accept that ", "we have to admit that ", "the truth is that "]),
    (r"(?:^|(?<=[.!?]\s))Ultimately\b,?\s*", ["In the end, ", "At last, ", ""]),
    (r"\bstands? as a testament to\b", ["shows", "proves"]),
    (r"\bnavigat(?:e|ing) the (?:complexities|challenges|landscape) of\b", ["working through", "dealing with"]),
    (r"\bpoised to\b", ["ready to", "set to"]),
    (r"\bfoster(?:s|ing)? (?:a |an )?(?:culture|environment) of\b", ["builds", "creates"]),
    (r"\bin the process of\b", ["working on"]),
    (r"\bit is worth (?:mentioning|highlighting) that\s*", ["worth adding: ", "and note: "]),
]

CONNECTIVE_STARTS = re.compile(
    r"^(Moreover|Furthermore|Additionally|Consequently|Notably|Importantly|In addition|Overall|Ultimately|Therefore|Hence|Thus|Nevertheless|Nonetheless)\b[,:]?\s*",
    re.IGNORECASE,
)

CONTRACTIONS = {
    "do not": "don't", "does not": "doesn't", "did not": "didn't",
    "cannot": "can't", "can not": "can't", "will not": "won't",
    "would not": "wouldn't", "should not": "shouldn't", "could not": "couldn't",
    "is not": "isn't", "are not": "aren't", "was not": "wasn't", "were not": "weren't",
    "have not": "haven't", "has not": "hasn't", "had not": "hadn't",
    "it is": "it's", "that is": "that's", "there is": "there's",
    "what is": "what's", "who is": "who's", "here is": "here's",
    "they are": "they're", "we are": "we're", "you are": "you're",
    "i am": "I'm", "i have": "I've", "i will": "I'll", "i would": "I'd",
    "we will": "we'll", "we have": "we've", "you will": "you'll",
    "they will": "they'll", "let us": "let's", "it has": "it's",
    "she is": "she's", "he is": "he's", "that would": "that'd",
}

PASSIVE_FIXES = [
    (r"\bit (?:should|must|can) be (?:noted|mentioned|said) that\s*", ["note that ", ""]),
    (r"\bcan be seen (?:in|from)\b", ["shows up in", "you can see in"]),
    (r"\bis (?:considered|regarded) to be\b", ["is"]),
    (r"\bwas created in order to\b", ["was built to", "exists to"]),
    (r"\bshould be taken into (?:account|consideration)\b", ["is worth weighing", "matters"]),
    (r"\bis expected to\b", ["should"]),
    (r"\bare expected to\b", ["should"]),
    (r"\bit has been (?:shown|demonstrated) that\s*", ["studies show ", "we know "]),
    (r"\bhave been shown to\b", ["are known to", "do"]),
    (r"\bwas made possible by\b", ["happened because of", "came from"]),
]

STRUCTURE_PATTERNS = [
    (r"\bnot (?:just|only|merely) ([\w][\w\s,]{2,60}?)\s*(?:but|rather)\s+([\w][\w\s]{2,120})",
     ["{0} and {1}", "{0}, plus {1}"]),
    (r"\b[Ww]hether you(?:'re| are) ((?:an |a )?[^,.!?]+?)(?: or ((?:an |a )?[^,.!?]+?))?\s*,\s*",
     ["if you're {0} or {1}, ", "say you're {0}. or {1}. either way, "]),
    (r"\b(?:It's|It is) not about ([\w\s,]{2,40}?)[,;] (?:it's|it is) about ([\w\s]{2,40})",
     ["this isn't about {0}. it's about {1}", "less {0}, more {1}"]),
]

SYNONYMS = {
    "important": ["big", "major", "key"],
    "good": ["solid", "decent"],
    "bad": ["poor", "rough"],
    "big": ["large", "sizable"],
    "small": ["little", "modest"],
    "fast": ["quick", "rapid"],
    "slow": ["gradual", "unhurried"],
    "help": ["help out", "lend a hand"],
    "show": ["point to", "lay out"],
    "think": ["figure", "reckon"],
    "want": ["wish for", "hope for"],
    "need": ["have to have"],
    "many": ["plenty of", "lots of"],
    "often": ["a lot", "time and again"],
    "people": ["folks"],
    "problem": ["issue", "headache"],
    "start": ["kick off", "get going"],
    "finish": ["wrap up", "get done"],
    "use": ["work with", "make use of"],
    "find": ["come across", "track down"],
    "explain": ["walk through", "spell out"],
    "reduce": ["trim", "cut"],
    "increase": ["grow", "bump up"],
    "change": ["shift", "shake up"],
    "difficult": ["tough", "tricky"],
    "easy": ["simple", "straightforward"],
    "interesting": ["fun", "neat"],
    "amazing": ["wild", "impressive"],
    "beautiful": ["lovely", "striking"],
    "expensive": ["pricey", "costly"],
    "cheap": ["affordable", "low-cost"],
    "happy": ["glad", "pleased"],
    "sad": ["down", "blue"],
    "tired": ["worn out", "beat"],
    "money": ["cash"],
    "buy": ["pick up"],
    "sell": ["move", "let go of"],
    "great": ["terrific", "top-notch"],
    "hard": ["rough", "grindy"],
    "simple": ["plain", "easy"],
    "clear": ["obvious", "plain"],
    "strong": ["solid", "sturdy"],
    "weak": ["shaky", "flimsy"],
    "new": ["fresh", "brand-new"],
    "old": ["aged", "long-standing"],
    "true": ["real", "genuine"],
    "false": ["off-base", "wrong"],
    "quick": ["fast", "snappy"],
    "smart": ["sharp", "clever"],
    "safe": ["low-risk", "secure"],
    "risky": ["dicey", "chancy"],
    "fun": ["enjoyable", "a blast"],
    "boring": ["dull", "dry"],
    "weird": ["odd", "strange"],
    "common": ["widespread", "everyday"],
    "rare": ["scarce", "uncommon"],
    "early": ["ahead of time"],
    "late": ["behind", "tardy"],
    "wrong": ["off", "mistaken"],
}

RARE_SWAPS = {
    "problem": ["snag", "hitch"],
    "mistake": ["slip-up", "fumble"],
    "success": ["win", "home run"],
    "failure": ["flop", "dud"],
    "increase": ["balloon", "climb"],
    "decrease": ["dwindle", "shrink"],
    "quickly": ["in a flash"],
    "slowly": ["at a snail's pace"],
    "expensive": ["steep", "spendy"],
    "cheap": ["dirt-cheap"],
    "weird": ["off-kilter", "kooky"],
    "boring": ["mind-numbing", "dry as dust"],
    "busy": ["swamped", "slammed"],
    "tired": ["running on fumes", "wiped"],
    "angry": ["fed up", "miffed"],
    "scared": ["spooked"],
    "happy": ["over the moon", "tickled"],
    "sad": ["bummed"],
    "difficult": ["thorny", "knotty"],
    "interesting": ["worth your time", "fascinating"],
    "understand": ["get your head around"],
    "important": ["make-or-break", "high-stakes"],
    "many": ["a good deal of", "plenty of"],
    "few": ["a handful of"],
    "very": ["downright"],
    "start": ["kick off", "get rolling"],
    "finish": ["wrap up", "wind down"],
    "improve": ["sharpen", "tighten up"],
    "confusing": ["muddled", "murky"],
    "clear": ["plain as day", "crystal"],
    "big": ["hefty", "whopping"],
    "small": ["puny", "teeny"],
    "fast": ["in a heartbeat", "quick as anything"],
    "obvious": ["plain as day"],
}

ASIDE_ANCHORS = re.compile(r",(?= \w)")

PROTECT_BIGRAMS = {
    "change management", "machine learning", "resource allocation",
    "decision making", "artificial intelligence", "best practices",
    "social media", "real estate", "customer service", "data science",
    "human resources", "operating system", "supply chain", "cash flow",
    "right now", "of course",
    "important to", "crucial to", "essential to", "critical to",
    "necessary to", "vital to", "worth noting", "bound to",
    "job displacement", "job market", "job losses", "make decisions",
    "data privacy", "algorithmic bias",
    "quickly changing", "quickly evolving", "use case", "use cases",
}

NOMINALIZATION_FIXES = [
    (r"\bthe integration of ([\w][\w\s]{2,50}?) into\b", ["integrating {0} into", "bringing {0} into"]),
    (r"\bthe implementation of ([\w][\w\s]{2,50}?)\b", ["implementing {0}", "rolling out {0}"]),
    (r"\bthe adoption of ([\w][\w\s]{2,50}?)\b", ["adopting {0}"]),
    (r"\bthe utilization of ([\w][\w\s]{2,50}?)\b", ["using {0}"]),
    (r"\bthe development of ([\w][\w\s]{2,50}?) (?:continues|remains|requires|is)\b", ["developing {0} \\3"]),
    (r"\brequires a thorough understanding of\b", ["requires you to really understand"]),
    (r"\bprovides? (?:the )?(?:ability|opportunity) to\b", ["lets you", "makes it possible to"]),
]

DISCOURSE_MARKERS = ["Honestly,", "Look,", "Now,", "That said,", "Still,", "Plus,", "Frankly,", "To be fair,"]

MERGE_SKIP_STARTS = re.compile(
    r"^(Additionally|Moreover|Furthermore|In conclusion|In summary|However|Therefore|Nevertheless|It is|This is|These)",
    re.IGNORECASE,
)

ASIDES = [
    ", honestly", ", in practice", ", more or less", ", at least in my experience",
    ", for what it's worth", ", more often than not", ", to some degree",
]

MARKERS_HUMAN_OPENERS = ["And", "But", "So", "Plus", "Still", "Yet", "Now"]

REVERSE_CONTRACTIONS = {}
for _full, _cont in CONTRACTIONS.items():
    REVERSE_CONTRACTIONS.setdefault(_cont.lower(), _full)

MARKERS_CASUAL = ["Honestly,", "Look,", "Now,", "Still,", "Plus,", "To be fair,"]
MARKERS_FORMAL = ["That said,", "In practice,", "Still,", "Even so,"]
DISCOURSE_MARKERS = MARKERS_CASUAL

ASIDES_CASUAL = [
    ", honestly", ", more or less", ", for what it's worth", ", more often than not",
    ", to some degree", ", at least in my experience",
]
ASIDES_FORMAL = [", in practice", ", in most cases", ", broadly speaking", ", at least in part"]

RARE_SWAPS_CASUAL_ONLY = {
    "dirt-cheap", "spendy", "kooky", "off-kilter", "mind-numbing", "dry as dust",
    "wiped", "running on fumes", "miffed", "bummed", "spooked", "tickled",
    "over the moon", "downright", "whopping", "puny", "teeny", "in a heartbeat",
    "quick as anything", "fumble", "home run", "flop", "dud", "balloon",
}

STOPWORDS = {
    "the", "of", "and", "to", "a", "in", "that", "is", "was", "for", "it", "with",
    "as", "on", "at", "by", "this", "which", "or", "from", "but", "not", "are", "be",
    "have", "has", "had", "they", "their", "them", "we", "our", "us", "you", "your",
    "i", "my", "me", "he", "she", "his", "her", "its", "an", "if", "then", "than",
    "so", "because", "while", "when", "where", "who", "whom", "whose", "what", "how",
    "there", "here", "all", "any", "some", "no", "nor", "only", "own", "same", "too",
    "very", "can", "will", "just", "should", "now", "also", "plus", "but", "and",
    "its", "it's", "don", "didn", "doesn", "won", "would", "could", "might", "may",
    "do", "does", "did", "am", "been", "being", "were", "into", "about", "over",
    "after", "before", "between", "through", "during", "above", "below", "up", "down",
    "out", "off", "again", "further", "once", "each", "few", "more", "most", "other",
    "such", "than", "them", "well", "still", "yet", "even", "much", "many", "lot",
}

DROPPABLE_WORDS = {
    "frankly", "honestly", "look", "plus", "however", "moreover", "furthermore",
    "additionally", "also", "ultimately", "notably", "importantly", "still",
    "overall", "besides", "meanwhile", "therefore", "consequently", "thus",
    "hence", "indeed", "essentially", "basically", "actually", "granted",
    "major", "key", "big", "huge", "solid", "decent", "sturdy", "hardy", "many",
    "lots", "tons", "loads", "myriad", "plethora", "countless", "real", "true",
    "notable", "striking", "crucial", "vital", "essential", "necessary",
    "landscape", "realm", "leverage", "robust", "seamless", "delve", "dig",
    "quickly", "rapidly", "in", "practice", "said", "fair", "top", "gear",
    "swamped", "slammed", "snag", "hitch", "win", "steep", "thorny", "knotty",
    "hefty", "sizable", "modest", "widespread", "everyday", "scarce", "uncommon",
}

FUNCTION_WORDS = {
    "the", "of", "and", "to", "a", "in", "that", "is", "was", "for", "it", "with",
    "as", "on", "at", "by", "this", "which", "or", "from", "but", "not", "are", "be",
    "have", "has", "had", "they", "their", "them", "we", "our", "us", "you", "your",
    "i", "my", "me", "he", "she", "his", "her", "its", "an", "if", "then", "than",
    "so", "because", "while", "when", "where", "who", "whom", "whose", "what", "how",
    "there", "here", "all", "any", "some", "no", "nor", "only", "same", "too",
    "very", "can", "will", "just", "should", "do", "does", "did", "am", "been",
    "being", "were", "into", "about", "would", "could", "might", "may", "must",
}

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F000-\U0001F0FF\U00002600-\U000026FF\U0001F900-\U0001F9FF\U00002B00-\U00002BFF\U0000FE0F\U0000200D]"
)

UNICODE_FIXES = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2212": "-", "\u00a0": " ", "\u200b": "",
    "\u200c": "", "\u200d": "", "\ufeff": "", "\u2026": "...",
    "\u2032": "'", "\u2033": '"', "\u0430": "a", "\u0435": "e",
    "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0443": "y",
    "\u0445": "x", "\u05d0": "a", "\u23af": "-", "\u2500": "-",
    "\uff0c": ",", "\uff1a": ":", "\uff1b": ";",
}

EM_DASH_RE = re.compile(r"\s*[—–]\s*")

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")

SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def split_sentences(text):
    parts = SENT_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p and p.strip()]

SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


@lru_cache(maxsize=1)
def word_ranks():
    ranks = {}
    path = os.path.join(DATA_DIR, "wordfreq.txt")
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            w = line.strip().lower()
            if w and w not in ranks:
                ranks[w] = i
    return ranks
