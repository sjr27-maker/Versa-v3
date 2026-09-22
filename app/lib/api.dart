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
            body: jsonEncode({'learner_id': learnerId, 'mode': 'sandbox'}))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) throw ApiException(_detail(r, 'could not start a chat'));
    return (jsonDecode(r.body) as Map<String, dynamic>)['session_id'] as String;
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
