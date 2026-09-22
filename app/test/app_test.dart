import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:versa_app/app_state.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/main.dart';
import 'package:versa_app/models.dart';

import 'support/fakes.dart';

void _size(WidgetTester tester, double w, double h) {
  tester.view.physicalSize = Size(w, h);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

Future<SharedPreferences> _prefs([Map<String, Object> initial = const {}]) async {
  SharedPreferences.setMockInitialValues(initial);
  return SharedPreferences.getInstance();
}

/// Boots the whole app against a scripted backend and (optionally) a scripted
/// chat connection.
Future<void> _boot(
  WidgetTester tester, {
  required FakeBackend backend,
  FakeTransport? transport,
  Map<String, Object> prefs = const {},
}) async {
  final p = await _prefs(prefs);
  await tester.pumpWidget(VersaApp(
    api: backend.api,
    prefs: p,
    chatFactory: (AppState app, {resumeSessionId}) => ChatController(
      api: app.api,
      learner: app.learner!,
      resumeSessionId: resumeSessionId,
      transportFactory: (_) async => transport ?? FakeTransport(),
    ),
  ));
  await tester.pumpAndSettle();
}

Future<void> _signIn(WidgetTester tester, String name) async {
  await tester.enterText(find.byKey(const ValueKey('name-field')), name);
  await tester.tap(find.byKey(const ValueKey('continue-button')));
  await tester.pumpAndSettle();
}

Future<void> _openSandbox(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('nav-Modes')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const ValueKey('mode-sandbox')));
  await tester.pumpAndSettle();
}

Future<void> _type(WidgetTester tester, String text) async {
  await tester.enterText(find.byKey(const ValueKey('composer-field')), text);
  await tester.pump();
  await tester.tap(find.byKey(const ValueKey('send-button')));
  await tester.pump(const Duration(milliseconds: 50));
}

void main() {
  group('sign in', () {
    testWidgets('a new person names themselves and lands on Home', (tester) async {
      _size(tester, 1400, 900);
      final backend = FakeBackend();
      await _boot(tester, backend: backend);

      expect(find.byKey(const ValueKey('server-ok')), findsOneWidget);
      expect(find.textContaining('live Gemini'), findsOneWidget);
      await _signIn(tester, 'Asha');

      expect(find.text('Hello, Asha'), findsOneWidget);
      expect(backend.learnersSeen, contains('Asha'));
    });

    testWidgets('says plainly when the server is not running', (tester) async {
      _size(tester, 1400, 900);
      await _boot(tester, backend: FakeBackend(up: false));
      expect(find.byKey(const ValueKey('server-down')), findsOneWidget);
      expect(find.textContaining('uv run versa serve'), findsOneWidget);
    });

    testWidgets('a returning person skips sign-in', (tester) async {
      _size(tester, 1400, 900);
      await _boot(tester, backend: FakeBackend(), prefs: {'learner_label': 'Ben'});
      expect(find.text('Hello, Ben'), findsOneWidget);
      expect(find.byKey(const ValueKey('name-field')), findsNothing);
    });

    testWidgets('Switch learner signs out', (tester) async {
      _size(tester, 1400, 900);
      await _boot(tester, backend: FakeBackend(), prefs: {'learner_label': 'Ben'});
      await tester.tap(find.byKey(const ValueKey('nav-Settings')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('settings-name')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('switch-learner')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('name-field')), findsOneWidget);
    });
  });

  group('the shell', () {
    testWidgets('wide screens get a left rail with every destination', (tester) async {
      _size(tester, 1400, 900);
      await _boot(tester, backend: FakeBackend(), prefs: {'learner_label': 'Asha'});
      for (final label in ['Home', 'Modes', 'History', 'Thinking style', 'Settings']) {
        expect(find.byKey(ValueKey('nav-$label')), findsOneWidget, reason: label);
      }
      expect(find.byType(NavigationBar), findsNothing);
    });

    testWidgets('a phone-width screen gets a bottom bar instead', (tester) async {
      _size(tester, 400, 800);
      await _boot(tester, backend: FakeBackend(), prefs: {'learner_label': 'Asha'});
      expect(find.byType(NavigationBar), findsOneWidget);
      expect(find.byKey(const ValueKey('nav-Home')), findsNothing);
    });

    testWidgets('Modes: Sandbox is live, the other three say they are coming', (tester) async {
      _size(tester, 1400, 900);
      await _boot(tester, backend: FakeBackend(), prefs: {'learner_label': 'Asha'});
      await tester.tap(find.byKey(const ValueKey('nav-Modes')));
      await tester.pumpAndSettle();

      expect(find.text('LIVE'), findsOneWidget);
      expect(find.text('COMING SOON'), findsNWidgets(3));
      await tester.tap(find.byKey(const ValueKey('mode-exam')));
      await tester.pump();
      expect(find.textContaining("isn't built yet"), findsOneWidget);
    });

    testWidgets('History and Thinking style are labelled placeholders', (tester) async {
      _size(tester, 1400, 900);
      await _boot(tester, backend: FakeBackend(), prefs: {'learner_label': 'Asha'});
      for (final label in ['History', 'Thinking style']) {
        await tester.tap(find.byKey(ValueKey('nav-$label')));
        await tester.pumpAndSettle();
        expect(find.text('COMING SOON'), findsWidgets, reason: label);
      }
    });

    testWidgets('Home can start the Sandbox chat', (tester) async {
      _size(tester, 1400, 900);
      await _boot(tester, backend: FakeBackend(), prefs: {'learner_label': 'Asha'});
      await tester.tap(find.byKey(const ValueKey('home-start-sandbox')));
      await tester.pumpAndSettle();
      expect(find.text("What's on your mind, Asha?"), findsOneWidget);
    });
  });

  group('the Sandbox chat', () {
    testWidgets('streams an answer word by word and shows the timing', (tester) async {
      _size(tester, 1400, 900);
      final transport = FakeTransport();
      await _boot(tester,
          backend: FakeBackend(), transport: transport, prefs: {'learner_label': 'Asha'});
      await _openSandbox(tester);

      await _type(tester, 'Explain binary search.');
      expect(find.text('Explain binary search.'), findsOneWidget);
      expect(find.byKey(const ValueKey('typing-dot')), findsWidgets,
          reason: 'waiting for the first word');

      transport.emit(const Delta('Binary search '));
      await tester.pump(const Duration(milliseconds: 20));
      expect(find.text('Binary search '), findsOneWidget);
      expect(find.byKey(const ValueKey('typing-dot')), findsNothing);

      transport.emit(const Delta('halves the list.'));
      transport.emit(const Done(
          turnIndex: 0,
          kind: 'answer',
          text: 'Binary search halves the list.',
          firstOutputMs: 700,
          totalMs: 1900));
      await tester.pump(const Duration(milliseconds: 20));

      expect(find.text('Binary search halves the list.'), findsOneWidget);
      expect(find.byKey(const ValueKey('timing')), findsOneWidget);
      final label = tester.widget<Text>(find.byKey(const ValueKey('timing'))).data!;
      expect(label, startsWith('first words'));
    });

    testWidgets('an ambiguous question offers readings you can click', (tester) async {
      _size(tester, 1400, 900);
      final transport = FakeTransport();
      await _boot(tester,
          backend: FakeBackend(), transport: transport, prefs: {'learner_label': 'Asha'});
      await _openSandbox(tester);

      await _type(tester, 'can you help me with derivatives?');
      transport.emit(const OptionsEvent('Which of these did you mean?', [
        ChatOption(id: 'o1', text: 'Calculus derivatives'),
        ChatOption(id: 'o2', text: 'Financial derivatives'),
      ]));
      transport.emit(const Done(
          turnIndex: 0,
          kind: 'options',
          text: 'Which of these did you mean?',
          firstOutputMs: 4200,
          totalMs: 4200));
      await tester.pump(const Duration(milliseconds: 20));

      expect(find.text('Which of these did you mean?'), findsOneWidget);
      expect(find.text('Calculus derivatives'), findsOneWidget);
      expect(find.text('Financial derivatives'), findsOneWidget);
      final label = tester.widget<Text>(find.byKey(const ValueKey('timing'))).data!;
      expect(label, startsWith('options shown'));

      await tester.tap(find.byKey(const ValueKey('option-o1')));
      await tester.pump(const Duration(milliseconds: 20));
      expect(transport.sent.last, {'type': 'select_option', 'option_id': 'o1'});
      expect(find.text('Calculus derivatives'), findsOneWidget,
          reason: 'the chosen reading stays on its chip -- no echoed user bubble');

      transport.emit(const Delta('The power rule …'));
      transport.emit(const Done(
          turnIndex: 1, kind: 'answer', text: 'The power rule …', firstOutputMs: 800, totalMs: 1500));
      await tester.pump(const Duration(milliseconds: 20));

      // buttons are spent: tapping again sends nothing
      final sent = transport.sent.length;
      await tester.tap(find.byKey(const ValueKey('option-o2')), warnIfMissed: false);
      await tester.pump();
      expect(transport.sent.length, sent);
    });

    testWidgets('a suggestion chip starts a chat in one tap', (tester) async {
      _size(tester, 1400, 900);
      final transport = FakeTransport();
      await _boot(tester,
          backend: FakeBackend(), transport: transport, prefs: {'learner_label': 'Asha'});
      await _openSandbox(tester);
      await tester.tap(find.byKey(const ValueKey('suggestion-Explain how binary search works.')));
      await tester.pump(const Duration(milliseconds: 20));
      expect(transport.sent.single['text'], 'Explain how binary search works.');
    });

    testWidgets('the conversation survives visiting another tab', (tester) async {
      _size(tester, 1400, 900);
      final transport = FakeTransport();
      await _boot(tester,
          backend: FakeBackend(), transport: transport, prefs: {'learner_label': 'Asha'});
      await _openSandbox(tester);
      await _type(tester, 'remember me');
      transport.emit(const Done(
          turnIndex: 0, kind: 'answer', text: 'I will.', firstOutputMs: 1, totalMs: 2));
      await tester.pump(const Duration(milliseconds: 20));

      await tester.tap(find.byKey(const ValueKey('nav-Settings')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('nav-Modes')));
      await tester.pumpAndSettle();
      expect(find.text('remember me'), findsOneWidget);
      expect(find.text('I will.'), findsOneWidget);
    });

    testWidgets('the timing can be switched off in Settings', (tester) async {
      _size(tester, 1400, 900);
      final transport = FakeTransport();
      await _boot(tester,
          backend: FakeBackend(), transport: transport, prefs: {'learner_label': 'Asha'});
      await _openSandbox(tester);
      await _type(tester, 'hi');
      transport.emit(const Done(
          turnIndex: 0, kind: 'answer', text: 'Hello!', firstOutputMs: 1, totalMs: 2));
      await tester.pump(const Duration(milliseconds: 20));
      expect(find.byKey(const ValueKey('timing')), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('nav-Settings')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('timing-switch')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('nav-Modes')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('timing')), findsNothing);
    });

    testWidgets('a lost connection shows a banner and Reconnect brings the chat back',
        (tester) async {
      _size(tester, 1400, 900);
      var transport = FakeTransport();
      final backend = FakeBackend();
      final prefs = await _prefs({'learner_label': 'Asha'});
      await tester.pumpWidget(VersaApp(
        api: backend.api,
        prefs: prefs,
        chatFactory: (app, {resumeSessionId}) => ChatController(
          api: app.api,
          learner: app.learner!,
          resumeSessionId: resumeSessionId,
          transportFactory: (_) async => transport,
        ),
      ));
      await tester.pumpAndSettle();
      await _openSandbox(tester);

      await transport.drop();
      await tester.pumpAndSettle();
      expect(find.text('Disconnected'), findsOneWidget);
      expect(find.byKey(const ValueKey('reconnect')), findsOneWidget);

      transport = FakeTransport();
      await tester.tap(find.byKey(const ValueKey('reconnect')));
      await tester.pumpAndSettle();
      expect(find.text('Connected'), findsOneWidget);
      expect(backend.sessionsCreated, 1);
    });

    testWidgets('on a phone, the readings scroll into view above the message box', (tester) async {
      _size(tester, 400, 800);
      final transport = FakeTransport();
      await _boot(tester,
          backend: FakeBackend(), transport: transport, prefs: {'learner_label': 'Asha'});
      await tester.tap(find.byType(NavigationDestination).at(1));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('mode-sandbox')));
      await tester.pumpAndSettle();

      // a long answer first, so the conversation is taller than the screen
      await _type(tester, 'explain binary search');
      final long = List.filled(40, 'Binary search halves the list each step.').join(' ');
      transport.emit(Delta(long));
      transport.emit(Done(turnIndex: 0, kind: 'answer', text: long, firstOutputMs: 1, totalMs: 2));
      await tester.pump(const Duration(milliseconds: 300));

      await _type(tester, 'can you help me with derivatives?');
      transport.emit(const OptionsEvent('Which of these did you mean?', [
        ChatOption(id: 'o1', text: 'Are you looking for an introduction to the concepts and rules of calculus derivatives?'),
        ChatOption(id: 'o2', text: 'Do you have a specific math problem or homework question you need help working through?'),
        ChatOption(id: 'o3', text: 'Are you asking about financial derivatives, such as options, futures, and contracts?'),
      ]));
      transport.emit(const Done(
          turnIndex: 1, kind: 'options', text: 'Which of these did you mean?', firstOutputMs: 1, totalMs: 2));
      await tester.pump(const Duration(milliseconds: 400));
      await tester.pump(const Duration(milliseconds: 400));

      final composerTop = tester.getTopLeft(find.byKey(const ValueKey('composer-field'))).dy;
      for (final id in ['o1', 'o2', 'o3']) {
        final bottom = tester.getBottomLeft(find.byKey(ValueKey('option-$id'))).dy;
        expect(bottom, lessThanOrEqualTo(composerTop),
            reason: 'reading $id must be visible above the message box, not hidden behind it');
      }
    });

    testWidgets('works at phone width too', (tester) async {
      _size(tester, 400, 800);
      final transport = FakeTransport();
      await _boot(tester,
          backend: FakeBackend(), transport: transport, prefs: {'learner_label': 'Asha'});
      await tester.tap(find.byType(NavigationDestination).at(1));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('mode-sandbox')));
      await tester.pumpAndSettle();
      await _type(tester, 'hi');
      transport.emit(const Delta('Hello there'));
      transport.emit(const Done(
          turnIndex: 0, kind: 'answer', text: 'Hello there', firstOutputMs: 1, totalMs: 2));
      await tester.pump(const Duration(milliseconds: 20));
      expect(find.text('Hello there'), findsOneWidget);
      expect(tester.takeException(), isNull, reason: 'no layout overflow on a phone');
    });
  });

  group('the chat history sidebar', () {
    testWidgets('does not show on Home or the Modes picker', (tester) async {
      _size(tester, 1400, 900);
      await _boot(tester, backend: FakeBackend(), prefs: {'learner_label': 'Asha'});
      expect(find.byKey(const ValueKey('chat-history-rail')), findsNothing);

      await tester.tap(find.byKey(const ValueKey('nav-Modes')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('chat-history-rail')), findsNothing);
    });

    testWidgets('lists past chats newest-active first, active one shown by its own', (tester) async {
      _size(tester, 1400, 900);
      final backend = FakeBackend();
      final learnerId = backend.learnerIdFor('Asha');
      final older = backend.seedSession(
        learnerId: learnerId,
        preview: 'what is a derivative?',
        turnCount: 2,
        lastActivityAt: DateTime.now().subtract(const Duration(hours: 2)),
      );
      final newer = backend.seedSession(
        learnerId: learnerId,
        preview: 'and an integral?',
        turnCount: 1,
        lastActivityAt: DateTime.now().subtract(const Duration(minutes: 5)),
      );
      await _boot(tester, backend: backend, prefs: {'learner_label': 'Asha'});
      await _openSandbox(tester);
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('chat-history-rail')), findsOneWidget);
      expect(find.text('what is a derivative?'), findsOneWidget);
      expect(find.text('and an integral?'), findsOneWidget);
      final newerY = tester.getCenter(find.byKey(ValueKey('chat-row-$newer'))).dy;
      final olderY = tester.getCenter(find.byKey(ValueKey('chat-row-$older'))).dy;
      expect(newerY, lessThan(olderY), reason: 'the more recently used chat sorts first');
    });

    testWidgets('clicking a past chat resumes it, replacing what was on screen', (tester) async {
      _size(tester, 1400, 900);
      final backend = FakeBackend();
      final learnerId = backend.learnerIdFor('Asha');
      final oldChat = backend.seedSession(
        learnerId: learnerId, preview: 'an older question', turnCount: 1,
      );
      backend.historyBySession[oldChat] = [
        {
          'turn_index': 0, 'student_text': 'an older question', 'kind': 'answer',
          'tutor_text': 'an older answer', 'options_message': null, 'options': [],
        },
      ];
      await _boot(tester, backend: backend, prefs: {'learner_label': 'Asha'});
      await _openSandbox(tester);
      await tester.pumpAndSettle();
      expect(find.text("What's on your mind, Asha?"), findsOneWidget, reason: 'the freshly opened chat is empty');

      await tester.tap(find.byKey(ValueKey('chat-row-$oldChat')));
      await tester.pumpAndSettle();

      // "an older question" now appears twice by design: once in the
      // sidebar row (which persists) and once as the resumed conversation's
      // own first bubble.
      expect(find.text('an older question'), findsNWidgets(2));
      expect(find.text('an older answer'), findsOneWidget);
    });

    testWidgets('New chat in the sidebar starts a fresh chat, losing nothing already saved',
        (tester) async {
      _size(tester, 1400, 900);
      final transport = FakeTransport();
      await _boot(tester,
          backend: FakeBackend(), transport: transport, prefs: {'learner_label': 'Asha'});
      await _openSandbox(tester);
      await tester.pumpAndSettle();
      await _type(tester, 'hello from the first chat');

      await tester.tap(find.byKey(const ValueKey('history-new-chat')));
      await tester.pumpAndSettle();

      expect(find.text("What's on your mind, Asha?"), findsOneWidget);
      expect(find.text('hello from the first chat'), findsNothing);
    });

    testWidgets('on a phone, chat history opens from a header icon instead of a column',
        (tester) async {
      _size(tester, 400, 800);
      final backend = FakeBackend();
      final learnerId = backend.learnerIdFor('Asha');
      backend.seedSession(learnerId: learnerId, preview: 'an old one', turnCount: 1);
      await _boot(tester, backend: backend, prefs: {'learner_label': 'Asha'});
      await tester.tap(find.byType(NavigationDestination).at(1));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('mode-sandbox')));
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('chat-history-rail')), findsNothing);
      expect(find.byKey(const ValueKey('history-button')), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('history-button')));
      await tester.pumpAndSettle();
      expect(find.text('an old one'), findsOneWidget);
    });
  });
}
