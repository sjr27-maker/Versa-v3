import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/models.dart';
import 'package:versa_app/picture.dart';
import 'package:versa_app/stage/engine.dart';
import 'package:versa_app/stage/script.dart';
import 'package:versa_app/stage/stage_view.dart';
import 'package:versa_app/widgets/composer.dart';
import 'package:versa_app/widgets/message_view.dart';
import 'package:versa_app/widgets/rich_text.dart';

import 'support/fakes.dart';

// a real 1x1 PNG, so Image.memory can decode it
final _png = base64Decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==');

Widget _host(Widget child) => MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child)));

void main() {
  group('formatting', () {
    test('maths, money, bold and lists are told apart', () {
      final blocks = parseBlocks('The roots of \$x^2 - 5x + 6 = 0\$ are:\n'
          '- \$x = 2\$\n'
          '- \$x = 3\$\n'
          '\n'
          '\$\$\n\\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}\n\$\$\n'
          '## Why\n'
          '1. factor it\n'
          'It costs \$5 and \$10 later.');
      expect(blocks.map((b) => b.kind).toList(), [
        BlockKind.paragraph,
        BlockKind.item,
        BlockKind.item,
        BlockKind.math,
        BlockKind.heading,
        BlockKind.item,
        BlockKind.paragraph,
      ]);
      expect(blocks[3].text, r'\frac{-b \pm \sqrt{b^2-4ac}}{2a}');
      expect(blocks[5].marker, '1.');

      final money = inlineSpans(r'It costs $5 and $10 later.', const TextStyle());
      expect(money.whereType<WidgetSpan>(), isEmpty, reason: 'dollars before digits are money');
      final maths = inlineSpans(r'so $v = \frac{d}{t}$, **fast** and *slow* `code`', const TextStyle());
      expect(maths.whereType<WidgetSpan>(), hasLength(1));
      expect(
        maths.whereType<TextSpan>().where((s) => s.style?.fontWeight == FontWeight.w700).map((s) => s.text),
        ['fast'],
      );
    });

    testWidgets('an answer with maths is typeset, not shown as source', (tester) async {
      await tester.pumpWidget(_host(const RichMessageText(r'Speed is $v = \frac{d}{t}$.' '\n\n' r'$$E = mc^2$$')));
      final maths = tester.widgetList<Math>(find.byType(Math)).toList();
      expect(maths, hasLength(2));
      expect(maths.every((m) => m.parseError == null), isTrue);
      expect(find.textContaining(r'\frac'), findsNothing);
    });
  });

  group('pictures', () {
    test('the words and the reading travel together and come apart again', () {
      final sent = withPicture('is this right?', r'$x = 2$');
      expect(splitPicture(sent), ('is this right?', r'$x = 2$'));
      expect(splitPicture(withPicture('', 'a graph')), ('', 'a graph'));
      expect(splitPicture('no picture here'), ('no picture here', null));
    });

    testWidgets('a picked picture is read, sent with the message, and handed to the slime', (tester) async {
      final backend = FakeBackend();
      final transport = FakeTransport();
      final chat = ChatController(
        api: backend.api,
        learner: const Learner(id: 'l1', label: 'Asha'),
        transportFactory: (_) async => transport,
      );
      await tester.runAsync(chat.start);
      picturePicker = () async => (name: 'working.png', bytes: Uint8List.fromList(_png));
      canTakePhoto = () => false; // a browser: straight to the file picker
      addTearDown(() {
        picturePicker = () async => null;
        canTakePhoto = () => false;
      });

      await tester.pumpWidget(_host(Composer(
        enabled: true,
        onSend: chat.send,
        uploadPicture: (bytes, name) => backend.api.uploadPicture('l1', bytes, name),
      )));
      await tester.tap(find.byKey(const ValueKey('composer-attach')));
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump();
      expect(backend.picturesUploaded, 1);
      expect(find.byKey(const ValueKey('composer-picture')), findsOneWidget);
      expect(find.text('Picture attached'), findsOneWidget);

      // a picture alone can be sent
      await tester.tap(find.byKey(const ValueKey('send-button')));
      await tester.pump();
      expect(transport.sent.last['type'], 'message');
      expect(transport.sent.last['image_id'], 'img-1');
      final mine = chat.messages.lastWhere((m) => m.role == Role.user);
      expect(mine.picture, isNotNull);
      expect(chat.stagePicture, isNotNull, reason: 'the slime can hold it up');
      expect(find.byKey(const ValueKey('composer-picture')), findsNothing, reason: 'sent: the box is clear');

      await tester.pumpWidget(_host(MessageView(
        message: mine,
        showTiming: false,
        canPickOption: false,
        onPickOption: (_) {},
      )));
      expect(find.byKey(ValueKey('msg-picture-${mine.id}')), findsOneWidget);
      chat.dispose();
    });

    testWidgets('on a phone, the button offers the camera, and a photo taken is sent', (tester) async {
      final backend = FakeBackend();
      var cameraOpened = 0, filesOpened = 0;
      canTakePhoto = () => true;
      cameraPicker = () async {
        cameraOpened++;
        return (name: 'photo.jpg', bytes: Uint8List.fromList(_png));
      };
      picturePicker = () async {
        filesOpened++;
        return null;
      };
      addTearDown(() {
        canTakePhoto = () => false;
        cameraPicker = () async => null;
        picturePicker = () async => null;
      });
      final sent = <AttachedPicture?>[];
      await tester.pumpWidget(_host(Composer(
        enabled: true,
        onSend: (_, picture) => sent.add(picture),
        uploadPicture: (bytes, name) => backend.api.uploadPicture('l1', bytes, name),
      )));
      await tester.tap(find.byKey(const ValueKey('composer-attach')));
      await tester.pumpAndSettle();
      expect(find.text('Take a photo'), findsOneWidget);
      expect(find.text('Choose a picture'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('picture-camera')));
      await tester.pumpAndSettle();
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump();
      expect((cameraOpened, filesOpened), (1, 0));
      expect(backend.picturesUploaded, 1);

      await tester.tap(find.byKey(const ValueKey('send-button')));
      await tester.pump();
      expect(sent.single?.name, 'photo.jpg');
    });

    testWidgets('a picture from history shows as what it showed', (tester) async {
      final message = ChatMessage(id: 1, role: Role.user, text: withPicture('check this', r'$x^2$ crossed out'));
      await tester.pumpWidget(_host(MessageView(
        message: message,
        showTiming: false,
        canPickOption: false,
        onPickOption: (_) {},
      )));
      expect(find.text('check this'), findsOneWidget);
      expect(find.textContaining('Attached picture'), findsNothing);
      await tester.tap(find.byKey(const ValueKey('picture-note')));
      await tester.pump();
      expect(find.byType(Math), findsOneWidget, reason: 'the reading, typeset');
    });

    testWidgets('the slime holds the picture up where the director puts it', (tester) async {
      final e = StageEngine()
        ..speed = 1
        ..photo = Uint8List.fromList(_png);
      e.play(parseScript([
        {'do': 'spawn', 'id': 'pic', 'kind': 'photo', 'x': 0.7, 'y': 0.4, 'size': 1.2},
      ]));
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: SizedBox(width: 800, height: 500, child: StageView(engine: e, animate: false))),
      ));
      for (var i = 0; i < 40; i++) {
        e.tick(1 / 60);
        await tester.pump();
      }
      final photo = find.byKey(const ValueKey('photo-pic'));
      expect(photo, findsOneWidget);
      expect(find.descendant(of: photo, matching: find.byType(Image)), findsOneWidget);
    });
  });
}
