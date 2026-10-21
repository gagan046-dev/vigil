import pytest

from vigil.matching import match_product, normalise


def test_normalise_joins_units_and_splits_model_codes():
    assert normalise('Aurora 15 (RTX5070) 15.6" 1 TB') == ["aurora", "15", "rtx", "5070", "15.6inch", "1024gb"]
    assert "128gb" in normalise("Apple iPhone 16 (Black, 128 GB)")


@pytest.mark.parametrize(
    "candidate",
    [
        'Aurora 15 RTX 5070 15.6" Gaming Laptop | Bazaar99',
        "Aurora 15 (RTX 5070) Gaming Laptop - PixelMart",
        "Aurora 15 Gaming Laptop, RTX 5070 - Northwind Electronics",
    ],
)
def test_accepts_same_model(candidate):
    m = match_product("Aurora 15 (RTX 5070) Gaming Laptop", candidate)
    assert m.verdict == "match" and m.score >= 0.75


@pytest.mark.parametrize("reference", ["Aurora 15 (RTX 5070) Gaming Laptop", "aurora 15"])
def test_rejects_pro_variant_with_other_gpu(reference):
    m = match_product(reference, "Aurora 15 Pro RTX 5080 Gaming Laptop")
    assert m.verdict == "rejected" and m.score < 0.5
    assert "pro" in m.reason


def test_model_number_conflict_alone_rejects():
    m = match_product("Aurora 15 RTX 5070", "Aurora 15 RTX 5080")
    assert m.verdict == "rejected" and "rtx 5070 vs 5080" in m.reason


def test_capacity_rules():
    ref = "Apple iPhone 16 128GB"
    assert match_product(ref, "Apple iPhone 16 (Black, 128 GB)").verdict == "match"
    bad = match_product(ref, "Apple iPhone 16 (256 GB) - Black")
    assert bad.verdict == "rejected" and "128gb vs 256gb" in bad.reason
    # RAM + storage listed together is fine as long as the asked capacity is there
    assert match_product(ref, "Apple iPhone 16 (Ultramarine, 8GB RAM, 128GB Storage)").verdict == "match"
    # 1 TB == 1024 GB
    assert match_product("Laptop 1TB SSD Zen 14", "Zen 14 laptop 1024 GB SSD").verdict == "match"


def test_generation_and_suffix_conflicts():
    assert match_product("Apple iPhone 16 128GB", "Apple iPhone 15 128GB").verdict == "rejected"
    assert match_product("Apple iPhone 16 128GB", "Apple iPhone 16e 128GB").verdict == "rejected"
    assert match_product("Apple iPhone 16 128GB", "Apple iPhone 16 Plus 128GB").verdict == "rejected"
    assert match_product("Apple iPhone 16 Plus 128GB", "Apple iPhone 16 Plus (128 GB)").verdict == "match"


def test_screen_size_conflict():
    assert match_product('Aurora 15.6" RTX 5070', 'Aurora 14" RTX 5070').verdict == "rejected"


def test_accessories_are_rejected():
    m = match_product("Apple iPhone 16 128GB", "Silicone case for Apple iPhone 16 128GB")
    assert m.verdict == "rejected" and "accessory" in m.reason


def test_partial_title_is_possible_and_empty_is_rejected():
    m = match_product("Sony WH-1000XM5 Wireless Headphones Black", "Sony WH-1000XM5")
    assert m.verdict in ("possible", "match")
    assert match_product("Nike Pegasus 41 running shoes", "Nike Pegasus running shoes").verdict == "possible"
    assert match_product("", "anything").verdict == "rejected"
