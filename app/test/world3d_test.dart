import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/stage/engine.dart';
import 'package:versa_app/stage/script.dart';
import 'package:versa_app/stage/solids.dart';
import 'package:versa_app/stage/stage_view.dart';

Future<void> _run(StageEngine e, double seconds) async {
  for (var i = 0; i < (seconds * 60).ceil(); i++) {
    e.tick(1 / 60);
    await Future<void>.delayed(Duration.zero);
  }
}

void main() {
  test('the stage turns 3D: far things are smaller and nearer the horizon', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'world', 'mode': '3d'},
      {'do': 'spawn', 'id': 'near', 'kind': 'cube', 'x': 0.5, 'z': 0},
      {'do': 'spawn', 'id': 'far', 'kind': 'cube', 'x': 0.5, 'z': 1},
    ]));
    await _run(e, 2);
    expect(e.worldT, 1);
    final (nearAt, nearScale, _) = e.solidPlace(e.props['near']!);
    final (farAt, farScale, farDepth) = e.solidPlace(e.props['far']!);
    expect(farScale, lessThan(nearScale));
    expect(farAt.dy, lessThan(nearAt.dy), reason: 'toward the horizon');
    expect(farAt.dy, greaterThan(StageEngine.horizonY));
    expect(farDepth, 1);

    e.play(parseScript([{'do': 'world', 'mode': 'flat'}]));
    await _run(e, 1.2);
    expect(e.worldT, 0);
  });

  test('an orbit passes behind and in front of what it circles', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'sun', 'kind': 'sphere', 'x': 0.5},
      {'do': 'spawn', 'id': 'moon', 'kind': 'sphere', 'x': 0.8, 'size': 0.4},
      {'do': 'orbit', 'target': 'moon', 'around': 'sun', 'radius': 0.2, 'turns': 1, 'ms': 4000},
    ]));
    await _run(e, 1.0 + 0.7); // spawns, then a quarter turn in
    final moon = e.props['moon']!;
    expect(moon.around, 'sun');
    final (_, _, quarter) = e.solidPlace(moon);
    expect(quarter, greaterThan(0), reason: 'behind the sun at a quarter turn');
    await _run(e, 2.0); // three quarters
    final (_, _, threeQuarters) = e.solidPlace(moon);
    expect(threeQuarters, lessThan(0), reason: 'in front at three quarters');
  });

  test('turn rotates a solid, and spin keeps it turning', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'c', 'kind': 'cube', 'x': 0.5, 'yaw': 0, 'spin': 0.5},
      {'do': 'turn', 'target': 'c', 'yaw': 1, 'pitch': 0.25, 'ms': 1000},
    ]));
    await _run(e, 1.6);
    final c = e.props['c']!;
    expect(c.yaw.at(e.time), closeTo(1, 1e-9));
    expect(c.pitch.at(e.time), closeTo(0.25, 1e-9));
    expect(c.spinRate, 0.5);
  });

  test('every solid paints, from any angle, without error', () {
    for (final kind in SolidKind.values) {
      for (final yaw in [0.0, 1.3, 3.0]) {
        final recorder = ui.PictureRecorder();
        paintSolid(Canvas(recorder), const Offset(100, 100), 80, kind,
            yaw: yaw, pitch: yaw / 2, color: const Color(0xFF4F7CAC), time: yaw);
        recorder.endRecording().dispose();
      }
    }
    // a cube seen from above-front shows a few faces, never all six
    final visible = [
      for (final f in meshFor(SolidKind.cube)!.faces)
        () {
          final pts = [for (final i in f) turnPoint(meshFor(SolidKind.cube)!.vertices[i], 0.4, 0)];
          return (pts[1] - pts[0]).cross(pts[2] - pts[0]).z > 0;
        }()
    ].where((v) => v).length;
    expect(visible, inInclusiveRange(2, 3));
  });

  testWidgets('a 3D scene with every solid renders on the stage', (tester) async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'world', 'mode': '3d'},
      for (final (i, kind) in SolidKind.values.indexed)
        {'do': 'spawn', 'id': kind.name, 'kind': kind.name, 'x': 0.1 + i * 0.1, 'z': (i % 3) / 3, 'lift': i.isEven ? 0.05 : 0},
      {'do': 'orbit', 'target': 'sphere', 'around': 'planet', 'radius': 0.1, 'turns': 2, 'ms': 3000},
    ]));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SizedBox(width: 800, height: 500, child: StageView(engine: e, animate: false))),
    ));
    for (var i = 0; i < 300; i++) {
      e.tick(1 / 60);
      await tester.pump();
    }
    expect(tester.takeException(), isNull);
    expect(e.props.length, SolidKind.values.length);
  });

  test('the stage is a room from the start: anything can stand deeper in it', () async {
    final e = StageEngine()..speed = 1;
    expect(e.worldT, 1);
    e.play(parseScript([
      {'do': 'spawn', 'id': 'front', 'kind': 'box', 'x': 0.7},
      {'do': 'spawn', 'id': 'back', 'kind': 'box', 'x': 0.7, 'z': 0.8},
    ]));
    await _run(e, 2);
    final front = e.props['front']!, back = e.props['back']!;
    expect(e.propScale(back), lessThan(e.propScale(front)));
    expect(e.propAt(back).dy, lessThan(e.propAt(front).dy), reason: 'toward the horizon');
    expect(e.propAt(front), const Offset(0.7, kGroundY), reason: 'the front row does not move');
  });

  test('the slime hops aside when something appears on top of it', () async {
    final e = StageEngine()..speed = 1;
    final start = e.blob.base(0).dx;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'c', 'kind': 'cube', 'x': start, 'size': 1.2},
    ]));
    await _run(e, 2.5);
    final slime = e.blob.base(e.time).dx;
    expect((slime - start).abs(), greaterThan(0.05), reason: 'it moved out of the way');
    final cube = e.propBounds(e.props['c']!)!;
    final w = e.blobRadius * 1.25 / e.size.width;
    expect(slime - w >= cube.right || slime + w <= cube.left, isTrue, reason: 'and now stands clear of it');
  });

  test('something appearing elsewhere does not move the slime', () async {
    final e = StageEngine()..speed = 1;
    final start = e.blob.base(0).dx;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'c', 'kind': 'cube', 'x': 0.8},
    ]));
    await _run(e, 2);
    expect(e.blob.base(e.time).dx, start);
  });

  test('a new scene steps the last one back, and using a thing again brings it forward', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'rocket', 'kind': 'emoji', 'label': '🚀', 'x': 0.7},
      {'do': 'spawn', 'id': 'clock', 'kind': 'emoji', 'label': '⏰', 'x': 0.85},
      {'do': 'scene'},
    ]));
    await _run(e, 2.5);
    final rocket = e.props['rocket']!, clock = e.props['clock']!;
    expect(rocket.dim.at(e.time), lessThan(1), reason: 'dimmed, still there');
    expect(rocket.z, greaterThan(0.3), reason: 'stepped back into the room');
    expect(e.props.length, 2);

    e.play(parseScript([
      {'do': 'relabel', 'target': 'rocket', 'label': '🛸'},
      {'do': 'wait', 'ms': 800},
    ]));
    await _run(e, 1);
    expect(rocket.dim.at(e.time), 1);
    expect(rocket.z, closeTo(0, 1e-9), reason: 'back where it was put');
    expect(clock.receded, isTrue, reason: 'the one not used stays back');
  });

  test('the slime watches a thing it moves across the stage', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'r', 'kind': 'ball', 'x': 0.7},
      {'do': 'look', 'x': 0.0},
      {'do': 'move', 'target': 'r', 'x': 0.9, 'ms': 1000},
    ]));
    await _run(e, 1.2);
    expect(e.lookX, closeTo(e.propAt(e.props['r']!).dx, 1e-9));
  });

  test('an emoji keeps its glyph and takes words as a caption', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'clock', 'kind': 'emoji', 'label': '⏰', 'caption': 't = 0', 'x': 0.8},
      {'do': 'relabel', 'target': 'clock', 'label': '1 min passed'},
      {'do': 'relabel', 'target': 'clock', 'label': '⏱️'},
    ]));
    await _run(e, 1.5);
    final clock = e.props['clock']!;
    expect(clock.caption, '1 min passed');
    expect(clock.label, '⏱️');
  });

  test('a link joins two things, and goes when either end does', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'sun', 'kind': 'emoji', 'label': '☀️', 'x': 0.6, 'y': 0.2},
      {'do': 'spawn', 'id': 'pot', 'kind': 'emoji', 'label': '🍲', 'x': 0.85},
      {'do': 'spawn', 'id': 'heat', 'kind': 'link', 'on': 'sun', 'to': 'pot', 'label': 'heat'},
    ]));
    await _run(e, 2);
    expect(e.props['heat']!.to, 'pot');
    expect(e.propBounds(e.props['heat']!), isNull, reason: 'a link never makes the slime step aside');
    e.play(parseScript([{'do': 'remove', 'id': 'pot'}]));
    await _run(e, 1);
    expect(e.props.containsKey('heat'), isFalse);
    expect(e.props.containsKey('sun'), isTrue);
  });

  test('the camera eases in on what the slime watches, and back out when it asks', () async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      {'do': 'spawn', 'id': 'r', 'kind': 'emoji', 'label': '🚀', 'x': 0.8},
      {'do': 'wait', 'ms': 3000},
      {'do': 'ask', 'question': 'q?', 'choices': [{'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}]},
    ]));
    await _run(e, 0.1);
    final early = e.camZoom;
    await _run(e, 2.5);
    expect(e.camZoom, greaterThan(early), reason: 'eased in, not cut');
    expect(e.camZoom, greaterThan(1.05));
    expect(e.camFocus.dx, greaterThan(0.5), reason: 'toward the rocket');
    expect(e.camFocus.dx, lessThanOrEqualTo(0.5 + (1 - 1 / e.camZoom) / 2 + 1e-9), reason: 'edges stay covered');
    await _run(e, 4);
    expect(e.asking, isTrue);
    expect(e.camZoom, lessThan(1.01), reason: 'back out for the question');
  });

  testWidgets('every entrance, a link, a trail and an orbit path paint', (tester) async {
    final e = StageEngine()..speed = 1;
    e.play(parseScript([
      for (final (i, enter) in ['pop', 'drop', 'rise', 'swoop'].indexed)
        {'do': 'spawn', 'id': 'b$i', 'kind': 'box', 'x': 0.2 + i * 0.2, 'enter': enter},
      {'do': 'spawn', 'id': 'l', 'kind': 'link', 'on': 'b0', 'to': 'b3', 'label': 'flow'},
      {'do': 'spawn', 'id': 'sun', 'kind': 'sphere', 'x': 0.5, 'z': 0.5},
      {'do': 'spawn', 'id': 'moon', 'kind': 'sphere', 'x': 0.7, 'z': 0.5, 'size': 0.4},
      {'do': 'orbit', 'target': 'moon', 'around': 'sun', 'radius': 0.15, 'turns': 1, 'ms': 2000},
      {'do': 'move', 'target': 'b1', 'x': 0.9, 'ms': 800},
    ]));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SizedBox(width: 800, height: 500, child: StageView(engine: e, animate: false))),
    ));
    for (var i = 0; i < 360; i++) {
      e.tick(1 / 60);
      await tester.pump();
    }
    expect(tester.takeException(), isNull);
  });
}
