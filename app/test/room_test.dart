import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:versa_app/api.dart';
import 'package:versa_app/main.dart';
import 'package:versa_app/room/room_api.dart';
import 'package:versa_app/room/room_controller.dart';
import 'package:versa_app/room/room_models.dart';
import 'package:versa_app/room/room_screen.dart';

import 'support/fakes.dart';

/// A scripted room socket: the test decides what the "server" sends.
class FakeRoomTransport implements RoomTransport {
  final _events = StreamController<RoomEvent>();
  final List<Map<String, String>> sent = [];
  bool closed = false;

  @override
  Stream<RoomEvent> get events => _events.stream;

  void emitJson(Map<String, dynamic> frame) => _events.add(parseRoomEvent(frame)!);

  Future<void> drop() => _events.close();

  @override
  void sendMessage(String text) => sent.add({'type': 'message', 'text': text});

  @override
  void pick(String optionId) => sent.add({'type': 'pick', 'option_id': optionId});

  @override
  void typing() => sent.add({'type': 'typing'});

  @override
  Future<void> close() async {
    closed = true;
    if (!_events.isClosed) await _events.close();
  }
}

final _t0 = DateTime.now().toUtc();

Map<String, dynamic> msg(
  int seq, {
  String sender = 'member',
  String? memberId,
  String name = '',
  String kind = 'text',
  String text = 'hi',
  String? to,
  String? toName,
  bool private = false,
  Map<String, dynamic> meta = const {},
}) =>
    {
      'id': 'm$seq',
      'seq': seq,
      'sender': sender,
      'member_id': memberId,
      'sender_name': sender == 'versa' ? 'Versa' : name,
      'kind': kind,
      'text': text,
      'to_member_id': to,
      'to_name': toName,
      'private': private,
      'meta': meta,
      'created_at': _t0.add(Duration(seconds: seq)).toIso8601String(),
    };

const _room = {
  'id': 'r1',
  'code': 'calc-101',
  'title': 'Derivatives',
  'source_kind': 'search',
  'query': 'derivatives',
  'outline': [
    {'title': 'Limits', 'summary': 'Where it starts.'},
    {'title': 'Rules', 'summary': 'The shortcuts.'},
  ],
  'created_by': 'Asha',
};

Map<String, dynamic> board({List<Map<String, dynamic>> options = const [], bool benDone = false}) => {
      'members': [
        {'id': 'asha', 'name': 'Asha', 'online': true},
        {'id': 'ben', 'name': 'Ben', 'online': false},
      ],
      'tasks': [
        {
          'id': 't1', 'member_id': 'asha', 'member_name': 'Asha', 'kind': 'learn',
          'description': 'Explain what a limit is', 'done': false, 'created_at': _t0.toIso8601String(),
        },
        {
          'id': 't2', 'member_id': 'ben', 'member_name': 'Ben', 'kind': 'practice',
          'description': 'Differentiate x squared', 'done': benDone, 'created_at': _t0.toIso8601String(),
        },
      ],
      'options': options,
    };

Map<String, dynamic> state(List<Map<String, dynamic>> messages, {Map<String, dynamic>? b}) => {
      'type': 'state',
      'room': _room,
      'me': {'id': 'asha', 'name': 'Asha'},
      'messages': messages,
      'board': b ?? board(),
    };

/// FakeBackend plus the rooms REST routes.
class RoomHarness {
  final backend = FakeBackend();
  final List<Map<String, dynamic>> joins = [];
  final List<Map<String, dynamic>> creates = [];

  static http.Response _json(Object? data, [int status = 200]) =>
      http.Response(jsonEncode(data), status, headers: const {'content-type': 'application/json'});

  late final http.Client client = MockClient((request) async {
    final path = request.url.path;
    if (path.endsWith('/feed')) {
      return _json({'has_history': false, 'generated_at': null, 'continue': [], 'related': [], 'explore': []});
    }
    if (path == '/api/rooms/summaries') {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      return _json([
        for (final m in body['memberships'] as List)
          {
            'code': 'calc-101', 'title': 'Derivatives', 'member_id': (m as Map)['member_id'], 'name': 'Asha',
            'member_names': ['Asha', 'Ben'], 'online': 1, 'unread': 3,
            'last_message': msg(9, memberId: 'ben', name: 'Ben', text: 'see you there'),
            'created_at': _t0.toIso8601String(),
          },
      ]);
    }
    if (path == '/api/rooms/calc-101/join') {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      joins.add(body);
      return _json({'room': _room, 'member': {'id': 'asha', 'name': body['name']}});
    }
    if (path == '/api/rooms/nope/join') return _json({'detail': "there's no room with that code"}, 404);
    if (path == '/api/rooms' && request.method == 'POST') {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      creates.add(body);
      if (body['code'] == 'taken') return _json({'detail': 'the room code is taken'}, 409);
      return _json({'room': {..._room, 'code': body['code']}, 'member': {'id': 'asha', 'name': body['name']}});
    }
    return backend.client.send(http.Request(request.method, request.url)
      ..headers.addAll(request.headers)
      ..body = request.body).then(http.Response.fromStream);
  });

  VersaApi get api => VersaApi('http://test', client: client);
}

Future<SharedPreferences> _boot(WidgetTester tester, RoomHarness h,
    {Size size = const Size(1400, 1000), Map<String, Object> prefs = const {}}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  SharedPreferences.setMockInitialValues({'learner_label': 'Asha', ...prefs});
  final p = await SharedPreferences.getInstance();
  await tester.pumpWidget(VersaApp(api: h.api, prefs: p));
  await tester.pumpAndSettle();
  return p;
}

/// Into a room: its spinner runs until the socket's state arrives, so step
/// frames through the page transition instead of settling.
Future<void> _enterRoom(WidgetTester tester) async {
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> _openRooms(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('nav-Modes')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const ValueKey('mode-study')));
  await tester.pumpAndSettle();
}

void main() {
  late Future<RoomTransport> Function(Uri) realConnector;
  late FakeRoomTransport socket;
  final uris = <Uri>[];
  setUp(() {
    realConnector = roomSocketConnector;
    socket = FakeRoomTransport();
    uris.clear();
    roomSocketConnector = (uri) async {
      uris.add(uri);
      return socket;
    };
  });
  tearDown(() => roomSocketConnector = realConnector);

  group('RoomController', () {
    test('state, then live messages without duplicates, board and typing', () async {
      final seen = <int>[];
      final c = RoomController(code: 'calc-101', memberId: 'asha', connect: (_, _) async => socket, onSeen: seen.add);
      await c.start();
      socket.emitJson(state([msg(1, sender: 'system', memberId: 'asha', kind: 'event', text: 'Asha created the room')]));
      await Future<void>.delayed(Duration.zero);
      expect(c.isLive, isTrue);
      expect(c.room!.title, 'Derivatives');

      socket.emitJson({'type': 'message', 'message': msg(2, memberId: 'ben', name: 'Ben', text: 'hey')});
      socket.emitJson({'type': 'message', 'message': msg(2, memberId: 'ben', name: 'Ben', text: 'hey')});
      socket.emitJson({'type': 'typing', 'member_id': null, 'name': 'Versa', 'on': true});
      await Future<void>.delayed(Duration.zero);
      expect(c.messages.map((m) => m.seq), [1, 2]);
      expect(c.versaTyping, isTrue);
      expect(seen.last, 2);

      socket.emitJson({'type': 'message', 'message': msg(3, sender: 'versa', text: 'nice')});
      await Future<void>.delayed(Duration.zero);
      expect(c.versaTyping, isFalse, reason: 'a message from Versa ends its typing');

      // my own typing isn't echoed to me
      socket.emitJson({'type': 'typing', 'member_id': 'asha', 'name': 'Asha', 'on': true});
      await Future<void>.delayed(Duration.zero);
      expect(c.typingNames, isEmpty);

      expect(c.send('  hello  '), isTrue);
      expect(socket.sent.last, {'type': 'message', 'text': 'hello'});
      c.typing();
      c.typing();
      expect(socket.sent.where((s) => s['type'] == 'typing').length, 1, reason: 'throttled');
      c.dispose();
      expect(socket.closed, isTrue);
    });

    test('a click is sent once and the set waits for the server', () async {
      final c = RoomController(code: 'calc-101', memberId: 'asha', connect: (_, _) async => socket);
      await c.start();
      final set = {
        'set_id': 's1', 'prompt': 'What next?', 'for_everyone': true,
        'options': [{'id': 'o1', 'text': 'Example'}, {'id': 'o2', 'text': 'Quiz'}],
      };
      socket.emitJson(state(const [], b: board(options: [set])));
      await Future<void>.delayed(Duration.zero);
      final s = c.board.options.single;
      c.pick(s, s.options.first);
      c.pick(s, s.options.last);
      expect(socket.sent, [{'type': 'pick', 'option_id': 'o1'}]);
      expect(c.pickedSetIds, {'s1'});
      socket.emitJson({'type': 'board', 'board': board()});
      await Future<void>.delayed(Duration.zero);
      expect(c.pickedSetIds, isEmpty);
      expect(c.board.options, isEmpty);
      c.dispose();
    });

    test('reconnects after the connection drops, and resyncs from the new state', () async {
      final sockets = [FakeRoomTransport(), FakeRoomTransport()];
      var opened = 0;
      final c = RoomController(code: 'calc-101', memberId: 'asha', connect: (_, _) async => sockets[opened++]);
      await c.start();
      sockets[0].emitJson(state([msg(1, memberId: 'ben', name: 'Ben', text: 'one')]));
      await Future<void>.delayed(Duration.zero);
      await sockets[0].drop();
      await Future<void>.delayed(Duration.zero);
      expect(c.status, RoomStatus.reconnecting);
      expect(c.send('lost?'), isFalse);
      await Future<void>.delayed(const Duration(milliseconds: 1100));
      expect(opened, 2);
      sockets[1].emitJson(state([
        msg(1, memberId: 'ben', name: 'Ben', text: 'one'),
        msg(2, memberId: 'ben', name: 'Ben', text: 'two'),
      ]));
      await Future<void>.delayed(Duration.zero);
      expect(c.isLive, isTrue);
      expect(c.messages.map((m) => m.text), ['one', 'two']);
      c.dispose();
    });
  });

  testWidgets('Modes -> Study with others: the rooms list and joining a room', (tester) async {
    final h = RoomHarness();
    await _boot(tester, h, prefs: {
      'rooms.learner-Asha': jsonEncode([
        {'code': 'calc-101', 'member_id': 'asha', 'name': 'Asha', 'seen_seq': 0},
      ]),
    });
    await _openRooms(tester);

    expect(find.text('Learn together'), findsOneWidget);
    expect(find.byKey(const ValueKey('room-row-calc-101')), findsOneWidget);
    expect(find.byKey(const ValueKey('room-unread-calc-101')), findsOneWidget);
    expect(find.text('Ben: see you there'), findsOneWidget);

    // join with a code that doesn't exist: the server's message shows
    await tester.tap(find.byKey(const ValueKey('rooms-join')));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(find.byKey(const ValueKey('join-name'))).controller!.text, 'Asha');
    await tester.enterText(find.byKey(const ValueKey('join-code')), 'nope');
    await tester.tap(find.byKey(const ValueKey('join-go')));
    await tester.pumpAndSettle();
    expect(find.text("there's no room with that code"), findsOneWidget);

    // a real code opens the room over its socket
    await tester.enterText(find.byKey(const ValueKey('join-code')), 'calc-101');
    await tester.tap(find.byKey(const ValueKey('join-go')));
    await _enterRoom(tester);
    expect(h.joins.last, {'name': 'Asha'});
    expect(uris.single.path, '/api/rooms/calc-101/ws');
    expect(uris.single.queryParameters['member_id'], 'asha');
    socket.emitJson(state([msg(1, sender: 'system', memberId: 'asha', kind: 'event', text: 'Asha created the room')]));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('room-title')), findsOneWidget);
    expect(find.text('Asha created the room'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('room-back')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('rooms-back')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('mode-study')), findsOneWidget);
  });

  testWidgets('creating a room: validation, a taken code, then in', (tester) async {
    final h = RoomHarness();
    await _boot(tester, h);
    await _openRooms(tester);
    await tester.tap(find.byKey(const ValueKey('rooms-create')));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('create-go')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('create-error')), findsOneWidget);

    await tester.enterText(find.byKey(const ValueKey('create-code')), 'taken');
    await tester.enterText(find.byKey(const ValueKey('create-topic')), 'derivatives');
    await tester.tap(find.byKey(const ValueKey('create-go')));
    await tester.pumpAndSettle();
    expect(find.text('the room code is taken'), findsOneWidget);

    await tester.enterText(find.byKey(const ValueKey('create-code')), 'calc-101');
    await tester.tap(find.byKey(const ValueKey('create-go')));
    await _enterRoom(tester);
    expect(h.creates.last, {'code': 'calc-101', 'name': 'Asha', 'topic': 'derivatives'});
    expect(uris, isNotEmpty);
    socket.emitJson(state(const []));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('room-for-you')), findsOneWidget);

    // back on the list, the new room is remembered on this device
    await tester.tap(find.byKey(const ValueKey('room-back')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('room-row-calc-101')), findsOneWidget);
  });

  testWidgets('inside a room: the group chat, the board and "For you"', (tester) async {
    final h = RoomHarness();
    await _boot(tester, h, prefs: {
      'rooms.learner-Asha': jsonEncode([
        {'code': 'calc-101', 'member_id': 'asha', 'name': 'Asha', 'seen_seq': 0},
      ]),
    });
    await _openRooms(tester);
    await tester.tap(find.byKey(const ValueKey('room-row-calc-101')));
    await _enterRoom(tester);

    final options = {
      'set_id': 's1', 'prompt': 'What next?', 'for_everyone': true,
      'options': [{'id': 'o1', 'text': 'An example'}, {'id': 'o2', 'text': 'A quiz'}],
    };
    socket.emitJson(state([
      msg(1, sender: 'system', memberId: 'asha', kind: 'event', text: 'Asha created the room'),
      msg(2, sender: 'versa', kind: 'task', text: 'Explain what a limit is', to: 'asha', toName: 'Asha',
          meta: {'task_kind': 'learn'}),
      msg(3, memberId: 'ben', name: 'Ben', text: 'I think a limit is where it ends?'),
      msg(4, sender: 'versa', text: 'Close! Think of approaching.', to: 'asha', toName: 'Asha', private: true),
      msg(5, sender: 'versa', kind: 'question', text: 'What next?', meta: {'options': ['An example', 'A quiz']}),
      msg(6, memberId: 'asha', name: 'Asha', text: 'mine'),
    ], b: board(options: [options])));
    await tester.pumpAndSettle();

    // header: title, members + Versa
    expect(find.text('Derivatives'), findsOneWidget);
    expect(find.textContaining('You, Ben, Versa'), findsOneWidget);
    // chat: a task card for me, a private line, a question
    expect(find.text('New task for you · learn'), findsOneWidget);
    expect(find.text('Only you can see this'), findsOneWidget);
    expect(find.text('I think a limit is where it ends?'), findsOneWidget);
    expect(find.text('Answer in "For you" above'), findsOneWidget);
    // board: everyone's current task
    expect(find.byKey(const ValueKey('board-member-Ben')), findsOneWidget);
    expect(find.text('Differentiate x squared'), findsOneWidget);
    // For you: my task and clickable options
    expect(find.byKey(const ValueKey('for-you-task')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('room-option-o1')));
    await tester.pump();
    expect(socket.sent.last, {'type': 'pick', 'option_id': 'o1'});

    // sending a message, and typing
    await tester.enterText(find.byKey(const ValueKey('composer-field')), '@Versa help');
    await tester.pump();
    expect(socket.sent.any((s) => s['type'] == 'typing'), isTrue);
    await tester.tap(find.byKey(const ValueKey('send-button')));
    await tester.pump();
    expect(socket.sent.last, {'type': 'message', 'text': '@Versa help'});

    // Versa typing shows under the chat and in the header
    socket.emitJson({'type': 'typing', 'member_id': null, 'name': 'Versa', 'on': true});
    await tester.pump();
    expect(find.byKey(const ValueKey('room-typing')), findsOneWidget);
    socket.emitJson({'type': 'message', 'message': msg(7, sender: 'versa', kind: 'content', text: 'A limit is…')});
    await tester.pump();
    expect(find.byKey(const ValueKey('room-typing')), findsNothing);
    expect(find.text('EXPLANATION'), findsOneWidget);

    // the invite dialog shows the code
    await tester.tap(find.byKey(const ValueKey('room-invite')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('invite-code')), findsOneWidget);
    await tester.tap(find.text('Done'));
    await tester.pumpAndSettle();

    // reading the room marks it seen on this device
    final prefs = await SharedPreferences.getInstance();
    final saved = jsonDecode(prefs.getString('rooms.learner-Asha')!) as List;
    expect((saved.single as Map)['seen_seq'], 7);
  });

  testWidgets('on a phone the top boxes become tabs', (tester) async {
    final h = RoomHarness();
    await _boot(tester, h, size: const Size(420, 900), prefs: {
      'rooms.learner-Asha': jsonEncode([
        {'code': 'calc-101', 'member_id': 'asha', 'name': 'Asha', 'seen_seq': 0},
      ]),
    });
    await tester.tap(find.text('Modes').last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const ValueKey('mode-study')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('mode-study')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('room-row-calc-101')));
    await _enterRoom(tester);
    socket.emitJson(state([msg(1, memberId: 'ben', name: 'Ben', text: 'hello from a phone')]));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('room-top-tabs')), findsOneWidget);
    expect(find.text('hello from a phone'), findsOneWidget);
    await tester.tap(find.text('For you'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('for-you-task')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
