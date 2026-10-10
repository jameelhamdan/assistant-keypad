


def test_press_must_match_the_screen():
    from keypad.core.dialogs import press_matches

    sel = {"tpl": "select", "items": [f"o{i}" for i in range(10)]}  # a decision: no Esc
    assert not press_matches(sel, {"key": 1, "act": "pick", "idx": 0}), "number keys are unassigned"
    assert not press_matches(sel, {"key": 3, "act": "pick", "idx": 2})
    assert press_matches(sel, {"key": 7, "act": "pick", "idx": 9}), "Enter on any option"
    assert not press_matches(sel, {"key": 0, "act": "pick", "idx": 0}), "the keypad sends the knob press as Enter (7), never as key 0"
    assert not press_matches(sel, {"key": 5, "act": "pc"}), "no way out of a decision"
    done = {"tpl": "prompt", "esc": "done", "items": ["continue"]}
    assert press_matches(done, {"key": 5, "act": "done"}) and not press_matches(done, {"key": 0, "act": "done"})
    assert not press_matches(sel, {"key": 4, "act": "pick", "idx": 3}), "4 is unassigned, not a pick"
    assert not press_matches(sel, {"key": 7, "act": "pick", "idx": 10}), "no such option"
    assert not press_matches(done, {"key": 7, "act": "done"}), "done is Esc (key 5), not Enter"
    assert not press_matches(sel, {"key": 7, "act": "allow"}), "not an action the screen offers"
    multi = {"tpl": "multi", "items": ["a", "b", "c"]}
    assert press_matches(multi, {"key": 7, "act": "submit", "sel": [0, 2]})
    assert not press_matches(multi, {"key": 7, "act": "submit", "sel": [3]})
    assert not press_matches(multi, {"key": 7, "act": "submit", "sel": []})
    assert not press_matches(multi, {"key": 7, "act": "pick", "idx": 0})


