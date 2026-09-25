import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:versa_app/api.dart';
import 'package:versa_app/app_state.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/composer_draft.dart';
import 'package:versa_app/main.dart';

import 'support/fakes.dart';

Map<String, String> _item(String title, String reason) => {
      'title': title,
      'hook': 'A hook for $title.',
      'reason': reason,
      'starter': 'Tell me about $title',
    };

/// FakeBackend plus a scripted `GET /api/learners/{id}/feed`.
class FeedHarness {
  FeedHarness({this.feedStatus = 200});

  final backend = FakeBackend();
  int feedStatus;
  final List<Uri> feedRequests = [];
  Map<String, dynamic> feed = {
    'has_history': false,
    'generated_at': DateTime.now().toUtc().toIso8601String(),
    'cached': false,
    'continue': <Map<String, dynamic>>[],
    'related': <Map<String, String>>[],
    'explore': [_item('How bridges stay up', 'Everyday engineering'), _item('Why the sky is blue', 'Short physics')],
  };

  late final http.Client client = MockClient((request) async {
    if (request.url.path.endsWith('/feed')) {
      feedRequests.add(request.url);
      if (feedStatus != 200) return http.Response('boom', feedStatus);
      return http.Response(jsonEncode(feed), 200, headers: const {'content-type': 'application/json'});
    }
    final copy = http.Request(request.method, request.url)
      ..headers.addAll(request.headers)
      ..body = request.body;
    return http.Response.fromStream(await backend.client.send(copy));
  });

  VersaApi get api => VersaApi('http://test', client: client);
}

Future<FakeTransport> _boot(WidgetTester tester, FeedHarness h) async {
  tester.view.physicalSize = const Size(1400, 1000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  SharedPreferences.setMockInitialValues({'learner_label': 'Asha'});
  final prefs = await SharedPreferences.getInstance();
  final transport = FakeTransport();
  await tester.pumpWidget(VersaApp(
    api: h.api,
    prefs: prefs,
    chatFactory: (AppState app, {resumeSessionId}) => ChatController(
      api: app.api,
      learner: app.learner!,
      resumeSessionId: resumeSessionId,
      transportFactory: (_) async => transport,
    ),
  ));
  await tester.pumpAndSettle();
  return transport;
}

void main() {
  tearDown(() => composerDraft.value = null);

  testWidgets('someone new sees only things to explore, and says why', (tester) async {
    final h = FeedHarness();
    await _boot(tester, h);

    expect(find.byKey(const ValueKey('home-start-sandbox')), findsOneWidget);
    expect(find.byKey(const ValueKey('feed-new-learner')), findsOneWidget);
    expect(find.byKey(const ValueKey('feed-section-explore')), findsOneWidget);
    expect(find.byKey(const ValueKey('feed-section-continue')), findsNothing);
    expect(find.byKey(const ValueKey('feed-section-related')), findsNothing);
    expect(find.text('How bridges stay up'), findsWidgets);
    expect(h.feedRequests.single.path, '/api/learners/learner-Asha/feed');
  });

  testWidgets('with history: Continue, For you and Explore, filtered by the chips', (tester) async {
    final h = FeedHarness();
    final chat = h.backend.seedSession(
        learnerId: h.backend.learnerIdFor('Asha'), preview: 'how does recursion work?', turnCount: 4);
    h.feed = {
      ...h.feed,
      'has_history': true,
      'continue': [
        {
          'session_id': chat,
          'app_mode': 'sandbox',
          'turn_count': 4,
          'created_at': DateTime.now().toUtc().toIso8601String(),
          'last_activity_at': DateTime.now().toUtc().toIso8601String(),
          'preview': 'how does recursion work?',
        },
      ],
      'related': [_item('Tail recursion', 'Because you asked about recursion')],
    };
    await _boot(tester, h);

    expect(find.byKey(const ValueKey('feed-new-learner')), findsNothing);
    expect(find.byKey(const ValueKey('feed-section-continue')), findsOneWidget);
    expect(find.byKey(const ValueKey('feed-section-related')), findsOneWidget);
    expect(find.text('Because you asked about recursion'), findsOneWidget);
    expect(find.text('4 messages · just now'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('feed-chip-For you')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('feed-section-related')), findsOneWidget);
    expect(find.byKey(const ValueKey('feed-section-continue')), findsNothing);
    expect(find.byKey(const ValueKey('feed-section-explore')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('feed-chip-Explore')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('feed-section-explore')), findsOneWidget);
    expect(find.byKey(const ValueKey('feed-section-related')), findsNothing);
  });

  testWidgets('a topic card opens a new chat with its starter typed, not sent', (tester) async {
    final h = FeedHarness();
    final transport = await _boot(tester, h);

    await tester.tap(find.byKey(const ValueKey('feed-topic-Why the sky is blue')));
    await tester.pumpAndSettle();

    final field = tester.widget<TextField>(find.byKey(const ValueKey('composer-field')));
    expect(field.controller!.text, 'Tell me about Why the sky is blue');
    expect(transport.sent, isEmpty);
    expect(composerDraft.value, isNull);
  });

  testWidgets('a Continue card reopens that chat', (tester) async {
    final h = FeedHarness();
    final chat = h.backend.seedSession(
        learnerId: h.backend.learnerIdFor('Asha'), preview: 'an older question', turnCount: 1);
    h.backend.historyBySession[chat] = [
      {
        'turn_index': 0, 'student_text': 'an older question', 'kind': 'answer',
        'tutor_text': 'an older answer', 'options_message': null, 'options': [],
      },
    ];
    h.feed = {
      ...h.feed,
      'has_history': true,
      'continue': [
        {
          'session_id': chat,
          'app_mode': 'sandbox',
          'turn_count': 1,
          'created_at': DateTime.now().toUtc().toIso8601String(),
          'last_activity_at': DateTime.now().toUtc().toIso8601String(),
          'preview': 'an older question',
        },
      ],
    };
    await _boot(tester, h);

    await tester.tap(find.byKey(ValueKey('feed-continue-$chat')));
    await tester.pumpAndSettle();
    expect(find.text('an older answer'), findsOneWidget);
    expect(h.backend.sessionsCreated, 1, reason: 'resuming must not start another chat');
  });

  testWidgets('refresh asks the server for new suggestions', (tester) async {
    final h = FeedHarness();
    await _boot(tester, h);
    await tester.tap(find.byKey(const ValueKey('feed-refresh')));
    await tester.pumpAndSettle();
    expect(h.feedRequests.length, 2);
    expect(h.feedRequests.last.queryParameters['refresh'], 'true');
  });

  testWidgets('a failed feed says so and can be retried', (tester) async {
    final h = FeedHarness(feedStatus: 500);
    await _boot(tester, h);
    expect(find.byKey(const ValueKey('feed-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('home-start-sandbox')), findsOneWidget);

    h.feedStatus = 200;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('feed-section-explore')), findsOneWidget);
  });

  testWidgets('coming back to Home reloads the feed', (tester) async {
    final h = FeedHarness();
    await _boot(tester, h);
    await tester.tap(find.byKey(const ValueKey('nav-Settings')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('nav-Home')));
    await tester.pumpAndSettle();
    expect(h.feedRequests.length, 2);
    expect(h.feedRequests.last.queryParameters.containsKey('refresh'), isFalse);
  });
}
