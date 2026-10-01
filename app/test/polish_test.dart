import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/models.dart';
import 'package:versa_app/stage/engine.dart';
import 'package:versa_app/stage/painter.dart';
import 'package:versa_app/stage/script.dart';
import 'package:versa_app/stage/stage_view.dart';
import 'package:versa_app/widgets/composer.dart';
import 'package:versa_app/widgets/feed_cards.dart';
import 'package:versa_app/widgets/message_view.dart';
import 'package:versa_app/widgets/rich_text.dart';
import 'package:versa_app/widgets/scene_preview.dart';
import 'package:versa_app/widgets/stage_panel.dart';
import 'package:versa_app/widgets/stage_split.dart';

import 'support/fakes.dart';

/// The fixes of 2026-10-01: names on the stage printed over each other,
/// cards cut off on a phone, hiding the stage turning Animations off, raw
/// LaTeX in an answer, the keyboard staying up, and Home cards quoting the
/// first message.

Widget _host(Widget child, {Size size = const Size(400, 800)}) => MaterialApp(
      home: MediaQuery(
        data: MediaQueryData(size: size),
        child: Scaffold(body: SizedBox(width: size.width, height: size.height, child: child)),
      ),
    );

void _phone(WidgetTester tester, [Size size = const Size(400, 800)]) {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

ChatSummary _chat({String? preview, String? title, String? about, List<Map<String, dynamic>>? scene}) => ChatSummary(
      sessionId: 's1',
      appMode: 'sandbox',
      turnCount: 2,
      createdAt: DateTime.now(),
      lastActivityAt: DateTime.now(),
      preview: preview,
      title: title,
      about: about,
      scene: scene,
    );

/// A counter that shows whether its state survived being moved around.
class _Counter extends StatefulWidget {
  const _Counter();
  @override
  State<_Counter> createState() => _CounterState();
}

class _CounterState extends State<_Counter> {
  int taps = 0;
  @override
  Widget build(BuildContext context) => GestureDetector(
        key: const ValueKey('counter'),
        onTap: () => setState(() => taps++),
        child: Container(color: Colors.blue, alignment: Alignment.center, child: Text('taps $taps')),
      );
}

void main() {
  group('maths the typesetter cannot read', () {
    test('comes out as readable symbols, never raw LaTeX', () {
      expect(texToPlain(r'\text{Symbol: } \ \sim\hspace{-0.6em}\nearrow'), 'Symbol: ∼ ↗');
      expect(texToPlain(r'\frac{a + b}{2}'), '(a + b)/2');
      expect(texToPlain(r'I = \frac{\Delta Q}{\Delta t}'), 'I = (Δ Q)/(Δ t)');
      expect(texToPlain(r'x^{2} \leq 4'), 'x² ≤ 4');
      expect(texToPlain(r'R = 5\,\Omega'), 'R = 5 Ω');
      expect(texToPlain(r'90^\circ'), '90°');
      expect(texToPlain(r'\begin{array}{c} a \\ b \end{array}'), 'a b');
      for (final tex in [r'\zigzag{x}\hspace{2em}y', r'\sqrt{2}', r'H_{2}O', r'\sin \theta']) {
        expect(texToPlain(tex), isNot(anyOf(contains(r'\'), contains('{'), contains('}'))), reason: tex);
      }
    });

    testWidgets('a broken displayed equation shows no backslashes', (tester) async {
      await tester.pumpWidget(_host(const SingleChildScrollView(
        child: RichMessageText('Rheostat:\n\n\$\$\\text{Symbol: } \\sim \\notacommand{-0.6em}\$\$', selectable: false),
      )));
      await tester.pump();
      final shown = tester.widgetList<Text>(find.byType(Text)).map((t) => t.data ?? '').join(' ');
      expect(shown, contains('Symbol:'));
      expect(shown, isNot(contains(r'\')));
      expect(shown, isNot(contains(r'$$')));
    });
  });

  group('names on the stage', () {
    const size = Size(400, 400);

    test('a name with a place of its own stays where it is', () {
      expect(StagePainter.clearOf(const Rect.fromLTWH(10, 10, 60, 14), const [Rect.fromLTWH(200, 10, 60, 14)], size),
          Offset.zero);
    });

    test('a name that would print over another steps below it', () {
      final taken = [const Rect.fromLTWH(100, 200, 60, 14)];
      final shift = StagePainter.clearOf(const Rect.fromLTWH(130, 202, 60, 14), taken, size);
      expect(shift.dy, greaterThan(0));
      expect(const Rect.fromLTWH(130, 202, 60, 14).shift(shift).overlaps(taken.single), isFalse);
    });

    test('with no room below, it steps above; never off the stage', () {
      final taken = [const Rect.fromLTWH(100, 380, 60, 14)];
      final shift = StagePainter.clearOf(const Rect.fromLTWH(110, 382, 60, 14), taken, size);
      expect(shift.dy, lessThan(0));
    });

    test('three things side by side each get their name in full', () async {
      // what the screenshot showed: "second", "electron" and "charge" on top of each other
      final engine = StageEngine()..size = size;
      engine.beginLive();
      for (final a in parseScript([
        {'do': 'spawn', 'id': 'a', 'kind': 'ball', 'x': 0.45, 'label': 'second'},
        {'do': 'spawn', 'id': 'b', 'kind': 'ball', 'x': 0.50, 'label': 'electron'},
        {'do': 'spawn', 'id': 'c', 'kind': 'ball', 'x': 0.55, 'label': 'charge'},
      ])) {
        engine.enqueue(a);
      }
      engine.endLive();
      for (var i = 0; i < 120; i++) {
        engine.tick(1 / 60);
        await Future<void>.delayed(Duration.zero);
      }
      expect(engine.props.length, 3);
      final recorder = ui.PictureRecorder();
      StagePainter(engine).paint(Canvas(recorder), size); // must not throw
      recorder.endRecording();
      engine.dispose();
    });
  });

  group('the stage and the chat', () {
    testWidgets('just the chat: the stage is out of sight but keeps its state', (tester) async {
      _phone(tester);
      Widget split(StageSplitView view) => _host(StageSplit(
            wide: false,
            view: view,
            stage: (h) => SizedBox(height: h, child: const _Counter()),
            chat: const ColoredBox(key: ValueKey('chat'), color: Colors.white, child: SizedBox.expand()),
          ));
      await tester.pumpWidget(split(StageSplitView.split));
      await tester.tap(find.byKey(const ValueKey('counter')));
      await tester.pump();
      expect(find.text('taps 1'), findsOneWidget);

      await tester.pumpWidget(split(StageSplitView.chatOnly));
      expect(find.byKey(const ValueKey('counter')), findsNothing, reason: 'hidden');
      expect(find.byKey(const ValueKey('counter'), skipOffstage: false), findsOneWidget, reason: 'but kept');
      expect(find.byKey(const ValueKey('stage-resize')), findsNothing);
      expect(tester.getSize(find.byKey(const ValueKey('chat'))).height, 800);

      await tester.pumpWidget(split(StageSplitView.split));
      expect(find.text('taps 1'), findsOneWidget, reason: 'the same stage came back');
      expect(tester.takeException(), isNull);
    });

    testWidgets('just the animation: the stage has the whole screen, the chat is kept', (tester) async {
      _phone(tester);
      Widget split(StageSplitView view) => _host(StageSplit(
            wide: false,
            view: view,
            stage: (h) => h == null
                ? const ColoredBox(key: ValueKey('stage'), color: Colors.black, child: SizedBox.expand())
                : SizedBox(key: const ValueKey('stage'), height: h),
            chat: const _Counter(),
          ));
      await tester.pumpWidget(split(StageSplitView.split));
      await tester.tap(find.byKey(const ValueKey('counter')));
      await tester.pump();

      await tester.pumpWidget(split(StageSplitView.stageOnly));
      expect(tester.getSize(find.byKey(const ValueKey('stage'))).height, 800);
      expect(find.byKey(const ValueKey('counter')), findsNothing);

      await tester.pumpWidget(split(StageSplitView.split));
      expect(find.text('taps 1'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('hiding the stage leaves Animations on; only removing it turns them off', (tester) async {
      _phone(tester);
      final transport = FakeTransport();
      final chat = ChatController(
        api: FakeBackend().api,
        learner: const Learner(id: 'l1', label: 'Asha'),
        transportFactory: (_) async => transport,
      );
      await tester.runAsync(chat.start);
      var full = 0;
      Widget panel({required bool hidden}) => _host(Column(children: [
            Offstage(
              offstage: hidden,
              child: StagePanel(
                chat: chat,
                compact: true,
                compactHeight: 280,
                hidden: hidden,
                onFull: () => full++,
                onCollapse: () {},
              ),
            ),
          ]));

      await tester.pumpWidget(panel(hidden: false));
      expect(chat.stageEnabled, isTrue);
      await tester.tap(find.byKey(const ValueKey('stage-panel-full')));
      expect(full, 1, reason: 'a button for the animation alone');

      await tester.pumpWidget(panel(hidden: true));
      expect(chat.stageEnabled, isTrue, reason: 'out of sight is not off');
      expect(find.byKey(const ValueKey('stage-panel')), findsNothing);

      await tester.pumpWidget(_host(const SizedBox()));
      expect(chat.stageEnabled, isFalse, reason: 'the switch (no stage at all) is what turns it off');
      chat.dispose();
    });
  });

  group('where this could go, on a phone', () {
    const long = 'How Emmy Noether connected symmetry to every conservation law we know';

    Widget cards(double width) => _host(
          SingleChildScrollView(
            child: MessageView(
              message: ChatMessage(id: 1, role: Role.tutor, text: 'An answer.')
                ..directions = const [
                  DirectionCard(id: 'c1', text: long),
                  DirectionCard(id: 'c2', text: 'Calculate the final speed of a falling ball from energy alone'),
                ],
              showTiming: false,
              canPickOption: true,
              onPickOption: (_) {},
              onPickDirection: (_) {},
              directionsStyle: 'strip',
            ),
          ),
          size: Size(width, 800),
        );

    testWidgets('a card too wide for the screen slides sideways instead of being cut off', (tester) async {
      _phone(tester, const Size(360, 800));
      await tester.pumpWidget(cards(360));
      expect(find.byKey(const ValueKey('directions-scroll')), findsOneWidget);
      final chip = tester.getSize(find.byKey(const ValueKey('direction-c1')));
      final words = tester.getSize(find.text(long));
      expect(chip.width, greaterThan(360), reason: 'the card is as wide as its words');
      expect(words.width, lessThan(chip.width));
      // both cards start in view, one under the other
      expect(tester.getTopLeft(find.byKey(const ValueKey('direction-c2'))).dx,
          tester.getTopLeft(find.byKey(const ValueKey('direction-c1'))).dx);
      await tester.drag(find.byKey(const ValueKey('directions-scroll')), const Offset(-200, 0));
      await tester.pump();
      expect(tester.getTopLeft(find.byKey(const ValueKey('direction-c1'))).dx, lessThan(0));
      expect(tester.takeException(), isNull);
    });

    testWidgets('on a wide screen the cards still wrap', (tester) async {
      _phone(tester, const Size(1000, 800));
      await tester.pumpWidget(cards(1000));
      expect(find.byKey(const ValueKey('directions-scroll')), findsNothing);
    });
  });

  group('the message box on a phone', () {
    tearDown(() => debugDefaultTargetPlatformOverride = null);

    testWidgets('Enter sends and the keyboard goes down', (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.android;
      _phone(tester);
      final sent = <String>[];
      await tester.pumpWidget(_host(Align(
        alignment: Alignment.bottomCenter,
        child: Composer(enabled: true, onSend: (text, _) => sent.add(text)),
      )));
      final field = find.byKey(const ValueKey('composer-field'));
      expect(tester.widget<TextField>(field).textInputAction, TextInputAction.send);

      await tester.enterText(field, 'what is current?');
      await tester.testTextInput.receiveAction(TextInputAction.send);
      await tester.pump();
      expect(sent, ['what is current?']);
      expect(tester.widget<TextField>(field).focusNode!.hasFocus, isFalse, reason: 'the keyboard is dismissed');

      await tester.tap(field);
      await tester.enterText(field, 'and voltage?');
      await tester.pump();
      await tester.tap(find.byKey(const ValueKey('send-button')));
      await tester.pump();
      expect(sent.last, 'and voltage?');
      expect(tester.widget<TextField>(field).focusNode!.hasFocus, isFalse);
      debugDefaultTargetPlatformOverride = null;
    });

    testWidgets('with a real keyboard the box keeps the cursor', (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      _phone(tester);
      await tester.pumpWidget(_host(Composer(enabled: true, onSend: (_, _) {})));
      final field = find.byKey(const ValueKey('composer-field'));
      await tester.enterText(field, 'hello');
      await tester.pump();
      await tester.tap(find.byKey(const ValueKey('send-button')));
      await tester.pump();
      expect(tester.widget<TextField>(field).focusNode!.hasFocus, isTrue);
      debugDefaultTargetPlatformOverride = null;
    });
  });

  group('a Continue card on Home', () {
    testWidgets('says what the chat is about, not what was typed first', (tester) async {
      await tester.pumpWidget(_host(SingleChildScrollView(
        child: ContinueCard(
          chat: _chat(
            preview: 'help with this\n\n[Attached picture -- what it shows: a circuit with a fuse]',
            title: 'Fuses and circuit symbols',
            about: 'How a fuse protects a circuit, and how to read a rheostat symbol.',
          ),
          onTap: () {},
        ),
      )));
      expect(find.text('Fuses and circuit symbols'), findsNWidgets(2), reason: 'on the picture and as the caption');
      expect(find.text('How a fuse protects a circuit, and how to read a rheostat symbol.'), findsOneWidget);
      expect(find.textContaining('help with this'), findsNothing);
      expect(find.textContaining('Attached picture'), findsNothing);
    });

    testWidgets('a chat never described shows its opening words, without the picture text', (tester) async {
      await tester.pumpWidget(_host(SingleChildScrollView(
        child: ContinueCard(
          chat: _chat(preview: 'help with this\n\n[Attached picture -- what it shows: a circuit with a fuse]'),
          onTap: () {},
        ),
      )));
      expect(find.text('help with this'), findsNWidgets(2));
      expect(find.textContaining('Attached picture'), findsNothing);
      expect(find.textContaining('“'), findsNothing, reason: 'no longer quoted');
    });

    testWidgets("its picture is the chat's own animation, when it has one", (tester) async {
      var taps = 0;
      await tester.pumpWidget(_host(SingleChildScrollView(
        child: ContinueCard(
          chat: _chat(
            title: 'Electric current',
            about: 'Current as charge flowing past a point each second.',
            scene: [
              {'do': 'spawn', 'id': 'wire', 'kind': 'cylinder', 'x': 0.55, 'label': 'copper wire'},
              {'do': 'say', 'text': 'Current is charge on the move.'},
              {'do': 'ask', 'question': 'More?', 'choices': [{'id': 'y', 'text': 'Yes'}]},
            ],
          ),
          onTap: () => taps++,
        ),
      )));
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.byType(ScenePreview), findsOneWidget);
      expect(find.byType(StageView), findsOneWidget);
      expect(find.text('Electric current'), findsOneWidget, reason: 'the caption only: the picture is the scene');
      expect(find.textContaining('Current is charge'), findsNothing, reason: 'no speech bubble on a card');
      expect(find.text('More?'), findsNothing, reason: 'and no question');
      await tester.tap(find.byType(ScenePreview));
      expect(taps, 1, reason: 'the picture is part of the card');
      expect(tester.takeException(), isNull);
    });

    testWidgets('renders (a picture to look at, when asked for)', (tester) async {
      // flutter test --dart-define=VERSA_SHOTS=dir  writes PNGs of the card and the stage there
      const dir = String.fromEnvironment('VERSA_SHOTS');
      if (dir.isEmpty) return;
      _phone(tester, const Size(400, 900));
      final engine = StageEngine();
      final key = GlobalKey();
      await tester.pumpWidget(_host(
        RepaintBoundary(
          key: key,
          child: ColoredBox(
            color: Colors.white,
            child: Column(children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: ContinueCard(
                  chat: _chat(
                    title: 'Electric current',
                    about: 'Current as charge flowing past a point each second.',
                    scene: [
                      {'do': 'spawn', 'id': 'wire', 'kind': 'cylinder', 'x': 0.55, 'label': 'copper wire'},
                      {'do': 'spawn', 'id': 'e1', 'kind': 'sphere', 'x': 0.3, 'label': 'electron'},
                    ],
                  ),
                  onTap: () {},
                ),
              ),
              SizedBox(height: 400, child: StageView(engine: engine, animate: false)),
            ]),
          ),
        ),
        size: const Size(400, 900),
      ));
      await tester.runAsync(() async {
        // started in here: the script's own steps run in real time, not the test's fake clock
        engine.beginLive();
        for (final a in parseScript([
          {'do': 'spawn', 'id': 'a', 'kind': 'sphere', 'x': 0.42, 'label': 'second'},
          {'do': 'spawn', 'id': 'b', 'kind': 'sphere', 'x': 0.50, 'label': 'electron'},
          {'do': 'spawn', 'id': 'c', 'kind': 'cylinder', 'x': 0.60, 'label': 'charge or electrons'},
          {'do': 'spawn', 'id': 'd', 'kind': 'emoji', 'label': '🔋', 'x': 0.8, 'caption': 'ammeter showing current rate'},
          {'do': 'say', 'text': 'Current (I) = Charge (Q) divided by Time (t).', 'ms': 60000},
        ])) {
          engine.enqueue(a);
        }
        engine.endLive();
        for (var i = 0; i < 400; i++) {
          engine.tick(1 / 60);
          await Future<void>.delayed(Duration.zero);
        }
      });
      await tester.pump();
      await tester.runAsync(() async {
        final boundary = key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final image = await boundary.toImage(pixelRatio: 2);
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File('$dir/polish.png').writeAsBytesSync(bytes!.buffer.asUint8List());
      });
      engine.dispose();
    });
  });
}
