"""FR-1: the catalogue file is the source; bad data becomes a readable error."""

import json

import pytest

from shop_desk.catalogue import CatalogueError, load_catalogue, money


def test_find_by_name_and_sku(catalogue_file):
    cat = load_catalogue(catalogue_file)
    assert [p.sku for p in cat.find("What does the kettle cost?")] == ["KTL-01"]
    assert [p.sku for p in cat.find("ktl-01")] == ["KTL-01"]
    assert [p.sku for p in cat.find("kettles")] == ["KTL-01"]
    assert cat.find("playstation") == []


def test_money_format():
    assert money(4200) == "PKR 4,200"
    assert money(3990.5) == "PKR 3,990.50"


def test_file_is_reread_every_call(catalogue_file):
    data = json.loads(catalogue_file.read_text())
    data["products"][0]["price"] = 4500
    catalogue_file.write_text(json.dumps(data))
    assert load_catalogue(catalogue_file).get("KTL-01").price == 4500


def test_malformed_catalogue_is_a_catalogue_error(tmp_path):
    bad = tmp_path / "catalogue.json"
    bad.write_text("{not json")
    with pytest.raises(CatalogueError):
        load_catalogue(bad)
