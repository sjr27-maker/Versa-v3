"""formatting.py: LaTeX a model wrote into JSON without doubling its
backslashes survives parsing."""

import json

from versa.formatting import repair_latex_escapes


def test_latex_commands_swallowed_by_json_escapes_are_put_back():
    # "\frac" would parse as a form feed + "rac", "\text" as a tab + "ext" (seen live 2026-10-01)
    raw = r'{"t": "$\frac{d}{t}$ in $\text{m/s}$, $\theta$, $v \neq u$, $a \times b$, $\beta$, $\rho$"}'
    assert json.loads(repair_latex_escapes(raw))["t"] == (
        r"$\frac{d}{t}$ in $\text{m/s}$, $\theta$, $v \neq u$, $a \times b$, $\beta$, $\rho$"
    )


def test_real_escapes_and_doubled_backslashes_are_left_alone():
    raw = r'{"t": "one\nNext\nexample\ttab, already \\frac{1}{2}, a quote \" here"}'
    assert json.loads(repair_latex_escapes(raw))["t"] == 'one\nNext\nexample\ttab, already \\frac{1}{2}, a quote " here'
