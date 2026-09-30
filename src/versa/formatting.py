"""How text a learner reads is written, so the app can show it cleanly.

The app renders a small, fixed set of formatting (app/lib/widgets/rich_text.dart):
LaTeX maths between $...$ (inline) or $$...$$ (its own line), **bold**,
*italic*, `code`, "- " bullets, "1. " steps and "## " headings. Every prompt
whose output is shown as prose to a learner includes MATH_STYLE, so maths
arrives typeset rather than as "x^2/3" or "sqrt(2)".
"""

import re

MATH_STYLE = (
    "Formatting: write every formula, equation, variable and symbol as LaTeX -- inline "
    "between single dollar signs ($x^2 + 1$, $\\frac{a}{b}$, $\\sqrt{2}$, $\\theta$, $\\Delta v$), "
    "or between double dollar signs on its own line for a displayed equation ($$...$$). Never "
    "write maths as plain text (no x^2, sqrt(2), a/b for fractions). Use **bold** for a key term "
    "sparingly; '- ' bullets or '1. ' steps only when the content really is a list. No tables, "
    "no HTML."
)

# The same, for a JSON field's text: inside JSON every backslash is doubled.
MATH_STYLE_JSON = (
    MATH_STYLE
    + ' Inside the JSON strings every LaTeX backslash is written twice ("\\\\frac", "\\\\theta").'
)

# LaTeX commands whose first letter is also a JSON escape (\f \b \t \r \n \u).
# A model that writes "\frac" in a JSON string without doubling the backslash
# produces VALID JSON meaning a form feed + "rac" (seen live 2026-10-01 in a
# room explanation). These get their backslash back before parsing -- whole
# command names only, so a real newline before a word ("\nexample") is left.
_SWALLOWED = re.compile(
    r"(?<!\\)\\(?=(?:frac|forall|beta|bar|begin|boldsymbol|bf|times|theta|text|textbf|textit|tan|tau|"
    r"tfrac|to|rho|right|rightarrow|rm|nu|neq|ne|nabla|not|underline|uparrow)(?![a-zA-Z]))"
)


def repair_latex_escapes(raw: str) -> str:
    """A model's JSON text with the LaTeX commands a JSON escape would
    swallow given their backslash back, so json.loads keeps "\\frac"."""
    return _SWALLOWED.sub(r"\\\\", raw or "")
