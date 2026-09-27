import 'package:flutter/material.dart';
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

/// Instruments (2026-09-28: "it speeds up one thing but there is no reference
/// of time"): the engine animates what a quantity is doing, so the stage can
/// SHOW it instead of the slime saying it.
void main() {
  test('a clock ticks at its rate', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'c', 'kind': 'clock', 'x': 0.8},
      {'do': 'spawn', 'id': 'half', 'kind': 'clock', 'x': 0.6, 'rate': 0.5},
    ]));
    await _run(e, 4);
    expect(e.props['c']!.reading, closeTo(4, 0.1));
    final half = e.props['half']!;
    expect(half.reading, closeTo((e.time - half.bornAt) * 0.5, 0.05), reason: 'half the rate, since it appeared');
  });

  test('a clock riding with a cruising ship falls behind the one at home', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'compare', 'left': 'At home', 'right': 'On the ship'},
      {'do': 'spawn', 'id': 'home', 'kind': 'clock', 'x': 0.25},
      {'do': 'spawn', 'id': 'ship', 'kind': 'emoji', 'label': '🚀', 'x': 0.75, 'y': 0.4},
      {'do': 'spawn', 'id': 'shipclock', 'kind': 'clock', 'x': 0.75, 'track': 'ship'},
      {'do': 'cruise', 'target': 'ship', 'speed': 0.9, 'ms': 4000},
    ]));
    await _run(e, 2);
    final home = e.props['home']!, ship = e.props['shipclock']!;
    final before = (home.reading, ship.reading);
    await _run(e, 3);
    expect(e.props['ship']!.speedNow, greaterThan(0.8));
    final homeGained = home.reading - before.$1, shipGained = ship.reading - before.$2;
    expect(shipGained, lessThan(homeGained * 0.6), reason: 'moving clocks run slow, and you can see it');
    expect(shipGained, greaterThan(0), reason: 'slow, not stopped');
  });

  test('a gauge reads what it tracks; a counter adds up the distance', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'car', 'kind': 'emoji', 'label': '🚗', 'x': 0.7},
      {'do': 'spawn', 'id': 'dial', 'kind': 'gauge', 'x': 0.85, 'y': 0.3, 'track': 'car'},
      {'do': 'spawn', 'id': 'odo', 'kind': 'counter', 'x': 0.5, 'y': 0.3, 'track': 'car', 'label': 'km'},
      {'do': 'cruise', 'target': 'car', 'speed': 0.6, 'ms': 3000},
    ]));
    await _run(e, 1.5);
    final startOdo = e.props['odo']!.reading;
    await _run(e, 2.5);
    expect(e.props['dial']!.display, closeTo(0.6, 0.08));
    expect(e.props['odo']!.reading, greaterThan(startOdo + 5));
    await _run(e, 3);
    expect(e.props['dial']!.display, lessThan(0.1), reason: 'the needle falls when it stops');
  });

  test('set eases a reading and a rate to new values', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 't', 'kind': 'thermometer', 'x': 0.8, 'value': 0.2},
      {'do': 'spawn', 'id': 'n', 'kind': 'counter', 'x': 0.5, 'rate': 0},
      {'do': 'set', 'target': 't', 'value': 0.9, 'ms': 1000},
      {'do': 'set', 'target': 'n', 'rate': 3, 'ms': 200},
      {'do': 'wait', 'ms': 2000},
    ]));
    await _run(e, 1.2);
    final mid = e.props['t']!.display;
    expect(mid, greaterThan(0.2));
    await _run(e, 3);
    expect(e.props['t']!.display, closeTo(0.9, 0.02));
    expect(e.props['n']!.reading, greaterThan(4));
  });

  test('a label put on a thing moves with it', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'ship', 'kind': 'emoji', 'label': '🚀', 'x': 0.7},
      {'do': 'spawn', 'id': 'v', 'kind': 'math', 'tex': 'v', 'on': 'ship'},
      {'do': 'move', 'target': 'ship', 'x': 0.9, 'ms': 600},
    ]));
    await _run(e, 0.5);
    final before = e.propAt(e.props['v']!);
    expect(before.dx, closeTo(e.propAt(e.props['ship']!).dx, 1e-6));
    expect(before.dy, lessThan(e.propBounds(e.props['ship']!)!.top), reason: 'sits on top of it');
    await _run(e, 2);
    expect(e.propAt(e.props['v']!).dx, closeTo(0.9, 1e-6));
  });

  test('the plan names things that carry no words; compare puts the slime between the lanes', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'plan', 'cast': {'ship': 'the spaceship', 'home': 'home clock'}, 'shows': 'time'},
      {'do': 'compare', 'left': 'Home', 'right': 'Ship'},
      {'do': 'spawn', 'id': 'ship', 'kind': 'emoji', 'label': '🚀', 'x': 0.75},
      {'do': 'spawn', 'id': 'home', 'kind': 'clock', 'x': 0.25, 'caption': 'Earth'},
    ]));
    await _run(e, 3);
    expect(e.props['ship']!.caption, 'the spaceship');
    expect(e.props['home']!.caption, 'Earth', reason: 'its own words win');
    expect(e.compare.at(e.time), 1);
    expect((e.compareLeft, e.compareRight), ('Home', 'Ship'));
    expect(e.blob.base(e.time).dx, closeTo(0.5, 1e-6));
    e.play(parseScript([{'do': 'compare'}]));
    await _run(e, 1.2);
    expect(e.compare.at(e.time), 0);
  });

  testWidgets('every instrument, the lanes and a cruise paint', (tester) async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'compare', 'left': 'A', 'right': 'B'},
      for (final (i, k) in ['clock', 'stopwatch', 'counter', 'gauge', 'bar', 'thermometer'].indexed)
        {'do': 'spawn', 'id': k, 'kind': k, 'x': 0.1 + i * 0.16, 'y': i.isEven ? 0.62 : 0.35, 'label': 'x'},
      {'do': 'spawn', 'id': 'r', 'kind': 'emoji', 'label': '🚀', 'x': 0.5, 'y': 0.2},
      {'do': 'spawn', 'id': 'cube', 'kind': 'cube', 'x': 0.3, 'z': 0.5},
      {'do': 'together', 'actions': [
        {'do': 'cruise', 'target': 'r', 'speed': 0.9, 'ms': 2000},
        {'do': 'cruise', 'target': 'cube', 'speed': 0.5, 'ms': 2000},
      ]},
    ]));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SizedBox(width: 800, height: 500, child: StageView(engine: e, animate: false))),
    ));
    for (var i = 0; i < 400; i++) {
      e.tick(1 / 60);
      await tester.pump();
    }
    expect(tester.takeException(), isNull);
  });
}
