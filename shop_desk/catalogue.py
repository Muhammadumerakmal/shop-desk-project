"""The catalogue: the only source of products, prices, stock and SKUs (FR-1, NFR-3).

`load_catalogue()` reads the file on every call on purpose: the guardrail must compare against
what the file says *now*, not what it said when the process started.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE_PATH = Path(os.getenv("SHOP_DESK_CATALOGUE", PROJECT_ROOT / "catalogue.json"))

_STOPWORDS = {
    "a", "an", "the", "what", "whats", "does", "do", "is", "are", "how", "much", "many",
    "cost", "costs", "price", "prices", "of", "for", "your", "you", "have", "in", "stock",
    "please", "me", "tell", "i", "want", "any", "one", "there",
}


class CatalogueError(Exception):
    """The catalogue file is missing or malformed."""


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: float
    stock: int

    @property
    def in_stock(self) -> bool:
        return self.stock > 0


@dataclass(frozen=True)
class Catalogue:
    shop: str
    currency: str
    products: dict[str, Product]
    # SHA-256 of the file's bytes (XR-3). Any edit to the file changes it, which is what
    # invalidates the price cache: a cached figure belongs to one exact version of the file.
    fingerprint: str = ""

    def get(self, sku: str) -> Product | None:
        return self.products.get(sku.strip().upper())

    def find(self, query: str) -> list[Product]:
        """Products matching a SKU or name words, best matches first."""
        by_sku = self.get(query)
        if by_sku:
            return [by_sku]
        words = [_singular(w) for w in re.findall(r"[a-z0-9]+", query.lower())]
        words = [w for w in words if w not in _STOPWORDS]
        if not words:
            return []
        scored = []
        for product in self.products.values():
            name_words = {_singular(w) for w in re.findall(r"[a-z0-9]+", product.name.lower())}
            score = sum(1 for w in words if w in name_words or w == product.sku.lower())
            if score:
                scored.append((score, product))
        if not scored:
            return []
        best = max(score for score, _ in scored)
        return [p for score, p in scored if score == best]

    def money(self, amount: float) -> str:
        return money(amount, self.currency)


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def money(amount: float, currency: str = "PKR") -> str:
    """Format an amount the way every tool writes it: 'PKR 4,200' (or 'PKR 3,990.50')."""
    if float(amount).is_integer():
        return f"{currency} {int(amount):,}"
    return f"{currency} {amount:,.2f}"


def load_catalogue(path: Path | str | None = None) -> Catalogue:
    """Read and validate the catalogue file. Raises CatalogueError with a readable message."""
    path = Path(path) if path else CATALOGUE_PATH
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        products = {}
        for item in raw["products"]:
            product = Product(
                sku=str(item["sku"]).strip().upper(),
                name=str(item["name"]),
                price=float(item["price"]),
                stock=int(item["stock"]),
            )
            if product.price < 0 or product.stock < 0:
                raise ValueError(f"negative price or stock for {product.sku}")
            products[product.sku] = product
        return Catalogue(
            shop=str(raw["shop"]),
            currency=str(raw["currency"]),
            products=products,
            fingerprint=hashlib.sha256(raw_bytes).hexdigest(),
        )
    except FileNotFoundError as exc:
        raise CatalogueError(f"The catalogue file {path.name} was not found.") from exc
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise CatalogueError(f"The catalogue file {path.name} could not be read ({exc}).") from exc
