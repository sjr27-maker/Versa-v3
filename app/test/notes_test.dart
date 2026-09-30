import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:versa_app/notes/notes_api.dart';
import 'package:versa_app/notes/notes_sheet.dart';

Map<String, dynamic> _notesJson({String id = 'n1', int answers = 2}) => {
      'id': id,
      'session_id': 's1',
      'title': 'Solving quadratic equations',
      'topics': [
        {
          'name': 'Factorising',
          'points': [r'Split $ax^2 + bx + c$ into two brackets.', 'If a product is **zero**, one factor is zero.'],
          'formulas': ['x^2 - 5x + 6 = (x-2)(x-3)'],
          'example': r'$x^2 - 5x + 6 = 0$ gives $x = 2$ or $x = 3$.',
        },
        {'name': 'The formula', 'points': ['It solves any quadratic.'], 'formulas': [], 'example': null},
      ],
      'summary': 'You solved quadratics by factorising, then met the formula.',
      'stopped_at': 'You were about to try the formula.',
      'answers': answers,
      'created_at': '2026-09-30T10:00:00+00:00',
    };

/// A server that only knows the notes endpoints.
class _NotesServer {
  int answers = 2;
  Map<String, dynamic>? notes;
  bool upToDate = false;
  final List<String> calls = [];

  late final http.Client client = MockClient((request) async {
    calls.add('${request.method} ${request.url.path}');
    final path = request.url.path;
    if (path == '/api/sessions/s1/notes' && request.method == 'GET') {
      return http.Response(
          jsonEncode({'notes': notes, 'answers': answers, 'up_to_date': upToDate, 'cost': 2}), 200);
    }
    if (path == '/api/sessions/s1/notes' && request.method == 'POST') {
      if (answers == 0) return http.Response(jsonEncode({'detail': 'nothing to make notes from yet'}), 422);
      notes = _notesJson(id: 'n${calls.length}', answers: answers);
      upToDate = true;
      return http.Response(jsonEncode(notes), 200);
    }
    if (path.startsWith('/api/sessions/s1/notes/') && path.endsWith('/pdf')) {
      return http.Response.bytes(utf8.encode('%PDF-1.4 fake'), 200, headers: {'content-type': 'application/pdf'});
    }
    return http.Response('not found', 404);
  });

  NotesApi get api => NotesApi('http://versa.test', client: client);
}

Future<void> _pump(WidgetTester tester, _NotesServer server,
    {Future<void> Function(ChatNotes, List<int>, Rect?)? share, Future<bool> Function(ChatNotes, List<int>)? download}) async {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(body: NotesPanel(api: server.api, sessionId: 's1', sharePdf: share, downloadPdf: download)),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('opening the sheet makes nothing; Generate writes the notes and shows them', (tester) async {
    final server = _NotesServer();
    await _pump(tester, server);
    expect(server.calls, ['GET /api/sessions/s1/notes']);
    expect(find.text('Notes of this chat'), findsOneWidget);
    expect(find.text('2 answers so far'), findsOneWidget);
    expect(find.text('Costs 2 Sparks'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('notes-generate')));
    await tester.pumpAndSettle();
    expect(server.calls.where((c) => c.startsWith('POST')).length, 1);
    expect(find.byKey(const ValueKey('notes-title')), findsOneWidget);
    expect(find.text('Solving quadratic equations'), findsOneWidget);
    expect(find.text('TOPICS COVERED'), findsOneWidget);
    expect(find.byKey(const ValueKey('notes-topic-1')), findsOneWidget);
    expect(find.byKey(const ValueKey('notes-download')), findsOneWidget);
    expect(find.byKey(const ValueKey('notes-share')), findsOneWidget);
    expect(find.byKey(const ValueKey('notes-out-of-date')), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Download saves the PDF, Share hands it to the share sheet, and it is fetched once', (tester) async {
    final server = _NotesServer()
      ..notes = _notesJson()
      ..upToDate = true;
    ChatNotes? shared;
    List<int>? saved;
    await _pump(tester, server, share: (n, b, _) async {
      shared = n;
    }, download: (n, b) async {
      saved = b;
      return true;
    });
    expect(find.byKey(const ValueKey('notes-generate')), findsNothing); // already made: shown straight away

    await tester.tap(find.byKey(const ValueKey('notes-download')));
    await tester.pumpAndSettle();
    expect(server.calls.last, 'GET /api/sessions/s1/notes/n1/pdf');
    expect(utf8.decode(saved!), startsWith('%PDF'));
    expect(find.byKey(const ValueKey('notes-saved')), findsOneWidget);
    expect(find.text('Saved versa-notes-solving-quadratic-equations.pdf'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('notes-share')));
    await tester.pumpAndSettle();
    expect(shared?.fileName, 'versa-notes-solving-quadratic-equations.pdf');
    expect(server.calls.where((c) => c.endsWith('/pdf')).length, 1); // fetched once
    expect(server.calls.where((c) => c.startsWith('POST')), isEmpty); // nothing was written
  });

  testWidgets('studying more shows Update; an empty chat has nothing to generate', (tester) async {
    final server = _NotesServer()
      ..notes = _notesJson(answers: 1)
      ..answers = 3;
    await _pump(tester, server);
    expect(find.byKey(const ValueKey('notes-out-of-date')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('notes-update')));
    await tester.pumpAndSettle();
    expect(server.calls.where((c) => c.startsWith('POST')).length, 1);
    expect(find.byKey(const ValueKey('notes-out-of-date')), findsNothing);

    final empty = _NotesServer()..answers = 0;
    await _pump(tester, empty);
    expect(find.text('Nothing to make notes from yet'), findsOneWidget);
    expect(find.byKey(const ValueKey('notes-generate')), findsNothing);
  });
}
