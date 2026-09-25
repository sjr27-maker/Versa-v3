import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// The REST half of the Versa API (the chat itself is a WebSocket, see
/// chat_transport.dart).
class VersaApi {
  VersaApi(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();

  final String baseUrl;
  final http.Client _http;
  http.Client get httpClient => _http;

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  /// `{status: ok, llm: live|stub}`.
  Future<Map<String, dynamic>> health() async {
    final r = await _http.get(_uri('/api/health')).timeout(const Duration(seconds: 5));
    if (r.statusCode != 200) throw ApiException('server answered ${r.statusCode}');
    return jsonDecode(r.body) as Map<String, dynamic>;
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

  /// Change the length and/or depth level (0-100).
  Future<SessionKnobs> patchKnobs(String sessionId, {int? answerLength, int? depth}) async {
    final r = await _http
        .patch(_uri('/api/sessions/$sessionId/knobs'),
            headers: {'content-type': 'application/json'},
            body: jsonEncode({
              'answer_length': ?answerLength,
              'depth': ?depth,
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
    try {
      final d = (jsonDecode(r.body) as Map<String, dynamic>)['detail'];
      if (d is String) return d;
    } catch (_) {}
    return '$fallback (${r.statusCode})';
  }

  void close() => _http.close();
}
