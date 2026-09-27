"""The stage's graph kit and typeset formulas (stage.py): what the director may
spawn, how formulas are checked before they reach the app, and LaTeX that
survives a model's JSON escaping."""

import json

from versa.stage import repair_tex, sanitize_action, stage_prompt, valid_formula

BS = "\\"


def test_formulas_are_the_same_small_language_the_app_reads():
    for ok in ["x^2", "x^2 - 3x + 1", "sin(x)", "sinx", "2pix", "sqrt(abs(x))", "e^x", "1/x", "ln(x)"]:
        assert valid_formula(ok), ok
    for bad in ["", "import os", "__import__(1)", "y+1", "x;x", "(x+1", "eval(x)", "x" * 90]:
        assert not valid_formula(bad), bad


def test_graph_pieces_are_kept_only_when_they_can_be_drawn():
    axes = sanitize_action({"do": "spawn", "id": "ax", "kind": "axes", "x": 0.4, "y": 0.58,
                            "w": 0.7, "h": 0.9, "xmin": -1, "xmax": 4, "ymin": -1, "ymax": 10})
    assert (axes["w"], axes["h"]) == (0.7, 0.58), "wider than a prop, but kept on the stage"
    assert sanitize_action({"do": "spawn", "id": "ax", "kind": "axes", "xmin": 3, "xmax": 1}) is None
    plot = sanitize_action({"do": "spawn", "id": "f", "kind": "plot", "on": "ax", "fn": "x^2", "tex": "y = x^2"})
    assert plot["fn"] == "x^2" and plot["on"] == "ax"
    assert sanitize_action({"do": "spawn", "id": "f", "kind": "plot", "on": "ax", "fn": "__import__(1)"}) is None
    assert sanitize_action({"do": "spawn", "id": "f", "kind": "plot", "fn": "x^2"}) is None, "a curve needs axes"
    assert sanitize_action({"do": "spawn", "id": "p", "kind": "dot", "at": 1}) is None, "a point needs a curve"
    assert sanitize_action({"do": "spawn", "id": "eq", "kind": "math"}) is None, "a formula needs its tex"
    assert sanitize_action({"do": "slide", "target": "p", "at": 3, "ms": 1500}) == {
        "do": "slide", "target": "p", "at": 3.0, "ms": 1500.0}
    assert sanitize_action({"do": "slide", "target": "p"}) is None


def test_latex_survives_a_single_backslash_in_json():
    # "\frac" written with ONE backslash in JSON arrives as a form feed + "rac"
    raw = json.loads('{"do":"spawn","id":"eq","kind":"math","tex":"' + BS + "frac{dy}{dx} = "
                     + BS + "theta " + BS + "neq " + BS + 'beta"}')
    assert sanitize_action(raw)["tex"] == BS + "frac{dy}{dx} = " + BS + "theta " + BS + "neq " + BS + "beta"
    right = json.loads('{"do":"spawn","id":"eq","kind":"math","tex":"' + BS * 2 + 'frac{dy}{dx}"}')
    assert sanitize_action(right)["tex"] == BS + "frac{dy}{dx}"
    assert repair_tex("\f") == BS + "f"


def test_the_director_is_taught_to_show_the_real_thing():
    prompt = stage_prompt("what is a derivative?", opening="A derivative measures")
    assert "GRAPH KIT" in prompt and '"kind":"tangent"' in prompt and '"do":"slide"' in prompt
    assert "SHOW THE REAL THING" in prompt
    assert '"' + BS * 2 + 'frac{dy}{dx} = 2x"' in prompt, "the example is valid JSON"


def test_3d_world_solids_and_moves_are_bounded():
    assert sanitize_action({"do": "world", "mode": "3d"}) == {"do": "world", "mode": "3d"}
    assert sanitize_action({"do": "world", "mode": "4d"}) is None
    planet = sanitize_action({"do": "spawn", "id": "e", "kind": "planet", "x": 0.6, "z": 3, "lift": 0.1, "spin": 9})
    assert (planet["z"], planet["spin"]) == (1, 2), "depth and spin kept on the stage"
    assert sanitize_action({"do": "turn", "target": "e", "yaw": 1, "pitch": 0.25, "ms": 1500})["yaw"] == 1
    assert sanitize_action({"do": "turn", "yaw": 1}) is None
    orbit = sanitize_action({"do": "orbit", "target": "m", "around": "e", "radius": 0.9, "turns": 1, "ms": 4000})
    assert orbit["radius"] == 0.4
    assert sanitize_action({"do": "orbit", "target": "m", "around": "m"}) is None, "nothing orbits itself"
    prompt = stage_prompt("how does the moon orbit the earth?", live=True)
    assert "3D ROOM" in prompt and 'ANY spawn may take "z"' in prompt, "the room is for everything"
    assert '"do":"orbit"' in prompt and "planet" in prompt


def test_the_director_tells_one_story_and_can_move_to_a_new_scene():
    assert sanitize_action({"do": "scene", "junk": 1}) == {"do": "scene"}
    prompt = stage_prompt("what is einstein's theory of relativity?", live=True)
    assert "TELL ONE STORY" in prompt and "SET UP -> SHOW -> REACT -> LINK" in prompt
    assert "Show first, then say it" in prompt and '{"do":"scene"}' in prompt


def test_an_emoji_written_as_the_kind_becomes_an_emoji_with_its_label_as_a_caption():
    out = sanitize_action({"do": "spawn", "id": "c", "kind": "⌛", "label": "t = 12:00", "x": 0.2})
    assert (out["kind"], out["label"], out["caption"]) == ("emoji", "⌛", "t = 12:00")
    assert sanitize_action({"do": "spawn", "id": "r", "kind": "🚀"})["label"] == "🚀"
    assert sanitize_action({"do": "spawn", "id": "x", "kind": "rocket"})["kind"] == "rocket", "words stay as they were"
    kept = sanitize_action({"do": "spawn", "id": "c", "kind": "emoji", "label": "⏰", "caption": "t = 0"})
    assert kept["caption"] == "t = 0"


def test_links_need_two_ends_and_entrances_are_a_known_style():
    link = sanitize_action({"do": "spawn", "id": "heat", "kind": "link", "on": "sun", "to": "pot", "label": "heat"})
    assert (link["on"], link["to"]) == ("sun", "pot")
    assert sanitize_action({"do": "spawn", "id": "l", "kind": "link", "on": "sun"}) is None
    assert sanitize_action({"do": "spawn", "id": "l", "kind": "link", "on": "a", "to": "a"}) is None
    assert sanitize_action({"do": "spawn", "id": "b", "kind": "box", "enter": "drop"})["enter"] == "drop"
    assert "enter" not in sanitize_action({"do": "spawn", "id": "b", "kind": "box", "enter": "teleport"})
    prompt = stage_prompt("how does a stove heat a pot?", live=True)
    assert "K = link" in prompt and '"enter"' in prompt and "Keep it ALIVE" in prompt


def test_instruments_cruise_compare_and_the_plan_line():
    from versa.stage import check_script

    clock = sanitize_action({"do": "spawn", "id": "c", "kind": "clock", "rate": 9, "track": "ship"})
    assert (clock["rate"], clock["track"]) == (5, "ship")
    assert sanitize_action({"do": "spawn", "id": "g", "kind": "gauge", "value": 2})["value"] == 1
    assert sanitize_action({"do": "cruise", "target": "ship", "speed": 3})["speed"] == 0.99
    assert sanitize_action({"do": "cruise", "speed": 0.5}) is None
    assert sanitize_action({"do": "set", "target": "g"}) is None, "nothing to set"
    assert sanitize_action({"do": "compare", "left": "Home", "right": "Ship"}) == {
        "do": "compare", "left": "Home", "right": "Ship"}
    plan = sanitize_action({"do": "plan", "cast": {"ship": "the spaceship"}, "shows": "time slows"})
    assert plan["cast"] == {"ship": "the spaceship"}
    assert sanitize_action({"do": "plan", "cast": {"a": "b"}}, depth=1) is None

    shown = [plan, {"do": "spawn", "id": "ship", "kind": "emoji"}, {"do": "spawn", "id": "c", "kind": "clock"}]
    assert check_script(shown) == []
    told = [plan, {"do": "spawn", "id": "ship", "kind": "emoji"}]
    assert any("nothing measuring it" in p for p in check_script(told))
    assert check_script([{"do": "spawn", "id": "x", "kind": "box"}]) == ["no plan line"]
    orphan = [plan, {"do": "spawn", "id": "ship", "kind": "emoji"}, {"do": "spawn", "id": "c", "kind": "clock"},
              {"do": "spawn", "id": "v", "kind": "math", "tex": "v", "on": "rocket"}]
    assert any("never appears" in p for p in check_script(orphan))

    prompt = stage_prompt("what is relativity?", live=True)
    assert "The FIRST line is the PLAN" in prompt and "INSTRUMENTS" in prompt and '"do":"cruise"' in prompt
    assert '"do":"compare"' in prompt and "LABELS ON THINGS" in prompt and "Real things look like themselves" in prompt
