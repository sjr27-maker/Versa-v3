import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/models.dart';
import 'package:versa_app/stage/engine.dart';
import 'package:versa_app/stage/script.dart';
import 'package:versa_app/stage/skits.dart';
import 'package:versa_app/widgets/stage_panel.dart';

import 'support/fakes.dart';

/// Step the engine's clock by hand, [seconds] in 1/60 s frames, letting the
/// awaiting script continue between frames.
Future<void> _run(StageEngine e, double seconds) async {
  final frames = (seconds * 60).ceil();
  for (var i = 0; i < frames; i++) {
    e.tick(1 / 60);
    await Future<void>.delayed(Duration.zero);
  }
}

Widget _host(Widget child) => MaterialApp(
      home: Scaffold(body: SizedBox(width: 700, height: 600, child: child)),
    );

void main() {
  group('script', () {
    test('parses every action from plain JSON', () {
      final script = parseScript([
        {'do': 'spawn', 'id': 'b', 'kind': 'ball', 'x': 0.4, 'label': 'mass'},
        {'do': 'move', 'target': 'b', 'x': 0.8, 'style': 'slide', 'ms': 300},
        {'do': 'approach', 'target': 'b'},
        {'do': 'push', 'target': 'b', 'dx': -0.1},
        {'do': 'emote', 'mood': 'excited'},
        {'do': 'say', 'text': 'hi'},
        {'do': 'look', 'x': 0.1},
        {'do': 'jump', 'times': 2},
        {'do': 'shake', 'target': 'blob'},
        {'do': 'remove', 'id': 'b'},
        {'do': 'wait', 'ms': 10},
        {'do': 'together', 'actions': [{'do': 'wait'}]},
        {
          'do': 'ask',
          'question': 'which?',
          'choices': [{'id': 'a', 'text': 'A'}],
          'then': {'a': [{'do': 'jump'}]},
        },
      ]);
      expect(script.map((a) => a.runtimeType.toString()), [
        'SpawnAction', 'MoveAction', 'ApproachAction', 'PushAction', 'EmoteAction', //
        'SayAction', 'LookAction', 'JumpAction', 'ShakeAction', 'RemoveAction',
        'WaitAction', 'TogetherAction', 'AskAction',
      ]);
      final spawn = script.first as SpawnAction;
      expect(spawn.kind, PropKind.ball);
      expect(spawn.y, kGroundY, reason: 'props default to standing on the ground');
      expect((script[4] as EmoteAction).mood, Mood.excited);
      expect((script.last as AskAction).then['a'], hasLength(1));
    });

    test('an unknown action degrades to a no-op instead of throwing', () {
      final script = parseScript([
        {'do': 'teleport_to_mars'},
        {'do': 'emote', 'mood': 'not-a-mood'},
        'garbage',
      ]);
      expect(script, hasLength(2));
      expect(script.first, isA<WaitAction>());
      expect((script.last as EmoteAction).mood, Mood.neutral);
    });
  });

  group('engine', () {
    test('spawn, move and remove change the world over time', () async {
      final e = StageEngine();
      final done = e.play(parseScript([
        {'do': 'spawn', 'id': 'b', 'kind': 'box', 'x': 0.5},
        {'do': 'move', 'target': 'b', 'x': 0.9, 'style': 'slide', 'ms': 500},
        {'do': 'move', 'target': 'blob', 'x': 0.1, 'ms': 400},
      ]));
      await _run(e, 0.1);
      expect(e.props.keys, ['b']);
      await _run(e, 1.5);
      await done;
      expect(e.props['b']!.pos.base(e.time).dx, closeTo(0.9, 1e-9));
      expect(e.blob.base(e.time).dx, closeTo(0.1, 1e-9));
      expect(e.running, isFalse);

      e.play(parseScript([{'do': 'remove', 'id': 'b'}]));
      await _run(e, 1);
      expect(e.props, isEmpty, reason: 'poofed away after its fade');
    });

    test('a push moves the prop and the blob together, then the blob relaxes', () async {
      final e = StageEngine();
      final done = e.play(parseScript([
        {'do': 'spawn', 'id': 'b', 'kind': 'box', 'x': 0.5},
        {'do': 'push', 'target': 'b', 'dx': 0.2, 'ms': 600},
      ]));
      var sawStrain = false;
      for (var i = 0; i < 240 && e.running; i++) {
        await _run(e, 1 / 60);
        sawStrain |= e.mood == Mood.strain;
      }
      await done;
      expect(sawStrain, isTrue);
      expect(e.mood, isNot(Mood.strain));
      expect(e.props['b']!.pos.base(e.time).dx, closeTo(0.7, 1e-9));
      expect(e.blob.base(e.time).dx, lessThan(0.7), reason: 'still behind the box it pushed');
    });

    test('a skit ask waits for an answer, then runs that branch', () async {
      final e = StageEngine();
      final done = e.play(parseScript([
        {
          'do': 'ask',
          'question': 'Heavier or lighter?',
          'choices': [
            {'id': 'h', 'text': 'Heavier'},
            {'id': 'l', 'text': 'Lighter'},
          ],
          'then': {
            'l': [{'do': 'spawn', 'id': 'feather', 'kind': 'star', 'x': 0.5}],
          },
        },
      ]));
      await _run(e, 2);
      expect(e.asking, isTrue);
      expect(e.question!.choices.map((c) => c.text), ['Heavier', 'Lighter']);
      expect(e.mood, Mood.confused);
      expect(e.running, isTrue, reason: 'blocked on the answer');

      e.answer('l');
      await _run(e, 1);
      await done;
      expect(e.asking, isFalse);
      expect(e.props.keys, ['feather']);
    });

    test('a chat question outranks a playing skit', () async {
      final e = StageEngine();
      e.play(forceSkit);
      await _run(e, 1);
      expect(e.running, isTrue);

      e.ask(const StageQuestion(
          key: 'msg-3', text: 'Which force?', choices: [StageChoice(id: 'o1', text: 'Gravity')]));
      await _run(e, 0.2);
      expect(e.running, isFalse);
      expect(e.question!.key, 'msg-3');
    });

    test('carry, throw, drop, scale, spin, relabel, effects and hats', () async {
      final e = StageEngine();
      final done = e.play(parseScript([
        {'do': 'spawn', 'id': 'a', 'kind': 'emoji', 'label': 'A', 'x': 0.6},
        {'do': 'carry', 'target': 'a'},
      ]));
      await _run(e, 3);
      await done;
      final a = e.props['a']!;
      expect(a.carried, isTrue);
      expect(e.propAt(a).dy, lessThan(e.blobPos.dy), reason: 'riding on its head');

      e.play(parseScript([
        {'do': 'throw', 'target': 'a', 'x': 0.9, 'ms': 400},
        {'do': 'scale', 'target': 'a', 'to': 2, 'ms': 200},
        {'do': 'spin', 'target': 'a', 'turns': 1, 'ms': 200},
        {'do': 'relabel', 'target': 'a', 'label': 'B'},
        {'do': 'wear', 'label': 'H'},
        {'do': 'effect', 'kind': 'confetti'},
        {'do': 'effect', 'kind': 'rain', 'x': 0.5, 'ms': 500},
      ]));
      await _run(e, 1.0);
      expect(a.carried, isFalse);
      expect(e.propAt(a).dx, closeTo(0.9, 1e-9));
      expect(a.scale.at(e.time), closeTo(2, 1e-9));
      expect(a.spin.at(e.time), closeTo(2, 1e-9), reason: 'the throw spun it once too');
      expect(a.label, 'B');
      expect(e.hat, 'H');
      expect(e.particles, isNotEmpty);
      await _run(e, 4);
      expect(e.particles, isEmpty, reason: 'bursts fade and emitters stop');
    });

    test('an unknown prop kind still shows up, as its name', () {
      final spawn = parseScript([
        {'do': 'spawn', 'id': 'v', 'kind': 'volcano'},
      ]).single as SpawnAction;
      expect(spawn.kind, PropKind.text);
      expect(spawn.label, 'volcano');
    });

    test('a live performance plays actions as they arrive, then ends', () async {
      final e = StageEngine();
      e.beginLive();
      expect(e.running, isTrue, reason: 'waiting for the first action');
      e.enqueue(StageAction.fromJson({'do': 'spawn', 'id': 'a', 'kind': 'emoji', 'label': 'A', 'x': 0.7}));
      await _run(e, 0.6);
      expect(e.props.keys, ['a'], reason: 'played before the script is complete');

      e.enqueue(StageAction.fromJson({'do': 'ask', 'question': '?', 'choices': [{'id': 'x', 'text': 'x'}]}));
      e.enqueue(StageAction.fromJson({'do': 'emote', 'mood': 'excited'}));
      await _run(e, 0.2);
      expect(e.asking, isFalse, reason: 'a script never asks; the chat does');
      expect(e.mood, Mood.excited);

      e.endLive();
      await _run(e, 0.2);
      expect(e.running, isFalse);

      e.beginLive();
      await _run(e, 1);
      expect(e.props, isEmpty, reason: 'a new performance clears the old one');
      e.endLive();
    });

    test('the "I remember" gag plays, and the performance joins it instead of wiping it', () async {
      final e = StageEngine();
      e.recalled(retracted: true);
      await _run(e, 0.6);
      expect(e.props.containsKey('_bulb'), isTrue, reason: 'the lightbulb moment');
      expect(e.particles, isNotEmpty);

      e.beginLive(); // the answer's performance starts mid-gag
      e.enqueue(StageAction.fromJson({'do': 'spawn', 'id': 'next', 'kind': 'emoji', 'label': 'N', 'x': 0.8}));
      e.endLive();
      expect(e.props.containsKey('_bulb'), isTrue, reason: 'not reset');
      for (var i = 0; i < 40 && e.running; i++) {
        await _run(e, 0.25);
      }
      expect(e.props.containsKey('next'), isTrue, reason: 'played after the gag');
      expect(e.props.containsKey('_bulb'), isFalse);
    });

    test('a gag with no performance after it winds down by itself', () async {
      final e = StageEngine();
      e.recalled(retracted: false);
      for (var i = 0; i < 60 && e.running; i++) {
        await _run(e, 0.25);
      }
      expect(e.running, isFalse);
    });

    test('the gravity skit plays start to finish down either branch', () async {
      for (final pick in ['yes', 'no']) {
        final e = StageEngine();
        final done = e.play(gravitySkit);
        for (var i = 0; i < 80 && !e.asking; i++) {
          await _run(e, 0.5);
        }
        expect(e.asking, isTrue);
        e.answer(pick);
        for (var i = 0; i < 80 && e.running; i++) {
          await _run(e, 0.5);
        }
        await done;
        expect(e.hat, '👑', reason: '$pick branch reaches the ending');
      }
    });

    test('the demo skit plays start to finish down either branch', () async {
      for (final pick in ['harder', 'lighter']) {
        final e = StageEngine();
        final done = e.play(forceSkit);
        for (var i = 0; i < 60 && !e.asking; i++) {
          await _run(e, 0.5);
        }
        expect(e.asking, isTrue, reason: 'the skit asks what to change');
        e.answer(pick);
        for (var i = 0; i < 60 && e.running; i++) {
          await _run(e, 0.5);
        }
        await done;
        expect(e.props.containsKey('law'), isTrue, reason: '$pick branch reaches the ending');
      }
    });
  });

  group('stage panel', () {
    testWidgets('the blob asks the chat\'s options beneath itself; a tap picks it', (tester) async {
      final backend = FakeBackend();
      final transport = FakeTransport();
      final chat = ChatController(
        api: backend.api,
        learner: const Learner(id: 'l1', label: 'Asha'),
        transportFactory: (_) async => transport,
      );
      await tester.runAsync(chat.start);

      await tester.pumpWidget(_host(StagePanel(chat: chat, onCollapse: () {})));
      expect(find.byKey(const ValueKey('stage-choices')), findsNothing);

      chat.send('tell me about force');
      transport.emit(const OptionsEvent('Did you mean physics or feelings?', [
        ChatOption(id: 'o1', text: 'Force in physics'),
        ChatOption(id: 'o2', text: 'Forcing someone'),
      ]));
      transport.emit(const Done(
          turnIndex: 0,
          kind: 'options',
          text: 'Did you mean physics or feelings?',
          firstOutputMs: 500,
          totalMs: 500));
      await tester.runAsync(() => Future<void>.delayed(Duration.zero));
      await tester.pump(const Duration(milliseconds: 600));

      expect(find.byKey(const ValueKey('stage-choices')), findsOneWidget);
      expect(find.text('Did you mean physics or feelings?'), findsOneWidget);
      expect(find.byKey(const ValueKey('stage-bubble-question')), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('stage-choice-o1')));
      await tester.pump(const Duration(milliseconds: 100));

      expect(transport.sent.last, {'type': 'select_option', 'option_id': 'o1', 'stage': 'true'});
      expect(find.byKey(const ValueKey('stage-choices')), findsNothing);
      chat.dispose();
    });

    testWidgets('options taken back by memory leave the stage and the chat', (tester) async {
      final backend = FakeBackend();
      final transport = FakeTransport();
      final chat = ChatController(
        api: backend.api,
        learner: const Learner(id: 'l1', label: 'Asha'),
        transportFactory: (_) async => transport,
      );
      await tester.runAsync(chat.start);
      await tester.pumpWidget(_host(StagePanel(chat: chat, onCollapse: () {})));

      chat.send('which rule applies here again?');
      transport.emit(const OptionsEvent('Which of these did you mean?', [
        ChatOption(id: 'o1', text: 'reading 0'),
        ChatOption(id: 'o2', text: 'reading 1'),
      ]));
      await tester.runAsync(() => Future<void>.delayed(Duration.zero));
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.byKey(const ValueKey('stage-choices')), findsOneWidget);

      transport.emit(const RecalledEvent(retracted: true));
      transport.emit(const Delta('The power rule. '));
      transport.emit(const Done(
          turnIndex: 0, kind: 'answer', text: 'The power rule.', firstOutputMs: 1, totalMs: 1));
      await tester.runAsync(() => Future<void>.delayed(Duration.zero));
      await tester.pump(const Duration(milliseconds: 100));

      expect(find.byKey(const ValueKey('stage-choices')), findsNothing);
      final reply = chat.messages.last;
      expect(reply.recalled, isTrue);
      expect(reply.hasOptions, isFalse);
      expect(reply.text, 'The power rule.');
      chat.dispose();
    });

    testWidgets('while it shows, turns ask the server to perform on it', (tester) async {
      final backend = FakeBackend();
      final transport = FakeTransport();
      final chat = ChatController(
        api: backend.api,
        learner: const Learner(id: 'l1', label: 'Asha'),
        transportFactory: (_) async => transport,
      );
      await tester.runAsync(chat.start);
      chat.send('before the stage');
      expect(transport.sent.last.containsKey('stage'), isFalse);
      transport.emit(const Done(turnIndex: 0, kind: 'answer', text: 'ok', firstOutputMs: 1, totalMs: 1));
      await tester.runAsync(() => Future<void>.delayed(Duration.zero));

      await tester.pumpWidget(_host(StagePanel(chat: chat, onCollapse: () {})));
      chat.send('why is the sky blue?');
      expect(transport.sent.last['stage'], 'true');

      await tester.pumpWidget(_host(const SizedBox()));
      expect(chat.stageEnabled, isFalse, reason: 'a hidden stage costs no calls');
      chat.dispose();
    });

    testWidgets('the demo button plays the skit and its ask shows choices', (tester) async {
      await tester.pumpWidget(_host(StagePanel(onCollapse: () {})));
      await tester.tap(find.byKey(const ValueKey('stage-demo')));
      await tester.pump();
      expect(find.byTooltip('Stop the skit'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('stage-demo')));
      await tester.pump();
      expect(find.byTooltip('Play the demo skit'), findsOneWidget);
    });
  });
}
