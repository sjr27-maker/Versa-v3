import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:versa_app/api.dart';
import 'package:versa_app/chat_transport.dart';
import 'package:versa_app/models.dart';

/// A scripted chat connection: the test decides which frames the "server"
/// sends and when, and can see what the app sent.
class FakeTransport implements ChatTransport {
  final _controller = StreamController<ServerEvent>();
  final List<Map<String, String>> sent = [];
  bool closed = false;

  @override
  Stream<ServerEvent> get events => _controller.stream;

  void emit(ServerEvent event) => _controller.add(event);

  /// The connection drops.
  Future<void> drop() => _controller.close();

  @override
  void sendMessage(String text) => sent.add({'type': 'message', 'text': text});

  @override
  void selectOption(String optionId) => sent.add({'type': 'select_option', 'option_id': optionId});

  @override
  Future<void> close() async {
    closed = true;
    if (!_controller.isClosed) await _controller.close();
  }
}

class _FakeSession {
  _FakeSession({
    required this.id,
    required this.learnerId,
    required this.mode,
    required this.createdAt,
  }) : lastActivityAt = createdAt;

  final String id;
  final String learnerId;
  final String mode;
  final DateTime createdAt;
  DateTime lastActivityAt;
  int turnCount = 0;
  String? preview;

  Map<String, dynamic> toJson() => {
        'session_id': id,
        'app_mode': mode,
        'turn_count': turnCount,
        'created_at': createdAt.toIso8601String(),
        'last_activity_at': lastActivityAt.toIso8601String(),
        'preview': preview,
      };
}

/// A scripted REST server. Counts session creations so tests can prove a
/// reconnect reuses the same chat; also tracks each session's sidebar row and
/// (optionally, per `historyBySession`) its resumable turn-by-turn history.
class FakeBackend {
  FakeBackend({this.up = true, this.llm = 'live'});

  bool up;
  String llm;
  int sessionsCreated = 0;
  final List<String> learnersSeen = [];
  final List<_FakeSession> _sessions = [];

  /// sessionId -> the raw HistoryTurn-shaped rows `GET .../history` returns.
  /// A test sets this directly; unset -> `[]` (a chat with nothing to replay).
  final Map<String, List<Map<String, dynamic>>> historyBySession = {};

  /// `upsertLearner`'s deterministic id for a given label — lets a test seed
  /// a chat for a learner before that learner has actually signed in.
  String learnerIdFor(String label) => 'learner-$label';

  /// Seed a past chat directly (bypassing the HTTP round-trip that would
  /// normally create + use one) — for a test that needs chats to already
  /// exist before the app opens Sandbox. Returns the new session id.
  String seedSession({
    required String learnerId,
    String mode = 'sandbox',
    String? preview,
    int turnCount = 0,
    DateTime? lastActivityAt,
  }) {
    sessionsCreated++;
    final created = lastActivityAt ?? DateTime.now();
    final s = _FakeSession(id: 'session-$sessionsCreated', learnerId: learnerId, mode: mode, createdAt: created)
      ..turnCount = turnCount
      ..preview = preview
      ..lastActivityAt = created;
    _sessions.add(s);
    return s.id;
  }

  /// `http.Response` picks its body's encoding from the `content-type`
  /// header (UTF-8 for `application/json`, latin1 -- too narrow for a model's
  /// own text -- for anything else, including no header at all); the real
  /// server always sends that header, so the fake must too, or a name/answer
  /// with a plain em dash or "…" throws instead of round-tripping.
  static http.Response _json(Object? data, [int status = 200]) => http.Response(
        jsonEncode(data),
        status,
        headers: const {'content-type': 'application/json'},
      );

  late final http.Client client = MockClient((request) async {
    if (!up) throw http.ClientException('connection refused');
    final segments = request.url.pathSegments;

    if (request.url.path == '/api/health') {
      return _json({'status': 'ok', 'llm': llm});
    }
    if (request.url.path == '/api/learners') {
      final label = (jsonDecode(request.body) as Map)['label'] as String;
      learnersSeen.add(label);
      return _json({'id': 'learner-$label', 'label': label});
    }
    if (request.url.path == '/api/sessions') {
      sessionsCreated++;
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      final id = 'session-$sessionsCreated';
      _sessions.add(_FakeSession(
        id: id,
        learnerId: body['learner_id'] as String,
        mode: body['mode'] as String? ?? 'sandbox',
        createdAt: DateTime.now(),
      ));
      return _json({'session_id': id, 'learner_id': body['learner_id'], 'mode': body['mode']});
    }
    // GET /api/learners/{id}/sessions?mode=...
    if (segments.length == 4 && segments[0] == 'api' && segments[1] == 'learners' && segments[3] == 'sessions') {
      final learnerId = segments[2];
      final mode = request.url.queryParameters['mode'] ?? 'sandbox';
      final rows = _sessions.where((s) => s.learnerId == learnerId && s.mode == mode).toList()
        ..sort((a, b) => b.lastActivityAt.compareTo(a.lastActivityAt));
      return _json([for (final s in rows) s.toJson()]);
    }
    // GET /api/sessions/{id}/history
    if (segments.length == 4 && segments[0] == 'api' && segments[1] == 'sessions' && segments[3] == 'history') {
      return _json(historyBySession[segments[2]] ?? const []);
    }
    return http.Response('not found', 404);
  });

  VersaApi get api => VersaApi('http://test', client: client);
}
