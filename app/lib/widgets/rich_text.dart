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
/// streams in shows as plain text until it closes.
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
              onErrorFallback: (_) => Text('\$\$${b.text}\$\$', style: base),
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
          onErrorFallback: (_) => Text(m.group(0)!, style: style),
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
