import 'dart:math' as math;

/// A tiny, safe formula reader for the stage's graph kit: turns text like
/// `x^2 - 3x + 1`, `sin(2x)` or `sqrt(abs(x))` into a function of x.
///
/// Numbers, `x`, `+ - * / ^`, parentheses, implicit multiplication (`2x`,
/// `3(x+1)`, `x(x-1)`), the constants `pi` and `e`, and a fixed set of
/// functions. Anything else is refused (null), never guessed at: a script
/// from a model can make a dull graph, never run code.
typedef Formula = double Function(double x);

const _functions = <String, double Function(double)>{
  'sin': math.sin,
  'cos': math.cos,
  'tan': math.tan,
  'exp': math.exp,
  'log': math.log,
  'ln': math.log,
  'sqrt': math.sqrt,
  'abs': _abs,
};

double _abs(double v) => v.abs();

final _known = {'x', 'pi', 'e', ..._functions.keys};

Formula? parseFormula(String? source) {
  if (source == null || source.trim().isEmpty || source.length > 80) return null;
  try {
    final p = _Parser(source.toLowerCase().replaceAll(' ', ''));
    final f = p.expression();
    if (!p.done) return null;
    return f;
  } on FormatException {
    return null;
  }
}

class _Parser {
  _Parser(this.s);
  final String s;
  int i = 0;

  bool get done => i >= s.length;
  String? get peek => done ? null : s[i];

  bool _eat(String c) {
    if (peek == c) {
      i++;
      return true;
    }
    return false;
  }

  // expression := term (('+'|'-') term)*
  Formula expression() {
    var left = term();
    while (true) {
      if (_eat('+')) {
        final a = left, b = term();
        left = (x) => a(x) + b(x);
      } else if (_eat('-')) {
        final a = left, b = term();
        left = (x) => a(x) - b(x);
      } else {
        return left;
      }
    }
  }

  // term := unary (('*'|'/')? unary)*   -- a missing operator multiplies (2x)
  Formula term() {
    var left = unary();
    while (true) {
      if (_eat('*')) {
        final a = left, b = unary();
        left = (x) => a(x) * b(x);
      } else if (_eat('/')) {
        final a = left, b = unary();
        left = (x) => a(x) / b(x);
      } else if (!done && _startsFactor(peek!)) {
        final a = left, b = power();
        left = (x) => a(x) * b(x);
      } else {
        return left;
      }
    }
  }

  bool _startsFactor(String c) => c == '(' || c == 'x' || _isLetter(c) || _isDigit(c) || c == '.';

  // unary := '-' unary | power
  Formula unary() {
    if (_eat('-')) {
      final a = unary();
      return (x) => -a(x);
    }
    if (_eat('+')) return unary();
    return power();
  }

  // power := atom ('^' unary)?   (right-associative)
  Formula power() {
    final base = atom();
    if (_eat('^')) {
      final exp = unary();
      return (x) => math.pow(base(x), exp(x)).toDouble();
    }
    return base;
  }

  Formula atom() {
    final c = peek;
    if (c == null) throw const FormatException('unexpected end');
    if (_eat('(')) {
      final inner = expression();
      if (!_eat(')')) throw const FormatException('missing )');
      return inner;
    }
    if (_isDigit(c) || c == '.') {
      final start = i;
      while (!done && (_isDigit(peek!) || peek == '.')) {
        i++;
      }
      final v = double.parse(s.substring(start, i));
      return (_) => v;
    }
    if (_isLetter(c)) {
      final start = i;
      while (!done && _isLetter(peek!)) {
        i++;
      }
      var word = s.substring(start, i);
      // no spaces, so "sinx" or "pix" run together: take the longest known
      // name at the front and leave the rest for the next factor
      if (!_known.contains(word)) {
        final head = (_known.toList()..sort((a, b) => b.length.compareTo(a.length)))
            .where(word.startsWith)
            .firstOrNull;
        if (head == null) throw FormatException('unknown name $word');
        word = head;
        i = start + head.length;
      }
      if (word == 'x') return (x) => x;
      if (word == 'pi') return (_) => math.pi;
      if (word == 'e') return (_) => math.e;
      final fn = _functions[word];
      if (fn != null) {
        final arg = _eat('(') ? _closeParen(expression()) : power();
        return (x) => fn(arg(x));
      }
      throw FormatException('unknown name $word');
    }
    throw FormatException('unexpected $c');
  }

  Formula _closeParen(Formula f) {
    if (!_eat(')')) throw const FormatException('missing )');
    return f;
  }

  static bool _isDigit(String c) => c.codeUnitAt(0) >= 48 && c.codeUnitAt(0) <= 57;
  static bool _isLetter(String c) => c.codeUnitAt(0) >= 97 && c.codeUnitAt(0) <= 122;
}

/// The slope of [f] at [x] (central difference).
double slopeAt(Formula f, double x, {double h = 1e-4}) => (f(x + h) - f(x - h)) / (2 * h);
