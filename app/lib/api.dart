import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// The saved sign-in isn't accepted any more.
class SignedOut extends ApiException {
  SignedOut() : super('Signed out -- sign in again.');
}

/// A new account needs an invite code (the server said so); [message] says
/// what was wrong with the one given, if any.
class InviteRequired implements Exception {
  InviteRequired(this.message);
  final String message;
  @override
  String toString() => message;
}

/// The signed-in session: the Versa session token every request carries
/// (src/versa/accounts.py). Null on a server with sign-in off.
class AuthSession {
  String? token;
  final _expired = StreamController<void>.broadcast();

  /// The server stopped accepting the token (it expired, or was signed out
  /// elsewhere): the app signs out.
  Stream<void> get expired => _expired.stream;

  /// What a WebSocket offers so the server can check the token: a browser
  /// can't put headers on one, so it rides as a subprotocol.
  List<String>? get socketProtocols {
    final t = token;
    return t == null ? null : ['versa', t];
  }
}

/// Adds the session token to every request made through it -- and so to
/// every API in the app, since they all share [VersaApi.httpClient].
class _AuthClient extends http.BaseClient {
  _AuthClient(this._inner, this._session);

  final http.Client _inner;
  final AuthSession _session;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final token = _session.token;
    if (token != null) request.headers['authorization'] = 'Bearer $token';
    final response = await _inner.send(request);
    if (response.statusCode == 401 && token != null && !request.url.path.startsWith('/api/auth/')) {
      _session._expired.add(null);
    }
    return response;
  }

  @override
  void close() => _inner.close();
}

/// Which sign-in the server offers (`/api/health`'s `auth`).
class AuthConfig {
  const AuthConfig({
    required this.required,
    this.firebase = false,
    this.dev = false,
    this.devCode = false,
    this.invites = false,
  });

  /// False: an open server (sign-in off) -- the old name-only sign-in.
  final bool required;
  final bool firebase;
  final bool dev;
  final bool devCode;
  final bool invites;

  static const open = AuthConfig(required: false);

  factory AuthConfig.fromHealth(Map<String, dynamic> health) {
    final a = health['auth'];
    if (a is! Map) return open;
    return AuthConfig(
      required: a['required'] == true,
      firebase: a['firebase'] == true,
      dev: a['dev'] == true,
      devCode: a['dev_code'] == true,
      invites: a['invites'] == true,
    );
  }
}

class SignInResult {
  const SignInResult({required this.token, required this.learner, required this.profileComplete});
  final String token;
  final Learner learner;
  final bool profileComplete;
}

/// The REST half of the Versa API (the chat itself is a WebSocket, see
/// chat_transport.dart).
class VersaApi {
  VersaApi(String baseUrl, {http.Client? client, AuthSession? session})
      : this._(baseUrl, client ?? http.Client(), session ?? AuthSession());

  VersaApi._(this.baseUrl, http.Client inner, this.session) : _http = _AuthClient(inner, session);

  final String baseUrl;
  final AuthSession session;
  final http.Client _http;
  http.Client get httpClient => _http;

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  static const _jsonHeaders = {'content-type': 'application/json'};

  /// `{status: ok, llm: live|stub, auth: {...}}`.
  Future<Map<String, dynamic>> health() async {
    final r = await _http.get(_uri('/api/health')).timeout(const Duration(seconds: 5));
    if (r.statusCode != 200) throw ApiException('server answered ${r.statusCode}');
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  SignInResult _signedIn(http.Response r, String fallback) {
    if (r.statusCode == 403) {
      final detail = _detailObject(r);
      if (detail is Map && detail['reason'] == 'invite') {
        throw InviteRequired('${detail['message'] ?? 'Enter your invite code.'}');
      }
    }
    if (r.statusCode != 200) throw ApiException(_detail(r, fallback));
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    final l = j['learner'] as Map<String, dynamic>;
    return SignInResult(
      token: j['token'] as String,
      learner: Learner(id: l['id'] as String, label: l['label'] as String),
      profileComplete: j['profile_complete'] == true,
    );
  }

  /// Trade a Firebase ID token (Google / email) for a Versa session.
  /// Throws [InviteRequired] for a new account without a working code.
  Future<SignInResult> signInWithFirebase(String idToken, {String? inviteCode}) async {
    final r = await _http
        .post(_uri('/api/auth/firebase'),
            headers: _jsonHeaders,
            body: jsonEncode({
              'id_token': idToken,
              if (inviteCode != null && inviteCode.trim().isNotEmpty) 'invite_code': inviteCode.trim(),
            }))
        .timeout(const Duration(seconds: 20));
    return _signedIn(r, 'could not sign in');
  }

  /// The Versa team's testers: a name (and, on a deployed server, a code).
  Future<SignInResult> signInAsTester(String name, {String? code}) async {
    final r = await _http
        .post(_uri('/api/auth/dev'),
            headers: _jsonHeaders, body: jsonEncode({'name': name, 'code': ?code}))
        .timeout(const Duration(seconds: 10));
    return _signedIn(r, 'could not sign in');
  }

  /// Whether an invite code would let a new account in; null if it would,
  /// else why not.
  Future<String?> checkInvite(String code) async {
    final r = await _http
        .get(_uri('/api/auth/invites/${Uri.encodeComponent(code.trim())}'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not check that code'));
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    return j['valid'] == true ? null : (j['message'] as String? ?? 'That code doesn\'t work.');
  }

  /// Who the saved token belongs to. Throws [ApiException] on 401.
  Future<({Learner learner, bool profileComplete, String? email})> me() async {
    final r = await _http.get(_uri('/api/me')).timeout(const Duration(seconds: 10));
    if (r.statusCode == 401) throw SignedOut();
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not check your sign-in'));
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    final l = j['learner'] as Map<String, dynamic>;
    return (
      learner: Learner(id: l['id'] as String, label: l['label'] as String),
      profileComplete: j['profile_complete'] == true,
      email: j['email'] as String?,
    );
  }

  /// The sign-up profile (profiles.py): null if they haven't filled it in.
  Future<LearnerProfile?> getProfile(String learnerId) async {
    final r = await _http.get(_uri('/api/learners/$learnerId/profile')).timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load your profile'));
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    final p = j['profile'];
    return p is Map<String, dynamic> ? LearnerProfile.fromJson(p) : null;
  }

  /// The "are you sure?" line for an age that doesn't fit, or null.
  Future<String?> checkProfile(String learnerId, Map<String, dynamic> answers) async {
    final r = await _http
        .post(_uri('/api/learners/$learnerId/profile/check'), headers: _jsonHeaders, body: jsonEncode(answers))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'check your answers'));
    return (jsonDecode(r.body) as Map<String, dynamic>)['warning'] as String?;
  }

  Future<LearnerProfile> saveProfile(String learnerId, Map<String, dynamic> answers) async {
    final r = await _http
        .post(_uri('/api/learners/$learnerId/profile'), headers: _jsonHeaders, body: jsonEncode(answers))
        .timeout(const Duration(seconds: 45));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not save your profile'));
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    return LearnerProfile.fromJson(j['profile'] as Map<String, dynamic>);
  }

  /// Get-or-create a learner by name.
  Future<Learner> upsertLearner(String label) async {
    final r = await _http
        .post(_uri('/api/learners'),
            headers: {'content-type': 'application/json'},
            body: jsonEncode({'label': label}))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not sign in'));
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    return Learner(id: j['id'] as String, label: j['label'] as String);
  }

  /// Start a new chat; returns its session id.
  Future<String> createSession(String learnerId) async {
    final r = await _http
        .post(_uri('/api/sessions'),
            headers: {'content-type': 'application/json'},
            body: jsonEncode({
              'learner_id': learnerId,
              'mode': 'sandbox',
            }))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not start a chat'));
    return (jsonDecode(r.body) as Map<String, dynamic>)['session_id'] as String;
  }

  Future<SessionKnobs> getKnobs(String sessionId) async {
    final r = await _http
        .get(_uri('/api/sessions/$sessionId/knobs'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load the chat controls'));
    return SessionKnobs.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  /// Change the length, depth and/or breadth level (0-100).
  Future<SessionKnobs> patchKnobs(String sessionId, {int? answerLength, int? depth, int? breadth}) async {
    final r = await _http
        .patch(_uri('/api/sessions/$sessionId/knobs'),
            headers: {'content-type': 'application/json'},
            body: jsonEncode({
              'answer_length': ?answerLength,
              'depth': ?depth,
              'breadth': ?breadth,
            }))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not change the chat controls'));
    return SessionKnobs.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  /// This learner's chats within one app mode, newest-active first — the
  /// chat-history sidebar.
  Future<List<ChatSummary>> listSessions(String learnerId, {String mode = 'sandbox'}) async {
    final r = await _http
        .get(_uri('/api/learners/$learnerId/sessions').replace(queryParameters: {'mode': mode}))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load chat history'));
    return [
      for (final row in jsonDecode(r.body) as List)
        ChatSummary.fromJson(row as Map<String, dynamic>),
    ];
  }

  /// One chat's turn-by-turn record, to resume it (a page reload, or a past
  /// chat picked from the sidebar).
  Future<List<ChatMessage>> getSessionHistory(String sessionId) async {
    final r = await _http
        .get(_uri('/api/sessions/$sessionId/history'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load this chat'));
    return parseSessionHistory(jsonDecode(r.body) as List);
  }

  /// Every mode's chats together, newest-active first — the History page.
  Future<List<ChatSummary>> listAllSessions(String learnerId) async {
    final r = await _http
        .get(_uri('/api/learners/$learnerId/sessions/all'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load history'));
    return [
      for (final row in jsonDecode(r.body) as List)
        ChatSummary.fromJson(row as Map<String, dynamic>),
    ];
  }

  /// Best-effort: consolidation is a background nicety, never worth
  /// surfacing an error for.
  Future<void> endSession(String sessionId) async {
    try {
      await _http.post(_uri('/api/sessions/$sessionId/end')).timeout(const Duration(seconds: 5));
    } catch (_) {}
  }

  Future<ThinkingStyleOverview> getThinkingStyle(String learnerId, {bool includeArchived = false}) async {
    final r = await _http
        .get(_uri('/api/learners/$learnerId/thinking-style')
            .replace(queryParameters: {'include_archived': '$includeArchived'}))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load thinking style'));
    return ThinkingStyleOverview.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  /// How this learner moves through ideas, from their own choices; empty when
  /// the server has nothing yet (or is older and doesn't know the route).
  Future<List<StylePattern>> getStylePatterns(String learnerId) async {
    final r = await _http.get(_uri('/api/learners/$learnerId/style-patterns')).timeout(const Duration(seconds: 10));
    if (r.statusCode == 404) return const [];
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load how you explore'));
    final body = jsonDecode(r.body) as Map<String, dynamic>;
    return [
      for (final p in (body['patterns'] as List? ?? const [])) StylePattern.fromJson(p as Map<String, dynamic>),
    ];
  }

  Future<ClaimDetail> getClaim(String claimId) async {
    final r = await _http.get(_uri('/api/claims/$claimId')).timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load this claim'));
    return ClaimDetail.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<ThinkingStyleDetail> getThinkingStyleCandidate(String id) async {
    final r = await _http
        .get(_uri('/api/thinking-style/candidates/$id'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load this pattern'));
    return ThinkingStyleDetail.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<ClaimDetail> reviewClaim(String claimId, String action, {String? revisedStatement}) async {
    final r = await _http
        .post(_uri('/api/claims/$claimId/review'),
            headers: {'content-type': 'application/json'},
            body: jsonEncode({'action': action, 'revised_statement': ?revisedStatement}))
        .timeout(const Duration(seconds: 15));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not save that change'));
    return ClaimDetail.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<ThinkingStyleDetail> reviewThinkingStyle(String id, String action, {String? revisedStatement}) async {
    final r = await _http
        .post(_uri('/api/thinking-style/candidates/$id/review'),
            headers: {'content-type': 'application/json'},
            body: jsonEncode({'action': action, 'revised_statement': ?revisedStatement}))
        .timeout(const Duration(seconds: 15));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not save that change'));
    return ThinkingStyleDetail.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<ClaimDetail> undoClaimReview(String claimId, String reviewId) async {
    final r = await _http
        .post(_uri('/api/claims/$claimId/reviews/$reviewId/undo'))
        .timeout(const Duration(seconds: 15));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not undo that'));
    return ClaimDetail.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<ThinkingStyleDetail> undoThinkingStyleReview(String id, String reviewId) async {
    final r = await _http
        .post(_uri('/api/thinking-style/candidates/$id/reviews/$reviewId/undo'))
        .timeout(const Duration(seconds: 15));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not undo that'));
    return ThinkingStyleDetail.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<String> whyClaim(String claimId) async {
    final r = await _http.get(_uri('/api/claims/$claimId/why')).timeout(const Duration(seconds: 30));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not explain this'));
    return (jsonDecode(r.body) as Map<String, dynamic>)['explanation'] as String;
  }

  Future<String> whyThinkingStyle(String id) async {
    final r = await _http
        .get(_uri('/api/thinking-style/candidates/$id/why'))
        .timeout(const Duration(seconds: 30));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not explain this'));
    return (jsonDecode(r.body) as Map<String, dynamic>)['explanation'] as String;
  }

  Future<List<QnAEntry>> listClaimQna(String claimId) async {
    final r = await _http.get(_uri('/api/claims/$claimId/qna')).timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load this conversation'));
    return [for (final row in jsonDecode(r.body) as List) QnAEntry.fromJson(row as Map<String, dynamic>)];
  }

  Future<List<QnAEntry>> listThinkingStyleQna(String id) async {
    final r = await _http
        .get(_uri('/api/thinking-style/candidates/$id/qna'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not load this conversation'));
    return [for (final row in jsonDecode(r.body) as List) QnAEntry.fromJson(row as Map<String, dynamic>)];
  }

  Future<QnAEntry> askClaim(String claimId, String question) async {
    final r = await _http
        .post(_uri('/api/claims/$claimId/qna'),
            headers: {'content-type': 'application/json'}, body: jsonEncode({'question': question}))
        .timeout(const Duration(seconds: 30));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not ask that'));
    return QnAEntry.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  Future<QnAEntry> askThinkingStyle(String id, String question) async {
    final r = await _http
        .post(_uri('/api/thinking-style/candidates/$id/qna'),
            headers: {'content-type': 'application/json'}, body: jsonEncode({'question': question}))
        .timeout(const Duration(seconds: 30));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not ask that'));
    return QnAEntry.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  /// `ws://…/api/sessions/{id}/chat` (or `wss://` behind https).
  Uri chatUri(String sessionId) {
    final u = Uri.parse(baseUrl);
    return u.replace(
      scheme: u.scheme == 'https' ? 'wss' : 'ws',
      path: '/api/sessions/$sessionId/chat',
    );
  }

  String _detail(http.Response r, String fallback) {
    final d = _detailObject(r);
    if (d is String) return d;
    if (d is Map && d['message'] is String) return d['message'] as String;
    if (d is List && d.isNotEmpty && d.first is Map && (d.first as Map)['msg'] is String) {
      // a 422 from validation: the first problem, without pydantic's prefix
      return ((d.first as Map)['msg'] as String).replaceFirst('Value error, ', '');
    }
    return '$fallback (${r.statusCode})';
  }

  Object? _detailObject(http.Response r) {
    try {
      return (jsonDecode(r.body) as Map<String, dynamic>)['detail'];
    } catch (_) {
      return null;
    }
  }

  void close() => _http.close();
}
