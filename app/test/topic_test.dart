import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:versa_app/api.dart';
import 'package:versa_app/app_state.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/main.dart';
import 'package:versa_app/models.dart';
import 'package:versa_app/topic/topics_home_screen.dart';

import 'support/fakes.dart';

Map<String, dynamic> _node(String id, String title, {String? parent, int depth = 0, bool expanded = false}) => {
      'id': id,
      'parent_id': parent,
      'title': title,
      'summary': 'About $title.',
      'depth': depth,
      'expanded': expanded,
      'children': <Map<String, dynamic>>[],
    };

Map<String, dynamic> _lessonSummary(String id, String title, String status, int percent, int done, int total) => {
      'id': id,
      'title': title,
      'objective': 'Understand $title',
      'position': 0,
      'status': status,
      'percent': percent,
      'tasks_total': total,
      'tasks_done': done,
    };

/// FakeBackend plus a scripted Learn-a-topic server (topics.py's routes).
class TopicHarness {
  final backend = FakeBackend();
  final List<http.BaseRequest> topicRequests = [];
  final List<Map<String, dynamic>> createdTopicBodies = [];
  final List<String> expandCalls = [];
  List<Map<String, dynamic>>? allSessions;

  Map<String, dynamic> exploration(String sourceKind, String query) => {
        'id': 'exp-1',
        'query': query,
        'source_kind': sourceKind,
        'resource': sourceKind == 'search' ? null : {'id': 'res-1', 'kind': sourceKind, 'title': 'Notes.pdf'},
        'personalized_by': ['your thinking style'],
        'root_nodes': [
          _node('n1', 'Foundations'),
          _node('n2', 'Core methods'),
          _node('n3', 'Applications'),
        ],
      };

  Map<String, dynamic> topic = {
    'id': 'topic-1',
    'title': 'Machine learning',
    'percent': 35,
    'source_kind': 'search',
    'chapters': [
      {
        'id': 'c1',
        'title': 'Foundations',
        'summary': 'The ground floor.',
        'position': 0,
        'percent': 75,
        'lessons': [
          _lessonSummary('l1', 'What is learning from data', 'done', 100, 3, 3),
          _lessonSummary('l2', 'Loss functions', 'in_progress', 50, 2, 4),
        ],
      },
      {
        'id': 'c2',
        'title': 'Core methods',
        'summary': 'The workhorses.',
        'position': 1,
        'percent': 0,
        'lessons': [_lessonSummary('l3', 'Linear regression', 'not_started', 0, 0, 3)],
      },
    ],
  };

  Map<String, dynamic> lesson(String id) => {
        ..._lessonSummary(id, id == 'l3' ? 'Linear regression' : 'Loss functions', 'in_progress', 25, 1, 4),
        'chapter_id': 'c2',
        'chapter_title': 'Core methods',
        'topic_id': 'topic-1',
        'topic_title': 'Machine learning',
        'session_id': null,
        'tasks': [
          {'id': 't1', 'position': 0, 'kind': 'learn', 'description': 'Say what a line of best fit is', 'done': true},
          {'id': 't2', 'position': 1, 'kind': 'practice', 'description': 'Fit a line to three points', 'done': false},
          {'id': 't3', 'position': 2, 'kind': 'apply', 'description': 'Predict a house price', 'done': false},
          {'id': 't4', 'position': 3, 'kind': 'check', 'description': 'Answer the end-of-lesson questions', 'done': false},
        ],
      };

  static http.Response _json(Object? data) =>
      http.Response(jsonEncode(data), 200, headers: const {'content-type': 'application/json'});

  late final http.Client client = MockClient.streaming((request, body) async {
    final path = request.url.path;
    final bytes = await body.toBytes();
    if (path.endsWith('/feed')) {
      return _stream(_json({
        'has_history': false,
        'generated_at': null,
        'continue': [],
        'related': [],
        'explore': [],
      }));
    }
    if (path.startsWith('/api/topic') || path.startsWith('/api/lessons') || path.endsWith('/topics')) {
      topicRequests.add(request);
    }
    final json = request.headers['content-type']?.contains('application/json') == true && bytes.isNotEmpty
        ? jsonDecode(utf8.decode(bytes)) as Map<String, dynamic>
        : const <String, dynamic>{};
    if (path == '/api/topic-explorations') return _stream(_json(exploration('search', json['query'] as String)));
    if (path == '/api/topic-explorations/from-link') return _stream(_json(exploration('link', json['url'] as String)));
    if (path == '/api/topic-explorations/from-pdf') {
      final text = latin1.decode(bytes);
      if (!text.contains('filename="notes.pdf"')) return _stream(http.Response('no file', 422));
      return _stream(_json(exploration('pdf', 'Notes')));
    }
    if (path.startsWith('/api/topic-nodes/') && path.endsWith('/expand')) {
      final id = request.url.pathSegments[2];
      final more = json['more'] == true;
      expandCalls.add('$id${more ? '+more' : ''}');
      return _stream(_json(more
          ? [_node('${id}c', 'Ensembles', parent: id, depth: 1)]
          : [
              _node('${id}a', 'Supervised learning', parent: id, depth: 1),
              _node('${id}b', 'Unsupervised learning', parent: id, depth: 1),
            ]));
    }
    if (path == '/api/topics' && request.method == 'POST') {
      createdTopicBodies.add(json);
      return _stream(_json(topic));
    }
    if (path.startsWith('/api/learners/') && path.endsWith('/topics')) {
      return _stream(_json([
        {
          'id': 'topic-1',
          'title': 'Machine learning',
          'percent': 35,
          'chapter_count': 2,
          'lesson_count': 3,
          'lessons_done': 1,
          'updated_at': DateTime.now().toUtc().toIso8601String(),
        },
      ]));
    }
    if (path == '/api/topics/topic-1') return _stream(_json(topic));
    if (path.startsWith('/api/lessons/') && path.endsWith('/start')) {
      return _stream(_json({'session_id': 'lesson-session-${request.url.pathSegments[2]}'}));
    }
    if (path.startsWith('/api/lessons/')) return _stream(_json(lesson(request.url.pathSegments[2])));
    if (allSessions != null && path.endsWith('/sessions/all')) return _stream(_json(allSessions));

    final copy = http.Request(request.method, request.url)
      ..headers.addAll(request.headers)
      ..bodyBytes = bytes;
    return backend.client.send(copy);
  });

  static http.StreamedResponse _stream(http.Response r) => http.StreamedResponse(
        Stream.value(r.bodyBytes),
        r.statusCode,
        headers: r.headers,
      );

  VersaApi get api => VersaApi('http://test', client: client);
}

Future<FakeTransport> _boot(WidgetTester tester, TopicHarness h, {Size size = const Size(1400, 1000)}) async {
  tester.view.physicalSize = size;
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

Future<void> _openTopics(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('nav-Modes')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const ValueKey('mode-learn')));
  await tester.pumpAndSettle();
}

Future<void> _tapKey(WidgetTester tester, String key) async {
  final f = find.byKey(ValueKey(key));
  await tester.ensureVisible(f);
  await tester.pumpAndSettle();
  await tester.tap(f);
  await tester.pumpAndSettle();
}

void main() {
  late Future<PickedPdf?> Function() realPicker;
  setUp(() => realPicker = topicPdfPicker);
  tearDown(() => topicPdfPicker = realPicker);

  testWidgets('topics home lists courses with progress and offers the three ways in', (tester) async {
    final h = TopicHarness();
    await _boot(tester, h);
    await _openTopics(tester);

    expect(find.text('What do you want to learn?'), findsOneWidget);
    expect(find.byKey(const ValueKey('topic-search-field')), findsOneWidget);
    expect(find.byKey(const ValueKey('topic-upload-pdf')), findsOneWidget);
    expect(find.byKey(const ValueKey('topic-link')), findsOneWidget);
    expect(find.byKey(const ValueKey('topic-card-topic-1')), findsOneWidget);
    expect(find.byKey(const ValueKey('topic-percent-topic-1')), findsOneWidget);
    expect(find.text('35%'), findsOneWidget);
    expect(find.text('1 of 3 lessons done · 2 chapters'), findsOneWidget);

    // back to the mode picker
    await _tapKey(tester, 'topics-back');
    expect(find.byKey(const ValueKey('mode-learn')), findsOneWidget);
  });

  testWidgets('a PDF is uploaded as bytes and mapped into branches', (tester) async {
    final h = TopicHarness();
    topicPdfPicker = () async => (name: 'notes.pdf', bytes: Uint8List.fromList(utf8.encode('%PDF-1.4 fake')));
    await _boot(tester, h);
    await _openTopics(tester);

    await _tapKey(tester, 'topic-upload-pdf');
    expect(find.text('FROM YOUR PDF'), findsOneWidget);
    expect(find.text('Foundations'), findsOneWidget);
    final upload = h.topicRequests.firstWhere((r) => r.url.path == '/api/topic-explorations/from-pdf');
    expect(upload.headers['content-type'], startsWith('multipart/form-data'));
  });

  testWidgets('a web link is checked, then mapped', (tester) async {
    final h = TopicHarness();
    await _boot(tester, h);
    await _openTopics(tester);

    await _tapKey(tester, 'topic-link');
    await tester.enterText(find.byKey(const ValueKey('topic-link-field')), 'not a link');
    await tester.tap(find.byKey(const ValueKey('topic-link-go')));
    await tester.pumpAndSettle();
    expect(find.textContaining('starting with http'), findsOneWidget);

    await tester.enterText(find.byKey(const ValueKey('topic-link-field')), 'https://example.com/ml');
    await tester.tap(find.byKey(const ValueKey('topic-link-go')));
    await tester.pumpAndSettle();
    expect(find.text('FROM A WEB PAGE'), findsOneWidget);
    expect(h.topicRequests.any((r) => r.url.path == '/api/topic-explorations/from-link'), isTrue);
  });

  testWidgets('explore: branch further, tick branches, build the course', (tester) async {
    final h = TopicHarness();
    await _boot(tester, h);
    await _openTopics(tester);

    await tester.enterText(find.byKey(const ValueKey('topic-search-field')), 'machine learning');
    await tester.pump();
    await _tapKey(tester, 'topic-search-go');

    expect(find.text('EXPLORE A TOPIC'), findsOneWidget);
    expect(find.text('Core methods'), findsOneWidget);
    expect(find.byKey(const ValueKey('shaped-by')), findsOneWidget);
    expect(find.text('Supervised learning'), findsNothing);

    // Branch further: children appear under it.
    await _tapKey(tester, 'branch-n2');
    expect(h.expandCalls, ['n2']);
    expect(find.text('Supervised learning'), findsOneWidget);
    expect(find.text('Unsupervised learning'), findsOneWidget);

    // More branches on an already-open node appends new ones.
    await _tapKey(tester, 'more-n2');
    expect(h.expandCalls, ['n2', 'n2+more']);
    expect(find.text('Ensembles'), findsOneWidget);

    // Hiding and showing again doesn't ask the server again.
    await _tapKey(tester, 'branch-n2');
    expect(find.text('Supervised learning'), findsNothing);
    await _tapKey(tester, 'branch-n2');
    expect(find.text('Supervised learning'), findsOneWidget);
    expect(h.expandCalls, ['n2', 'n2+more']);

    // Nothing ticked: can't build yet.
    final build = find.byKey(const ValueKey('build-course'));
    expect(tester.widget<ButtonStyleButton>(build).onPressed, isNull);

    await _tapKey(tester, 'select-n1');
    await _tapKey(tester, 'select-n2a');
    expect(find.text('2 selected'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('course-title-field')), 'My ML course');
    await _tapKey(tester, 'build-course');

    expect(h.createdTopicBodies.single['selected_node_ids'], ['n1', 'n2a']);
    expect(h.createdTopicBodies.single['title'], 'My ML course');
    expect(h.createdTopicBodies.single['exploration_id'], 'exp-1');
    // Lands on the course.
    expect(find.text('TOPIC'), findsOneWidget);
    expect(find.byKey(const ValueKey('chapter-c1')), findsOneWidget);

    // Back returns to the topics home, not the map.
    await _tapKey(tester, 'topic-back');
    expect(find.text('What do you want to learn?'), findsOneWidget);
  });

  testWidgets('topic screen shows overall and per-chapter percentages; the bar opens the path', (tester) async {
    final h = TopicHarness();
    await _boot(tester, h);
    await _openTopics(tester);
    await _tapKey(tester, 'topic-card-topic-1');

    expect(tester.widget<Text>(find.byKey(const ValueKey('topic-percent'))).data, '35%');
    expect(find.byKey(const ValueKey('chapter-percent-c1')), findsOneWidget);
    expect(tester.widget<Text>(find.byKey(const ValueKey('chapter-percent-c1'))).data, '75%');
    expect(tester.widget<Text>(find.byKey(const ValueKey('chapter-percent-c2'))).data, '0%');
    expect(find.byKey(const ValueKey('lesson-row-l1')), findsOneWidget);
    expect(find.byKey(const ValueKey('lesson-row-l3')), findsOneWidget);

    await _tapKey(tester, 'topic-progress');
    expect(find.text('YOUR PATH'), findsOneWidget);
  });

  testWidgets('path page: node states, the next lesson marked, any lesson opens (no locking)', (tester) async {
    final h = TopicHarness();
    await _boot(tester, h);
    await _openTopics(tester);
    await _tapKey(tester, 'topic-card-topic-1');
    await _tapKey(tester, 'topic-progress');

    expect(find.byKey(const ValueKey('path-state-done-l1')), findsOneWidget);
    expect(find.byKey(const ValueKey('path-state-inProgress-l2')), findsOneWidget);
    expect(find.byKey(const ValueKey('path-state-notStarted-l3')), findsOneWidget);
    expect(find.byKey(const ValueKey('path-chapter-c1')), findsOneWidget);
    expect(find.text('CONTINUE'), findsOneWidget);

    // A lesson not started, after one still in progress: opens anyway.
    await _tapKey(tester, 'path-node-l3');
    expect(find.byKey(const ValueKey('lesson-title')), findsOneWidget);
    expect(find.text('Linear regression'), findsWidgets);
  });

  testWidgets('lesson chat: start sends a turn, a progress event ticks the task and moves the bars',
      (tester) async {
    final h = TopicHarness();
    final transport = await _boot(tester, h);
    await _openTopics(tester);
    await _tapKey(tester, 'topic-card-topic-1');
    await _tapKey(tester, 'lesson-row-l3');

    expect(h.topicRequests.any((r) => r.url.path == '/api/lessons/l3/start'), isTrue);
    expect(find.byKey(const ValueKey('lesson-tasks')), findsOneWidget);
    expect(tester.widget<Text>(find.byKey(const ValueKey('lesson-percent'))).data, '25%');
    expect(find.byKey(const ValueKey('task-done-t1')), findsOneWidget);
    expect(find.byKey(const ValueKey('task-open-t2')), findsOneWidget);
    expect(find.text('END-OF-LESSON QUESTIONS'), findsOneWidget);
    expect(find.byKey(const ValueKey('lesson-knob-length')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('start-lesson')));
    await tester.pump();
    expect(transport.sent.last['type'], 'message');

    transport.emit(const Done(turnIndex: 0, kind: 'answer', text: 'First, fit a line.', firstOutputMs: 5, totalMs: 9));
    await tester.pump();
    transport.emit(const ProgressEvent(
      lessonId: 'l3',
      taskId: 't2',
      lessonPercent: 50,
      chapterPercent: 50,
      topicPercent: 42,
      lessonStatus: 'in_progress',
    ));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('task-done-t2')), findsOneWidget);
    expect(tester.widget<Text>(find.byKey(const ValueKey('lesson-percent'))).data, '50%');
    expect(tester.widget<Text>(find.byKey(const ValueKey('lesson-chapter-percent'))).data, '50%');
    expect(tester.widget<Text>(find.byKey(const ValueKey('lesson-topic-percent'))).data, '42%');
    expect(find.textContaining('Task done: Fit a line'), findsOneWidget);

    // A progress frame never becomes a chat message.
    expect(find.text('First, fit a line.'), findsOneWidget);
  });

  testWidgets('on a phone the lesson tasks fold into a strip above the chat', (tester) async {
    final h = TopicHarness();
    await _boot(tester, h, size: const Size(420, 900));
    await tester.tap(find.byType(NavigationDestination).at(1));
    await tester.pumpAndSettle();
    await _tapKey(tester, 'mode-learn');
    await _tapKey(tester, 'topic-card-topic-1');
    await _tapKey(tester, 'lesson-row-l3');

    expect(find.text('TASKS 1/4'), findsOneWidget);
    expect(find.byKey(const ValueKey('lesson-tasks')), findsOneWidget);
    await _tapKey(tester, 'lesson-tasks-toggle');
    expect(find.byKey(const ValueKey('lesson-tasks')), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('History opens a lesson chat into its lesson', (tester) async {
    final h = TopicHarness();
    h.allSessions = [
      {
        'session_id': 'lesson-session-l3',
        'app_mode': 'topic',
        'turn_count': 3,
        'created_at': DateTime.now().toUtc().toIso8601String(),
        'last_activity_at': DateTime.now().toUtc().toIso8601String(),
        'preview': 'fit a line to three points',
        'lesson_id': 'l3',
      },
    ];
    await _boot(tester, h);
    await tester.tap(find.byKey(const ValueKey('nav-History')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('fit a line to three points'));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('lesson-title')), findsOneWidget);
    expect(h.topicRequests.any((r) => r.url.path == '/api/lessons/l3/start'), isTrue);
  });

  testWidgets('parses the progress frame', (tester) async {
    final e = parseServerEvent({
      'type': 'progress',
      'lesson_id': 'l1',
      'task_id': 't1',
      'lesson_percent': 40,
      'chapter_percent': 20,
      'topic_percent': 10,
      'lesson_status': 'in_progress',
    });
    expect(e, isA<ProgressEvent>());
    expect((e as ProgressEvent).lessonPercent, 40);
  });
}
