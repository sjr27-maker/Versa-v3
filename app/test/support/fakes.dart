import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:versa_app/api.dart';
import 'package:versa_app/auth/identity.dart';
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
  void sendMessage(String text, {bool stage = false, String? directions}) =>
      sent.add({'type': 'message', 'text': text, if (stage) 'stage': 'true', 'directions': ?directions});

  @override
  void selectOption(String optionId, {bool stage = false, String? directions}) => sent.add({
        'type': 'select_option',
        'option_id': optionId,
        if (stage) 'stage': 'true',
        'directions': ?directions,
      });

  @override
  void pickDirection(String cardId, {bool stage = false, String? directions, bool continueAnswer = false}) =>
      sent.add({
        'type': 'direction',
        'card_id': cardId,
        if (stage) 'stage': 'true',
        'directions': ?directions,
        if (continueAnswer) 'continue': 'true',
      });

  @override
  void sendStageCheck(Map<String, Object?> check) =>
      sent.add({'type': 'stage_check', for (final e in check.entries) e.key: '${e.value}'});

  @override
  void regenerate(int requestId, {String? directions}) =>
      sent.add({'type': 'regenerate', 'request_id': '$requestId', 'directions': ?directions});

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
  FakeBackend({this.up = true, this.llm = 'live', this.auth = false, this.invites = true});

  bool up;
  String llm;

  /// Sign-in on (src/versa/accounts.py): every route but health and
  /// /api/auth/* wants a token the fake handed out.
  final bool auth;
  final bool invites;
  final Set<String> validInvites = {'GOODCODE'};
  final Map<String, String> _tokens = {}; // token -> learner id
  final Map<String, String> _labels = {}; // learner id -> label
  final Set<String> _firebaseAccounts = {};
  final Map<String, Map<String, dynamic>> profiles = {}; // learner id -> saved body
  final List<String> unauthorized = [];
  final List<Map<String, dynamic>> firebaseSignIns = [];

  /// Pretend this learner already filled in the sign-up questions.
  void seedProfile(String learnerId, Map<String, dynamic> answers) => profiles[learnerId] = answers;

  Map<String, dynamic> _signedIn(String learnerId, String label) {
    final token = 'tok-$learnerId';
    _tokens[token] = learnerId;
    _labels[learnerId] = label;
    return {
      'token': token,
      'learner': {'id': learnerId, 'label': (profiles[learnerId]?['name'] as String?) ?? label},
      'new_account': false,
      'profile_complete': profiles.containsKey(learnerId),
    };
  }

  Map<String, dynamic> _profileOut(Map<String, dynamic> body) => {
        'id': 'profile-1',
        'answers': {...body}..remove('consent'),
        'consent': body['consent'] ?? const {},
        'extracted': {'level': body['level'], 'education_system': body['curriculum'], 'age_fits_stage': true},
        'created_at': DateTime.now().toIso8601String(),
      };
  int sessionsCreated = 0;
  final List<String> learnersSeen = [];
  final List<_FakeSession> _sessions = [];

  /// sessionId -> the raw HistoryTurn-shaped rows `GET .../history` returns.
  /// A test sets this directly; unset -> `[]` (a chat with nothing to replay).
  final Map<String, List<Map<String, dynamic>>> historyBySession = {};

  /// learnerId -> the raw ThinkingStyleOut-shaped body `GET .../thinking-style`
  /// returns. A test sets this directly; unset -> everything empty.
  final Map<String, Map<String, dynamic>> thinkingStyleFor = {};

  /// learnerId -> the pattern rows `GET .../style-patterns` returns (unset = none).
  final Map<String, List<Map<String, dynamic>>> stylePatternsFor = {};

  /// sessionId -> its knobs (unset = defaults); `patchedKnobs` logs every PATCH body.
  final Map<String, Map<String, int>> knobsBySession = {};
  final List<Map<String, dynamic>> patchedKnobs = [];
  bool failKnobPatches = false;

  Map<String, int> _knobs(String id) =>
      knobsBySession.putIfAbsent(id, () => {'answer_length': 50, 'depth': 50, 'breadth': 50});

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
      return _json({
        'status': 'ok',
        'llm': llm,
        'auth': auth
            ? {'required': true, 'firebase': true, 'dev': true, 'dev_code': false, 'invites': invites}
            : {'required': false},
      });
    }
    if (auth) {
      final path = request.url.path;
      if (path == '/api/auth/dev') {
        final name = ((jsonDecode(request.body) as Map)['name'] as String).trim().toLowerCase();
        if (!{'sooraj', 'adithya'}.contains(name)) {
          return _json({'detail': "Name-only sign-in is only for the Versa team's testers."}, 403);
        }
        final label = name[0].toUpperCase() + name.substring(1);
        return _json(_signedIn('learner-$label', label));
      }
      if (path == '/api/auth/firebase') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        firebaseSignIns.add(body);
        final uid = (body['id_token'] as String).replaceFirst('fb:', '');
        if (!_firebaseAccounts.contains(uid)) {
          final code = (body['invite_code'] as String?)?.toUpperCase();
          if (invites && (code == null || !validInvites.contains(code))) {
            return _json({
              'detail': {
                'reason': 'invite',
                'message': code == null
                    ? 'Versa is invite-only for now: enter your invite code.'
                    : "That invite code isn't valid.",
              }
            }, 403);
          }
          _firebaseAccounts.add(uid);
        }
        final label = uid[0].toUpperCase() + uid.substring(1);
        return _json(_signedIn('learner-$uid', label));
      }
      if (path.startsWith('/api/auth/invites/')) {
        final ok = validInvites.contains(request.url.pathSegments.last.toUpperCase());
        return _json({'valid': ok, 'message': ok ? null : "That invite code isn't valid."});
      }
      final header = request.headers['authorization'] ?? '';
      final learnerId = _tokens[header.replaceFirst('Bearer ', '')];
      if (learnerId == null) {
        unauthorized.add(path);
        return _json({'detail': 'sign in first'}, 401);
      }
      if (path == '/api/me') {
        return _json({
          'learner': {'id': learnerId, 'label': (profiles[learnerId]?['name'] as String?) ?? _labels[learnerId]},
          'profile_complete': profiles.containsKey(learnerId),
          'email': null,
        });
      }
      if (segments.length == 4 && segments[1] == 'learners' && segments[3] == 'profile') {
        if (request.method == 'GET') {
          final p = profiles[segments[2]];
          return _json({'complete': p != null, 'profile': p == null ? null : _profileOut(p)});
        }
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        profiles[segments[2]] = body;
        return _json({'profile': _profileOut(body), 'label': body['name']});
      }
      if (segments.length == 5 && segments[3] == 'profile' && segments[4] == 'check') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        final age = body['age'] as int;
        final warning =
            body['occupation'] == 'school' && age > 22 ? "You said you're $age and at school." : null;
        return _json({'ok': true, 'warning': warning});
      }
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
    // GET|PATCH /api/sessions/{id}/knobs
    if (segments.length == 4 && segments[0] == 'api' && segments[1] == 'sessions' && segments[3] == 'knobs') {
      final knobs = _knobs(segments[2]);
      if (request.method == 'PATCH') {
        if (failKnobPatches) return http.Response('boom', 500);
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        patchedKnobs.add(body);
        for (final e in body.entries) {
          knobs[e.key] = (e.value as num).toInt();
        }
      }
      return _json(knobs);
    }
    // GET /api/sessions/{id}/history
    if (segments.length == 4 && segments[0] == 'api' && segments[1] == 'sessions' && segments[3] == 'history') {
      return _json(historyBySession[segments[2]] ?? const []);
    }
    // POST /api/sessions/{id}/end
    if (segments.length == 4 && segments[0] == 'api' && segments[1] == 'sessions' && segments[3] == 'end') {
      return _json({'status': 'too_short'});
    }
    // GET /api/learners/{id}/sessions/all
    if (segments.length == 5 &&
        segments[0] == 'api' &&
        segments[1] == 'learners' &&
        segments[3] == 'sessions' &&
        segments[4] == 'all') {
      final learnerId = segments[2];
      final rows = _sessions.where((s) => s.learnerId == learnerId).toList()
        ..sort((a, b) => b.lastActivityAt.compareTo(a.lastActivityAt));
      return _json([for (final s in rows) s.toJson()]);
    }
    // GET /api/learners/{id}/style-patterns
    if (segments.length == 4 &&
        segments[0] == 'api' &&
        segments[1] == 'learners' &&
        segments[3] == 'style-patterns') {
      return _json({'version': 'style-v1', 'patterns': stylePatternsFor[segments[2]] ?? []});
    }
    // GET /api/learners/{id}/thinking-style
    if (segments.length == 4 &&
        segments[0] == 'api' &&
        segments[1] == 'learners' &&
        segments[3] == 'thinking-style') {
      return _json(thinkingStyleFor[segments[2]] ??
          {'promotion_threshold': 5, 'confirmed': [], 'emerging': [], 'retired': [], 'claims': []});
    }
    return http.Response('not found', 404);
  });

  VersaApi get api => VersaApi('http://test', client: client);
}

/// Google / email sign-in without Firebase: "signs in" as [googleUid].
class FakeIdentity implements IdentityService {
  FakeIdentity({this.googleUid = 'asha'});

  String googleUid;
  String? _current;
  int signOuts = 0;

  @override
  bool get available => true;

  @override
  Future<String?> currentIdToken() async => _current;

  @override
  Future<String> signInWithGoogle() async => _current = 'fb:$googleUid';

  @override
  Future<String> signInWithEmail(String email, String password) async =>
      _current = 'fb:${email.split('@').first}';

  @override
  Future<String> createAccountWithEmail(String email, String password) => signInWithEmail(email, password);

  @override
  Future<void> sendPasswordReset(String email) async {}

  @override
  Future<void> signOut() async {
    _current = null;
    signOuts++;
  }
}
