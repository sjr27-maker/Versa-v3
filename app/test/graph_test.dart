import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/stage/engine.dart';
import 'package:versa_app/stage/script.dart';
import 'package:versa_app/stage/stage_view.dart';

Future<void> _run(StageEngine e, double seconds) async {
  for (var i = 0; i < (seconds * 60).ceil(); i++) {
    e.tick(1 / 60);
    await Future<void>.delayed(Duration.zero);
  }
}

const _derivative = [
  {'do': 'spawn', 'id': 'ax', 'kind': 'axes', 'x': 0.4, 'y': 0.6, 'w': 0.5, 'h': 0.5,
   'xmin': -1, 'xmax': 4, 'ymin': -1, 'ymax': 9},
  {'do': 'spawn', 'id': 'f', 'kind': 'plot', 'on': 'ax', 'fn': 'x^2', 'tex': 'y = x^2'},
  {'do': 'spawn', 'id': 'p', 'kind': 'dot', 'on': 'f', 'at': 1, 'tex': 'P'},
  {'do': 'spawn', 'id': 't', 'kind': 'tangent', 'on': 'p', 'tex': r'\text{slope} = {slope}'},
];

void main() {
  test('a dot sits on its curve, in the axes\' own numbers, and slides along it', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript(_derivative));
    await _run(e, 2);
    final ax = e.props['ax']!, dot = e.props['p']!;
    // x = 1 on [-1, 4] across w 0.5 from x 0.4; y = 1 on [-1, 9] up h 0.5 from y 0.6
    expect(e.dotPoint(dot)!.dx, closeTo(0.4 + 2 / 5 * 0.5, 1e-9));
    expect(e.dotPoint(dot)!.dy, closeTo(0.6 - 2 / 10 * 0.5, 1e-9));
    expect(e.slopeUnder(e.props['t']!), closeTo(2, 1e-3));
    expect(e.axesOf(e.props['t']), same(ax));

    e.play(parseScript([{'do': 'slide', 'target': 'p', 'at': 3, 'ms': 1000}]));
    await _run(e, 1.2);
    expect(dot.at.at(e.time), closeTo(3, 1e-9));
    expect(e.slopeUnder(e.props['t']!), closeTo(6, 1e-3), reason: 'the tangent follows the point');
  });

  test('removing the axes takes the whole graph with it', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([..._derivative, {'do': 'remove', 'id': 'ax'}]));
    await _run(e, 3);
    expect(e.props, isEmpty);
  });

  test('a formula nobody can read draws nothing rather than guessing', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'ax', 'kind': 'axes', 'x': 0.4, 'y': 0.6},
      {'do': 'spawn', 'id': 'f', 'kind': 'plot', 'on': 'ax', 'fn': 'import os'},
      {'do': 'spawn', 'id': 'p', 'kind': 'dot', 'on': 'f', 'at': 1},
    ]));
    await _run(e, 1.5);
    expect(e.props['f']!.formula, isNull);
    expect(e.dotPoint(e.props['p']), isNull);
  });

  testWidgets('formulas and graph labels are typeset, the slope filled in live', (tester) async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      ..._derivative,
      {'do': 'spawn', 'id': 'eq', 'kind': 'math', 'tex': r'\frac{dy}{dx} = 2x', 'x': 0.2, 'y': 0.2},
    ]));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SizedBox(width: 800, height: 500, child: StageView(engine: e, animate: false))),
    ));
    for (var i = 0; i < 150; i++) {
      e.tick(1 / 60);
      await tester.pump();
    }
    expect(find.byKey(const ValueKey('tex-eq')), findsOneWidget);
    expect(find.byKey(const ValueKey('tex-f')), findsOneWidget, reason: 'the curve\'s equation, once drawn');
    final texts = tester.widgetList<Math>(find.byType(Math)).map((m) => m.parseError == null).toList();
    expect(texts, isNotEmpty);
    expect(texts.every((ok) => ok), isTrue, reason: 'every formula parsed as LaTeX');
    expect(find.byKey(const ValueKey('tex-t')), findsOneWidget);
  });
}
