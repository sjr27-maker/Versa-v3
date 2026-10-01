import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';

import '../theme.dart';

/// Text a tutor wrote, shown clean: LaTeX maths typeset, and the little
/// formatting the prompts allow (server: formatting.py MATH_STYLE) --
///
///   $x^2$ or \(x^2\)        inline maths
///   $$...$$ or \[...\]      a displayed equation (may span lines)
///   **bold**  *italic*  `code`
///   - bullets   1. steps   ## headings
///
/// Anything else is shown as written. A lone dollar sign ("costs $5") stays
/// text: inline maths needs a non-space right inside both dollars, and no
/// digit straight after the closing one. Half-written maths while an answer
/// streams in shows as plain text until it closes. Maths the typesetter
/// can't read is shown as readable symbols ([texToPlain]), never as raw LaTeX.
class RichMessageText extends StatelessWidget {
  const RichMessageText(this.text, {super.key, this.style, this.selectable = true});

  final String text;
  final TextStyle? style;
  final bool selectable;

  @override
  Widget build(BuildContext context) {
    final base = style ?? sans(14.5, height: 1.6);
    final blocks = parseBlocks(text);
    final children = <Widget>[];
    for (final (i, b) in blocks.indexed) {
      if (i > 0) children.add(SizedBox(height: b.kind == BlockKind.item && blocks[i - 1].kind == BlockKind.item ? 4 : 10));
      children.add(_block(b, base));
    }
    final column = Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: children);
    return selectable ? SelectionArea(child: column) : column;
  }

  Widget _block(Block b, TextStyle base) {
    switch (b.kind) {
      case BlockKind.math:
        return SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Math.tex(
              b.text,
              mathStyle: MathStyle.display,
              textStyle: base.copyWith(fontSize: (base.fontSize ?? 14.5) * 1.1),
              onErrorFallback: (_) => Text(texToPlain(b.text), style: base),
            ),
          ),
        );
      case BlockKind.heading:
        final size = (base.fontSize ?? 14.5) * (b.level == 1 ? 1.3 : 1.12);
        return Text.rich(TextSpan(children: inlineSpans(b.text, base.copyWith(fontSize: size, fontWeight: FontWeight.w700))));
      case BlockKind.item:
        return Padding(
          padding: EdgeInsets.only(left: 4.0 + 16 * b.level),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: b.marker == null ? 14 : 22,
                child: Text(b.marker ?? '•', style: base.copyWith(color: Paper.muted)),
              ),
              Expanded(child: Text.rich(TextSpan(children: inlineSpans(b.text, base)))),
            ],
          ),
        );
      case BlockKind.paragraph:
        return Text.rich(TextSpan(children: inlineSpans(b.text, base)));
    }
  }
}

const _texSymbols = <String, String>{
  'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'δ', 'epsilon': 'ε', 'varepsilon': 'ε', 'zeta': 'ζ',
  'eta': 'η', 'theta': 'θ', 'vartheta': 'ϑ', 'iota': 'ι', 'kappa': 'κ', 'lambda': 'λ', 'mu': 'μ', 'nu': 'ν',
  'xi': 'ξ', 'pi': 'π', 'rho': 'ρ', 'sigma': 'σ', 'tau': 'τ', 'upsilon': 'υ', 'phi': 'φ', 'varphi': 'φ',
  'chi': 'χ', 'psi': 'ψ', 'omega': 'ω', 'Gamma': 'Γ', 'Delta': 'Δ', 'Theta': 'Θ', 'Lambda': 'Λ', 'Xi': 'Ξ',
  'Pi': 'Π', 'Sigma': 'Σ', 'Phi': 'Φ', 'Psi': 'Ψ', 'Omega': 'Ω',
  'times': '×', 'cdot': '·', 'div': '÷', 'pm': '±', 'mp': '∓', 'ast': '∗', 'star': '⋆', 'circ': '∘',
  'leq': '≤', 'le': '≤', 'geq': '≥', 'ge': '≥', 'neq': '≠', 'ne': '≠', 'approx': '≈', 'sim': '∼',
  'simeq': '≃', 'cong': '≅', 'equiv': '≡', 'propto': '∝', 'll': '≪', 'gg': '≫', 'infty': '∞',
  'to': '→', 'rightarrow': '→', 'longrightarrow': '→', 'leftarrow': '←', 'leftrightarrow': '↔',
  'Rightarrow': '⇒', 'implies': '⇒', 'Leftarrow': '⇐', 'Leftrightarrow': '⇔', 'iff': '⇔', 'mapsto': '↦',
  'uparrow': '↑', 'downarrow': '↓', 'nearrow': '↗', 'searrow': '↘', 'swarrow': '↙', 'nwarrow': '↖',
  'rightleftharpoons': '⇌', 'sum': '∑', 'prod': '∏', 'int': '∫', 'oint': '∮', 'partial': '∂', 'nabla': '∇',
  'degree': '°', 'angle': '∠', 'perp': '⊥', 'parallel': '∥', 'triangle': '△', 'in': '∈', 'notin': '∉',
  'subset': '⊂', 'subseteq': '⊆', 'supset': '⊃', 'cup': '∪', 'cap': '∩', 'emptyset': '∅', 'forall': '∀',
  'exists': '∃', 'neg': '¬', 'land': '∧', 'lor': '∨', 'therefore': '∴', 'because': '∵', 'hbar': 'ℏ',
  'ell': 'ℓ', 'prime': '′', 'ldots': '…', 'cdots': '…', 'dots': '…', 'vdots': '⋮', 'langle': '⟨',
  'rangle': '⟩', 'lvert': '|', 'rvert': '|', 'mid': '|', 'backslash': '\\', 'colon': ':',
  // spacing and sizing: nothing to show
  'quad': ' ', 'qquad': ' ', 'left': '', 'right': '', 'big': '', 'Big': '', 'bigg': '', 'Bigg': '',
  'displaystyle': '', 'textstyle': '', 'limits': '', 'nolimits': '', 'relax': '', 'hfill': ' ',
};

const _superscripts = {
  '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
  '+': '⁺', '-': '⁻', 'n': 'ⁿ', 'i': 'ⁱ', '(': '⁽', ')': '⁾',
};
const _subscripts = {
  '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
  '+': '₊', '-': '₋', '(': '₍', ')': '₎',
};

String _script(String body, Map<String, String> glyphs, String mark) {
  final t = body.trim();
  if (t.isEmpty) return '';
  if (t.split('').every(glyphs.containsKey)) return t.split('').map((c) => glyphs[c]!).join();
  return t.length == 1 ? '$mark$t' : '$mark($t)';
}

final _texLayout = RegExp(
  r'\\(?:hspace|vspace|hskip|vskip|kern|mkern|mskip|rule|raisebox|phantom|hphantom|vphantom)\*?'
  r'\s*(?:\{[^{}]*\}|-?[\d.]+\s*(?:em|ex|mu|pt|px|cm|mm))?(?:\{[^{}]*\}){0,2}',
);
final _texEnv = RegExp(r'\\(?:begin|end)\s*\{[^{}]*\}(?:\{[^{}]*\})?');
final _texFrac = RegExp(r'\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}');
final _texRoot = RegExp(r'\\sqrt\s*\{([^{}]*)\}');
final _texWrap = RegExp(
  r'\\(?:text|textbf|textit|textrm|texttt|mathrm|mathbf|mathit|mathsf|mathtt|mathcal|mathbb|boldsymbol|bm|'
  r'operatorname|overline|underline|vec|hat|bar|tilde|boxed|mbox)\s*\{([^{}]*)\}',
);
final _texSimple = RegExp(r'^[\w.]+$');

/// LaTeX the typesetter refused -> the same thing in ordinary symbols, so a
/// formula the model wrote badly still reads ("Symbol: ∼ ↗", "(a + b)/2",
/// "x² ≤ 4") instead of showing its backslashes and braces.
String texToPlain(String tex) {
  var s = tex.replaceAll(_texLayout, ' ').replaceAll(_texEnv, ' ');
  for (var i = 0; i < 8; i++) {
    final before = s;
    s = s.replaceAllMapped(_texFrac, (m) {
      String part(String p) => _texSimple.hasMatch(p.trim()) ? p.trim() : '(${p.trim()})';
      return '${part(m[1]!)}/${part(m[2]!)}';
    });
    s = s.replaceAllMapped(_texRoot, (m) => '√(${m[1]!.trim()})');
    s = s.replaceAllMapped(_texWrap, (m) => m[1]!);
    s = s.replaceAllMapped(RegExp(r'\^\s*\{([^{}]*)\}'), (m) => _script(m[1]!, _superscripts, '^'));
    s = s.replaceAllMapped(RegExp(r'_\s*\{([^{}]*)\}'), (m) => _script(m[1]!, _subscripts, '_'));
    if (s == before) break;
  }
  s = s.replaceAll(RegExp(r'\^\s*\\circ\b'), '°');
  s = s.replaceAllMapped(RegExp(r'\^([0-9n+\-])'), (m) => _superscripts[m[1]]!);
  s = s.replaceAllMapped(RegExp(r'_([0-9])'), (m) => _subscripts[m[1]]!);
  // a command -> its symbol; one with no symbol keeps its name (\sin -> sin)
  s = s.replaceAllMapped(RegExp(r'\\([a-zA-Z]+)\s?'), (m) {
    final symbol = _texSymbols[m[1]];
    if (symbol == null) return '${m[1]} ';
    return symbol.isEmpty ? '' : '$symbol ';
  });
  s = s.replaceAllMapped(RegExp(r'\\([{}%$&#_])'), (m) => m[1]!); // escaped characters
  s = s.replaceAll(RegExp(r'\\[,;:! ]|\\\\|~|&'), ' ').replaceAll(RegExp(r'[{}\\]'), '');
  return s.replaceAll(RegExp(r'\s+'), ' ').replaceAll(RegExp(r' ([,.;:)])'), r'$1').trim();
}

enum BlockKind { paragraph, heading, item, math }

class Block {
  const Block(this.kind, this.text, {this.level = 0, this.marker});
  final BlockKind kind;
  final String text;

  /// A heading's level (1-3), or an item's indent (0, 1, ...).
  final int level;

  /// A numbered item's "1." (null: a bullet).
  final String? marker;

  @override
  String toString() => '$kind($level${marker == null ? '' : ' $marker'}: $text)';
}

final _heading = RegExp(r'^(#{1,3})\s+(.*)$');
final _bullet = RegExp(r'^(\s*)[-*•]\s+(.*)$');
final _numbered = RegExp(r'^(\s*)(\d{1,3}[.)])\s+(.*)$');

/// The text -> its blocks: paragraphs (lines kept as written), headings, list
/// items, and displayed maths.
List<Block> parseBlocks(String text) {
  final lines = text.replaceAll('\r\n', '\n').split('\n');
  final out = <Block>[];
  final para = <String>[];
  void flush() {
    if (para.isNotEmpty) out.add(Block(BlockKind.paragraph, para.join('\n')));
    para.clear();
  }

  for (var i = 0; i < lines.length; i++) {
    final line = lines[i];
    final t = line.trim();
    // a displayed equation: $$ ... $$ or \[ ... \], on one line or several
    final open = t.startsWith(r'$$') ? r'$$' : (t.startsWith(r'\[') ? r'\[' : null);
    if (open != null) {
      final close = open == r'$$' ? r'$$' : r'\]';
      final rest = t.substring(2);
      final end = rest.indexOf(close);
      if (end >= 0 && rest.substring(end + 2).trim().isEmpty) {
        flush();
        out.add(Block(BlockKind.math, rest.substring(0, end).trim()));
        continue;
      }
      if (end < 0) {
        // look ahead for the closing line; without one it's just text
        final body = <String>[rest];
        var j = i + 1;
        for (; j < lines.length; j++) {
          final k = lines[j].indexOf(close);
          if (k >= 0) {
            body.add(lines[j].substring(0, k));
            break;
          }
          body.add(lines[j]);
        }
        if (j < lines.length && lines[j].substring(lines[j].indexOf(close) + 2).trim().isEmpty) {
          flush();
          out.add(Block(BlockKind.math, body.join('\n').trim()));
          i = j;
          continue;
        }
      }
    }
    if (t.isEmpty) {
      flush();
      continue;
    }
    final h = _heading.firstMatch(t);
    if (h != null) {
      flush();
      out.add(Block(BlockKind.heading, h.group(2)!.replaceAll(RegExp(r'\*\*'), ''), level: h.group(1)!.length));
      continue;
    }
    final n = _numbered.firstMatch(line);
    if (n != null) {
      flush();
      out.add(Block(BlockKind.item, n.group(3)!, level: n.group(1)!.length ~/ 2, marker: n.group(2)));
      continue;
    }
    final b = _bullet.firstMatch(line);
    if (b != null && !t.startsWith('**')) {
      flush();
      out.add(Block(BlockKind.item, b.group(2)!, level: b.group(1)!.length ~/ 2));
      continue;
    }
    para.add(line);
  }
  flush();
  return out;
}

final _inline = RegExp(
  r'\$\$(.+?)\$\$' // 1: display maths inside a line
  r'|\\\((.+?)\\\)' // 2: \( ... \)
  r'|(?<![\\$\w])\$(?=\S)([^$\n]*?\S)\$(?!\d)' // 3: $ ... $
  r'|\*\*(.+?)\*\*' // 4: bold
  r'|(?<![*\w])\*(?=\S)([^*\n]*?\S)\*(?![*\w])' // 5: italic
  r'|`([^`\n]+)`', // 6: code
);

/// One paragraph's text -> spans: words, typeset maths, bold, italic, code.
List<InlineSpan> inlineSpans(String text, TextStyle style) {
  final out = <InlineSpan>[];
  var at = 0;
  for (final m in _inline.allMatches(text)) {
    if (m.start > at) out.add(TextSpan(text: text.substring(at, m.start), style: style));
    final tex = m.group(1) ?? m.group(2) ?? m.group(3);
    if (tex != null) {
      out.add(WidgetSpan(
        alignment: PlaceholderAlignment.middle,
        child: Math.tex(
          tex.trim(),
          mathStyle: m.group(1) != null ? MathStyle.display : MathStyle.text,
          textStyle: style.copyWith(fontSize: (style.fontSize ?? 14.5) * 1.05, height: 1.0),
          onErrorFallback: (_) => Text(texToPlain(tex), style: style),
        ),
      ));
    } else if (m.group(4) != null) {
      out.addAll(inlineSpans(m.group(4)!, style.copyWith(fontWeight: FontWeight.w700)));
    } else if (m.group(5) != null) {
      out.addAll(inlineSpans(m.group(5)!, style.copyWith(fontStyle: FontStyle.italic)));
    } else {
      out.add(TextSpan(
        text: m.group(6),
        style: style.copyWith(
          fontFamily: 'monospace',
          fontSize: (style.fontSize ?? 14.5) * 0.92,
          backgroundColor: Paper.sliver,
        ),
      ));
    }
    at = m.end;
  }
  if (at < text.length) out.add(TextSpan(text: text.substring(at), style: style));
  return out;
}
