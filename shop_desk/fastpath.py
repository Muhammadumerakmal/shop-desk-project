"""A repeated price question costs nothing at all (XR-3).

The fast path (FR-3) already answers a price question in one model call: the Desk calls
`lookup_price` and the SDK stops the turn, so the model never writes a sentence around the result.
This module goes one step further for the *second* time a customer asks about the same product —
**zero** model calls, by never starting a run at all.

Two halves, deliberately on opposite sides of the model:

- `lookup_price` **writes**. It has already resolved the product against the catalogue and already
  holds the answer text, so recording it there is free and cannot disagree with the tool.
- `DeskSession` **reads**, but only after `is_price_question()` recognises a plain price question.
  The cache key is the **set of SKUs the catalogue resolves that question to**, so the session and
  the tool agree by construction: the tool is handed a product name, the session is handed a whole
  sentence, and both arrive at the same key or at no key at all.

The key also carries `Catalogue.fingerprint`, a hash of the file's bytes, so editing
`catalogue.json` invalidates every entry at once. That is the point: a cached figure may only ever
describe one exact version of the catalogue, and NFR-3 would be broken if a stale one outlived an
edit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from shop_desk.catalogue import Catalogue

# A question that asks for the price or availability of a product and nothing else. The product
# may sit anywhere in the sentence ("what is the price OF the kettle" vs "what does the kettle
# COST"), so these are whole question shapes rather than one keyword pattern. The subject is not
# extracted: the cache key comes from resolving the whole text with `Catalogue.find`, which already
# discards question words.
_POLITE = r"(?:hi|hey|hello|ok|okay|thanks|thank you|please)?[\s,]*"
_TAIL = r"[\s?.!]*$"

PRICE_QUESTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        _POLITE + r"what(?:'s|\s+is|\s+are|\s+was|\s+does|\s+do)?\s+(?:the\s+)?(?:price|cost)\b.*" + _TAIL,
        _POLITE + r"what\s+(?:does|do|is|are|was|were)\s+.*\s+(?:cost|price)\b" + _TAIL,
        _POLITE + r"how\s+much\s+(?:is|are|does|do|for|cost)\b.*" + _TAIL,
        _POLITE + r"(?:price|cost)\s+(?:of|for|on)\b.*" + _TAIL,
        _POLITE + r"is\s+.*\b(?:in\s+stock|available)\b.*" + _TAIL,
        _POLITE + r"do\s+you\s+(?:have|stock|still\s+have)\b.*" + _TAIL,
        _POLITE + r"can\s+i\s+(?:get|buy|have|order)\b.*" + _TAIL,
    )
)

# Anything that means the customer wants a judgement, a basket or a person rather than one figure.
# Checked before the shapes, so "what is the total cost of my order?" can never be served from cache.
NOT_A_PRICE_QUESTION_RE = re.compile(
    r"\b(basket|order(?:ing|ed)?|orders?|total|subtotal|discount|loyalty|delivery|"
    r"return|refund|receipt|warranty|guarantee|compare|cheaper|cheapest|least|most|"
    r"staff|human|person|manager|complain|complaint)\b"
    r"|\d{1,2}\s*%|\boff\b|\bper\b",
    re.IGNORECASE,
)


def is_price_question(text: str) -> bool:
    """True only for a plain "what does X cost / is X in stock" question.

    False is the common and safe answer: the turn then goes through the normal loop, where the model
    decides. The cache may only ever save a call the model would have made anyway, never answer
    something the model was asked to think about.
    """
    if NOT_A_PRICE_QUESTION_RE.search(text):
        return False
    return any(pattern.match(text) for pattern in PRICE_QUESTION_PATTERNS)


@dataclass
class PriceCache:
    """(SKUs resolved by the catalogue, fingerprint of the file) -> the exact tool output."""

    entries: dict[tuple[frozenset[str], str], str] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    @staticmethod
    def key(catalogue: Catalogue, question: str) -> tuple[frozenset[str], str] | None:
        """The key for a question, or None when the catalogue resolves it to no product.

        `question` may be a whole customer sentence or a bare product name: `Catalogue.find()`
        already discards question words, so both land on the same key.
        """
        matches = catalogue.find(question)
        if not matches:
            return None
        return frozenset(p.sku for p in matches), catalogue.fingerprint

    def get(self, catalogue: Catalogue, question: str) -> str | None:
        key = self.key(catalogue, question)
        if key is None or key not in self.entries:
            self.misses += 1
            return None
        self.hits += 1
        return self.entries[key]

    def put(self, catalogue: Catalogue, question: str, text: str) -> None:
        key = self.key(catalogue, question)
        if key is not None:
            self.entries[key] = text

    def stats(self) -> str:
        return f"{self.hits} hit(s), {self.misses} miss(es), {len(self.entries)} entry/entries"
