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
import 'package:versa_app/exam/quiz_screen.dart';
import 'package:versa_app/main.dart';
import 'package:versa_app/picture.dart';

import 'support/fakes.dart';

Map<String, dynamic> _unit(String id, int position, String title, {int taken = 0, int? last, int? best}) => {
      'id': id,
      'position': position,
      'title': title,
      'summary': 'What an exam asks about $title.',
      'quizzes_taken': taken,
      'last_percent': last,
      'best_percent': best,
    };

Map<String, dynamic> _question(String id, int position, String unit, {bool choice = true}) => {
      'id': id,
      'position': position,
      'unit_title': unit,
      'kind': choice ? 'choice' : 'short',
      'prompt': 'Question $id?',
      'choices': choice ? ['Right $id', 'Wrong $id', 'Also wrong $id'] : <String>[],
      'skill': choice ? 'apply' : 'analyse',
      'form': choice ? 'puzzle' : 'quiz',
    };

/// FakeBackend plus a scripted exams.py server.
class ExamHarness {
  final backend = FakeBackend();
  final List<Map<String, dynamic>> createBodies = [];
  final List<String> createPaths = [];
  final List<Map<String, dynamic>> submitBodies = [];
  final List<Map<String, dynamic>> checkBodies = [];
  final List<String> sceneCalls = [];
  final List<String> warmUpCalls = [];

  Map<String, dynamic> warmUp(String unitId, String title) => {
        'unit_id': unitId,
        'unit_title': title,
        'points': ['Key point of $title.', 'Another thing to remember.'],
        'formula': 'v = u + at',
        'example': 'A worked example for $title.',
        'script': [
          {'do': 'say', 'text': 'Here is the idea.'},
        ],
      };
  int? mockSecondsLeft = 600;
  bool taken = false;

  /// The current study plan (null = none yet); every POST/tick is logged.
  Map<String, dynamic>? plan;
  final List<Map<String, dynamic>> planPosts = [];
  final List<Map<String, dynamic>> ticks = [];
  final Set<String> doneItems = {};

  Map<String, dynamic> _item(String id, String day, String kind, String text, {String? unit, String? unitTitle}) => {
        'id': id,
        'day': day,
        'kind': kind,
        'unit_id': unit,
        'unit_title': unitTitle,
        'text': text,
        'done': doneItems.contains(id),
        'auto_done': false,
      };

  Map<String, dynamic> planJson(String id) {
    final today = [
      _item('i1', '2030-05-20', 'revise', 'Revise Foundations', unit: 'u1', unitTitle: 'Foundations'),
      _item('i2', '2030-05-20', 'quiz', 'Quiz: Foundations', unit: 'u1', unitTitle: 'Foundations'),
    ];
    final tomorrow = [
      _item('i3', '2030-05-21', 'revise', 'Revise Mechanisms', unit: 'u2', unitTitle: 'Mechanisms'),
      _item('i4', '2030-05-21', 'mock', 'Mock test'),
    ];
    final all = [...today, ...tomorrow];
    final done = all.where((i) => i['done'] == true).length;
    return {
      'id': id,
      'exam_id': 'exam-1',
      'start_date': '2030-05-20',
      'end_date': '2030-06-01',
      'today': '2030-05-20',
      'days_left': 12,
      'done': done,
      'total': all.length,
      'percent': (100 * done / all.length).round(),
      'today_items': today,
      'overdue': <Map<String, dynamic>>[],
      'days': [
        {'day': '2030-05-20', 'items': today},
        {'day': '2030-05-21', 'items': tomorrow},
      ],
    };
  }

  Map<String, dynamic> exam(String title) => {
        'id': 'exam-1',
        'title': title,
        'exam_date': '2030-06-01',
        'days_left': 12,
        'source_kind': 'search',
        'unit_count': 2,
        'quizzes_taken': taken ? 1 : 0,
        'created_at': '2026-09-26T10:00:00Z',
        'units': [
          taken ? _unit('u1', 0, 'Foundations', taken: 1, last: 50, best: 50) : _unit('u1', 0, 'Foundations'),
          _unit('u2', 1, 'Mechanisms'),
        ],
        'mocks': <Map<String, dynamic>>[],
      };

  Map<String, dynamic> quiz({required bool mock}) => {
        'id': mock ? 'mock-1' : 'quiz-1',
        'exam_id': 'exam-1',
        'exam_title': 'Chemistry',
        'kind': mock ? 'mock' : 'unit',
        'unit_title': mock ? null : 'Foundations',
        'time_limit_seconds': mock ? 600 : null,
        'started_at': '2026-09-26T10:00:00Z',
        'seconds_left': mock ? mockSecondsLeft : null,
        'questions': [_question('q1', 0, 'Foundations'), _question('q2', 1, 'Foundations', choice: false)],
        'submitted': false,
      };

  Map<String, dynamic> result(String quizId, Map<String, dynamic> body) {
    final answers = {for (final a in body['answers'] as List) a['question_id']: a['response']};
    final q1Right = answers['q1'] == '0';
    return {
      ...quiz(mock: quizId == 'mock-1'),
      'seconds_left': null,
      'submitted': true,
      'score': {'correct': q1Right ? 1 : 0, 'graded': 2, 'total': 2, 'percent': q1Right ? 50 : 0},
      'skills': [
        {'skill': 'apply', 'correct': q1Right ? 1 : 0, 'total': 1},
        {'skill': 'analyse', 'correct': 0, 'total': 1},
      ],
      'results': [
        {
          ..._question('q1', 0, 'Foundations'),
          'response': answers['q1'] ?? '',
          'correct': q1Right,
          'correct_answer': 'Right q1',
          'explanation': 'Because it is right.',
          'feedback': '',
        },
        {
          ..._question('q2', 1, 'Foundations', choice: false),
          'response': answers['q2'] ?? '',
          'correct': false,
          'correct_answer': 'The model answer.',
          'explanation': 'The essential point.',
          'feedback': 'You missed the essential point.',
        },
      ],
    };
  }

  static http.Response _json(Object? data) =>
      http.Response(jsonEncode(data), 200, headers: const {'content-type': 'application/json'});

  late final http.Client client = MockClient.streaming((request, body) async {
    final path = request.url.path;
    final bytes = await body.toBytes();
    final json = request.headers['content-type']?.contains('application/json') == true && bytes.isNotEmpty
        ? jsonDecode(utf8.decode(bytes)) as Map<String, dynamic>
        : const <String, dynamic>{};
    http.Response? r;
    if (path.endsWith('/feed')) {
      r = _json({'has_history': false, 'generated_at': null, 'continue': [], 'related': [], 'explore': []});
    } else if (path.startsWith('/api/learners/') && path.endsWith('/exams')) {
      r = _json([
        {
          'id': 'exam-0',
          'title': 'Physics finals',
          'exam_date': '2030-01-10',
          'days_left': 3,
          'source_kind': 'pdf',
          'unit_count': 6,
          'quizzes_taken': 2,
          'created_at': '2026-09-20T10:00:00Z',
        },
      ]);
    } else if (path.startsWith('/api/learners/') && path.endsWith('/topics')) {
      r = _json([
        {'id': 'topic-1', 'title': 'Machine learning', 'percent': 35, 'chapter_count': 2,
         'lesson_count': 3, 'lessons_done': 1, 'updated_at': null},
      ]);
    } else if (path == '/api/exams' || path == '/api/exams/from-course' || path == '/api/exams/from-image') {
      createPaths.add(path);
      createBodies.add(json);
      r = _json(exam(path == '/api/exams' ? json['query'] as String : 'Machine learning'));
    } else if (path == '/api/exams/exam-1/plan' && request.method == 'GET') {
      r = _json(plan);
    } else if (path == '/api/exams/exam-1/plan') {
      planPosts.add(json);
      plan = planJson('plan-${planPosts.length}');
      r = _json(plan);
    } else if (path.startsWith('/api/exam-plan-items/')) {
      final id = request.url.pathSegments[2];
      ticks.add({'id': id, ...json});
      json['done'] == true ? doneItems.add(id) : doneItems.remove(id);
      plan = planJson(plan!['id'] as String);
      r = _json(plan);
    } else if (path == '/api/exams/exam-1') {
      r = _json(exam('Chemistry'));
    } else if (path == '/api/exam-units/u1/quiz') {
      r = _json(quiz(mock: false));
    } else if (path == '/api/exams/exam-1/mock') {
      r = _json(quiz(mock: true));
    } else if (path.startsWith('/api/exam-quizzes/') && path.endsWith('/check')) {
      checkBodies.add(json);
      final right = json['response'] == '0';
      r = _json({
        'question_id': json['question_id'],
        'response': json['response'],
        'correct': right,
        'correct_index': 0,
        'correct_answer': 'Right ${json['question_id']}',
        'explanation': 'Because it follows from the rule.',
      });
    } else if (path.startsWith('/api/exam-units/') && path.endsWith('/warmup')) {
      warmUpCalls.add(path);
      r = _json(warmUp(request.url.pathSegments[2], 'Foundations'));
    } else if (path == '/api/exams/exam-1/warmup') {
      warmUpCalls.add(path);
      r = _json([warmUp('u1', 'Foundations'), warmUp('u2', 'Mechanisms')]);
    } else if (path.startsWith('/api/exam-questions/') && path.endsWith('/scene')) {
      sceneCalls.add(request.url.pathSegments[2]);
      r = _json({
        'question_id': request.url.pathSegments[2],
        'script': [
          {'do': 'say', 'text': 'Here is the situation.'},
          {
            'do': 'ask',
            'question': 'Question?',
            'choices': [
              {'id': '0', 'text': 'A'},
              {'id': '1', 'text': 'B'},
            ],
          },
        ],
      });
    } else if (path.startsWith('/api/exam-quizzes/') && path.endsWith('/submit')) {
      submitBodies.add(json);
      taken = true;
      r = _json(result(request.url.pathSegments[2], json));
    }
    if (r != null) return http.StreamedResponse(Stream.value(r.bodyBytes), r.statusCode, headers: r.headers);
    final copy = http.Request(request.method, request.url)
      ..headers.addAll(request.headers)
      ..bodyBytes = bytes;
    return backend.client.send(copy);
  });

  VersaApi get api => VersaApi('http://test', client: client);
}

Future<void> _boot(WidgetTester tester, ExamHarness h, {Map<String, Object> settings = const {}}) async {
  tester.view.physicalSize = const Size(1400, 1100);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  SharedPreferences.setMockInitialValues({'learner_label': 'Asha', ...settings});
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
  await tester.tap(find.byKey(const ValueKey('nav-Modes')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const ValueKey('mode-exam')));
  await tester.pumpAndSettle();
}

Future<void> _tapKey(WidgetTester tester, String key, {bool settle = true}) async {
  final f = find.byKey(ValueKey(key));
  await tester.ensureVisible(f);
  await tester.pump();
  await tester.tap(f);
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
  }
}

Future<void> _buildChemistry(WidgetTester tester) async {
  await tester.enterText(find.byKey(const ValueKey('exam-subject-field')), 'Chemistry');
  await tester.pump();
  await _tapKey(tester, 'exam-search-go');
}

void main() {
  testWidgets('exam prep is live: the home lists exams with countdowns and offers four ways in', (tester) async {
    final h = ExamHarness();
    await _boot(tester, h);

    expect(find.text('What are you preparing for?'), findsOneWidget);
    for (final key in ['exam-subject-field', 'exam-upload-pdf', 'exam-link', 'exam-from-course', 'exam-date']) {
      expect(find.byKey(ValueKey(key)), findsOneWidget, reason: key);
    }
    expect(find.byKey(const ValueKey('exam-card-exam-0')), findsOneWidget);
    expect(find.text('10 Jan 2030 · in 3 days'), findsOneWidget);
    expect(find.text('6 units · 2 quizzes taken'), findsOneWidget);

    await _tapKey(tester, 'exams-back');
    expect(find.byKey(const ValueKey('mode-exam')), findsOneWidget);
  });

  testWidgets('a subject becomes a syllabus, and a unit quiz opens untimed', (tester) async {
    final h = ExamHarness();
    await _boot(tester, h);
    await _buildChemistry(tester);

    expect(h.createBodies.single['query'], 'Chemistry');
    expect(find.text('Chemistry'), findsWidgets);
    expect(find.text('Exam on 1 Jun 2030 · in 12 days'), findsOneWidget);
    expect(find.text('Not quizzed yet'), findsNWidgets(2));
    expect(find.byKey(const ValueKey('exam-start-mock')), findsOneWidget);

    await _tapKey(tester, 'exam-quiz-u1');
    // warmed up on the chapter first: no quiz is written until they're ready
    expect(h.warmUpCalls, ['/api/exam-units/u1/warmup']);
    expect(find.text('WARM-UP'), findsOneWidget);
    expect(find.text('Key point of Foundations.'), findsOneWidget);
    expect(find.byKey(const ValueKey('warmup-formula')), findsOneWidget);
    expect(find.text('A worked example for Foundations.'), findsOneWidget);
    expect(find.text('1. Question q1?'), findsNothing);
    await _tapKey(tester, 'warmup-start');
    expect(find.text('1. Question q1?'), findsOneWidget);
    expect(find.text('2. Question q2?'), findsNothing); // one question at a time
    expect(find.byKey(const ValueKey('quiz-dot-1')), findsOneWidget);
    expect(find.byKey(const ValueKey('quiz-clock')), findsNothing); // a unit quiz is untimed
  });

  testWidgets('answering and handing in shows the score, the right answer and why', (tester) async {
    final h = ExamHarness();
    await _boot(tester, h);
    await _buildChemistry(tester);
    await _tapKey(tester, 'exam-quiz-u1');
    await _tapKey(tester, 'warmup-start');

    // one question at a time, saying what it tests
    expect(find.text('Question 1 of 2'), findsOneWidget);
    expect(find.text('APPLY · PUZZLE'), findsOneWidget);
    expect(find.byKey(const ValueKey('quiz-next')), findsOneWidget);
    expect(tester.widget<FilledButton>(find.byKey(const ValueKey('quiz-next'))).onPressed, isNull,
        reason: 'answer it first');

    // a tap is checked there and then: right or not, the answer and why
    await _tapKey(tester, 'quiz-choice-q1-1'); // the wrong one
    expect(h.checkBodies.single, {'question_id': 'q1', 'response': '1'});
    expect(find.text('Not quite'), findsOneWidget);
    expect(find.text('Because it follows from the rule.'), findsOneWidget);
    await _tapKey(tester, 'quiz-choice-q1-0'); // no second go
    expect(h.checkBodies.length, 1);

    await _tapKey(tester, 'quiz-next');
    expect(find.text('Question 2 of 2'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('quiz-typed-q2')), 'my own answer');
    await tester.pump();
    expect(find.text('2 of 2 answered'), findsOneWidget);
    await _tapKey(tester, 'quiz-submit');

    expect(h.submitBodies.single['answers'], [
      {'question_id': 'q1', 'response': '1'},
      {'question_id': 'q2', 'response': 'my own answer'},
    ]);
    expect(find.text('0%'), findsOneWidget);
    expect(find.text('0 of 2 correct'), findsOneWidget);
    expect(find.text('Wrong q1'), findsOneWidget); // what they picked, as text
    expect(find.text('Right q1'), findsOneWidget); // the correct answer
    expect(find.text('You missed the essential point.'), findsOneWidget);
    // what it tested, skill by skill
    expect(find.byKey(const ValueKey('quiz-skills')), findsOneWidget);
    expect(tester.widget<Text>(find.byKey(const ValueKey('quiz-skill-apply'))).data, '0/1');

    await _tapKey(tester, 'quiz-done');
    expect(find.byKey(const ValueKey('exam-unit-score-u1')), findsOneWidget);
    expect(find.text('Quiz again'), findsOneWidget);
  });

  testWidgets('handing in with blanks asks first', (tester) async {
    final h = ExamHarness();
    await _boot(tester, h);
    await _buildChemistry(tester);
    await _tapKey(tester, 'exam-quiz-u1');
    await _tapKey(tester, 'warmup-start');

    await _tapKey(tester, 'quiz-choice-q1-0');
    expect(find.text('Correct'), findsOneWidget);
    await _tapKey(tester, 'quiz-next');
    await _tapKey(tester, 'quiz-submit');
    expect(find.textContaining('1 question is still unanswered'), findsOneWidget);
    expect(h.submitBodies, isEmpty);
    await _tapKey(tester, 'quiz-confirm-submit');
    expect(h.submitBodies.single['answers'], [
      {'question_id': 'q1', 'response': '0'},
    ]);
    expect(find.text('50%'), findsOneWidget);
  });

  testWidgets('a mock counts down and hands itself in when time runs out', (tester) async {
    final h = ExamHarness()..mockSecondsLeft = 3;
    var now = DateTime(2026, 9, 26, 10);
    quizClock = () => now;
    addTearDown(() => quizClock = DateTime.now);
    await _boot(tester, h);
    await _buildChemistry(tester);
    await _tapKey(tester, 'exam-start-mock');
    // every chapter warmed up first -- the clock hasn't started
    expect(find.text('WARM-UP · CHAPTER 1 OF 2'), findsOneWidget);
    expect(find.byKey(const ValueKey('quiz-clock')), findsNothing);
    await _tapKey(tester, 'warmup-next');
    expect(find.text('Mechanisms'), findsWidgets);
    await _tapKey(tester, 'warmup-start', settle: false);

    expect(find.byKey(const ValueKey('quiz-clock')), findsOneWidget);
    expect(find.text('0:03'), findsOneWidget);
    await _tapKey(tester, 'quiz-choice-q1-0', settle: false);
    expect(h.checkBodies, isEmpty, reason: 'a mock is marked when it is handed in');
    expect(find.byKey(const ValueKey('quiz-feedback')), findsNothing);
    now = now.add(const Duration(seconds: 1));
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('0:02'), findsOneWidget);
    expect(h.submitBodies, isEmpty);
    for (var i = 0; i < 3; i++) {
      now = now.add(const Duration(seconds: 1));
      await tester.pump(const Duration(seconds: 1));
    }
    await tester.pump();
    expect(h.submitBodies.single['answers'], [
      {'question_id': 'q1', 'response': '0'},
    ]);
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.text('Time\'s up -- your answers were handed in.'), findsOneWidget);
    expect(find.byKey(const ValueKey('quiz-score')), findsOneWidget);
    expect(find.byKey(const ValueKey('quiz-clock')), findsNothing);
    await tester.pumpAndSettle();
  });

  testWidgets('with the stage on, each question is set up by the slime before its choices show', (tester) async {
    final h = ExamHarness();
    await _boot(tester, h, settings: {'show_stage_panel': true});
    await _buildChemistry(tester);
    await _tapKey(tester, 'exam-quiz-u1');
    expect(find.byKey(const ValueKey('warmup-stage')), findsOneWidget); // the slime explains first
    await _tapKey(tester, 'warmup-start', settle: false);
    await tester.pump(const Duration(milliseconds: 500));

    expect(find.byKey(const ValueKey('exam-stage')), findsOneWidget);
    expect(h.sceneCalls, ['q1']);
    expect(find.byKey(const ValueKey('quiz-watch-stage')), findsOneWidget);
    // (the test binding doesn't run the stage's clock, so the slime never
    // reaches its question here: the choices come after a short wait)
    await tester.pump(const Duration(seconds: 11));
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.byKey(const ValueKey('quiz-choice-q1-0')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('quiz-choice-q1-0')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.checkBodies.single, {'question_id': 'q1', 'response': '0'});
  });

  testWidgets('an exam can be built from one of your courses', (tester) async {
    final h = ExamHarness();
    await _boot(tester, h);

    await _tapKey(tester, 'exam-from-course');
    expect(find.text('Which course is the exam on?'), findsOneWidget);
    await _tapKey(tester, 'exam-course-topic-1');
    expect(h.createPaths.single, '/api/exams/from-course');
    expect(h.createBodies.single['topic_id'], 'topic-1');
    expect(find.text('Machine learning'), findsWidgets);
  });

  testWidgets('an exam can be built from a photo of the syllabus', (tester) async {
    final h = ExamHarness();
    final realPicture = picturePicker, realCamera = canTakePhoto;
    addTearDown(() {
      picturePicker = realPicture;
      canTakePhoto = realCamera;
    });
    canTakePhoto = () => false;
    picturePicker = () async => (name: 'syllabus.jpg', bytes: Uint8List.fromList([0xFF, 0xD8, 0xFF, 1, 2]));
    await _boot(tester, h);

    await _tapKey(tester, 'exam-upload-image');
    expect(h.backend.picturesUploaded, 1, reason: 'read once, on upload');
    expect(h.createPaths.single, '/api/exams/from-image');
    expect(h.createBodies.single, {'learner_id': 'learner-Asha', 'image_id': 'img-1'});
    expect(find.text('Machine learning'), findsWidgets);
  });

  testWidgets("a study plan is made and starts today's quiz; nothing is ticked by hand", (tester) async {
    final h = ExamHarness();
    await _boot(tester, h);
    await _buildChemistry(tester);

    expect(find.byKey(const ValueKey('plan-card')), findsOneWidget);
    expect(find.text('Make my study plan'), findsOneWidget); // the exam has a date: no picker
    await _tapKey(tester, 'plan-make');
    expect(h.planPosts.single['end_date'], '2030-06-01');
    expect(find.text('12 days to go · 0 of 4 done'), findsOneWidget);
    expect(find.text('Revise Foundations'), findsOneWidget);
    expect(find.text('Revise Mechanisms'), findsNothing); // tomorrow is only in the full plan

    await tester.tap(find.byKey(const ValueKey('plan-status-i1')));
    await tester.pumpAndSettle();
    expect(h.ticks, isEmpty);
    expect(find.text('12 days to go · 0 of 4 done'), findsOneWidget);

    expect(find.byKey(const ValueKey('plan-start-i1')), findsNothing); // revising has no Start
    await _tapKey(tester, 'plan-start-i2');
    await _tapKey(tester, 'warmup-start'); // warmed up first, from the plan too
    expect(find.text('1. Question q1?'), findsOneWidget); // the Foundations quiz opened
  });

  testWidgets('the full plan lists every day, and re-planning asks first', (tester) async {
    final h = ExamHarness();
    await _boot(tester, h);
    await _buildChemistry(tester);
    await _tapKey(tester, 'plan-make');

    await _tapKey(tester, 'plan-open-full');
    expect(find.text('Study plan'), findsWidgets);
    expect(find.byKey(const ValueKey('plan-day-2030-05-20')), findsOneWidget);
    expect(find.text('Today'), findsOneWidget);
    expect(find.text('Tomorrow'), findsOneWidget);
    expect(find.text('Revise Mechanisms'), findsOneWidget);
    expect(find.byKey(const ValueKey('plan-status-i3')), findsOneWidget);
    await _tapKey(tester, 'plan-back');

    await _tapKey(tester, 'plan-replan');
    expect(find.text('Re-plan from today?'), findsOneWidget);
    await _tapKey(tester, 'plan-replan-confirm');
    expect(h.planPosts, hasLength(2));
  });
}
