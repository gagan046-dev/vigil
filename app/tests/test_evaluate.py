from vigil.evaluate import condition_true, diff_summary, evaluate


def _price(value, prev_met=False, prev_hash="h0", new_hash="h1", kind="price_below", threshold=120000):
    return evaluate(
        kind, prev_condition_met=prev_met, prev_hash=prev_hash, new_hash=new_hash, value=value, threshold=threshold
    )


def test_conditions():
    assert condition_true("price_below", value=10, threshold=20)
    assert not condition_true("price_below", value=20, threshold=20)
    assert condition_true("price_above", value=21, threshold=20)
    assert not condition_true("price_above", value=None, threshold=20)
    assert condition_true("text_appears", keyword="Slot", text="a SLOT available")
    assert condition_true("text_disappears", keyword="sold out", text="In stock")
    assert not condition_true("text_disappears", keyword="sold out", text="Sold Out")
    assert condition_true("page_changed", prev_hash="a", new_hash="b")
    assert not condition_true("page_changed", prev_hash=None, new_hash="b")


def test_price_fires_once_then_rearms():
    e1 = _price(134990)
    assert (e1.fire, e1.status, e1.condition_met) == (False, "changed", False)
    e2 = _price(114990, prev_met=False)
    assert (e2.fire, e2.status, e2.condition_met) == (True, "triggered", True)
    e3 = _price(110000, prev_met=True)  # still below: no repeat notification
    assert (e3.fire, e3.status, e3.condition_met) == (False, "changed", True)
    e4 = _price(130000, prev_met=True)  # back above: re-arm
    assert (e4.fire, e4.condition_met) == (False, False)
    e5 = _price(100000, prev_met=False)
    assert e5.fire


def test_first_check_baseline_or_immediate_fire():
    assert _price(134990, prev_hash=None).status == "baseline"
    first = _price(100000, prev_hash=None)
    assert first.fire and first.status == "triggered"


def test_price_above():
    assert _price(60000, kind="price_above", threshold=50000).fire


def test_text_appears_transition():
    a = evaluate(
        "text_appears", prev_condition_met=False, prev_hash="1", new_hash="2", keyword="In stock", text="Out of stock"
    )
    assert not a.fire
    b = evaluate(
        "text_appears", prev_condition_met=False, prev_hash="2", new_hash="3", keyword="In stock", text="In stock now"
    )
    assert b.fire and b.condition_met
    c = evaluate(
        "text_appears",
        prev_condition_met=True,
        prev_hash="3",
        new_hash="4",
        keyword="In stock",
        text="In stock, 2 left",
    )
    assert not c.fire


def test_page_changed_baseline_then_every_change():
    base = evaluate("page_changed", prev_condition_met=False, prev_hash=None, new_hash="a")
    assert (base.status, base.fire) == ("baseline", False)
    ch = evaluate("page_changed", prev_condition_met=False, prev_hash="a", new_hash="b")
    assert (ch.status, ch.fire) == ("triggered", True)
    ch2 = evaluate("page_changed", prev_condition_met=True, prev_hash="b", new_hash="c")
    assert ch2.fire  # fires on every change
    same = evaluate("page_changed", prev_condition_met=True, prev_hash="c", new_hash="c")
    assert (same.status, same.fire) == ("unchanged", False)


def test_diff_summary():
    s = diff_summary("Notice A\nNotice B", "Exam on Monday\nNotice A\nNotice B")
    assert s.startswith("+1/-0 lines")
    assert "Exam on Monday" in s
    assert diff_summary("a\nb", "a").startswith("+0/-1 lines; removed: b")
