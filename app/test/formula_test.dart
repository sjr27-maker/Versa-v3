import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/stage/formula.dart';

void main() {
  double f(String src, double x) => parseFormula(src)!(x);

  test('reads the formulas a stage needs', () {
    expect(f('x^2', 3), 9);
    expect(f('x^2 - 3x + 1', 2), -1);
    expect(f('2x', 4), 8);
    expect(f('3(x+1)', 1), 6);
    expect(f('x(x-1)', 3), 6);
    expect(f('-x^2', 2), -4, reason: 'the power binds tighter than the minus');
    expect(f('2^x', 3), 8);
    expect(f('sin(x)', math.pi / 2), closeTo(1, 1e-9));
    expect(f('sinx', math.pi / 2), closeTo(1, 1e-9), reason: 'no spaces is fine');
    expect(f('2pix', 1), closeTo(2 * math.pi, 1e-9));
    expect(f('sqrt(abs(x))', -9), 3);
    expect(f('e^x', 0), 1);
    expect(f('1/x', 4), 0.25);
    expect(f('ln(x)', math.e), closeTo(1, 1e-9));
  });

  test('refuses anything else rather than guessing', () {
    for (final bad in ['', 'x^', '(x+1', 'y+1', 'import(x)', 'x;x', 'x' * 90, 'eval(x)']) {
      expect(parseFormula(bad), isNull, reason: bad);
    }
  });

  test('slope by central difference', () {
    expect(slopeAt(parseFormula('x^2')!, 3), closeTo(6, 1e-4));
  });
}
