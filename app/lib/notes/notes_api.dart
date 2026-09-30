import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../api.dart';
import '../billing/sparks.dart';

/// One topic of a chat's revision notes (src/versa/notes.py).
class NoteTopic {
  const NoteTopic({required this.name, required this.points, this.formulas = const [], this.example});

  final String name;

  /// Key points, each in the app's small formatting (maths as $...$).
  final List<String> points;

  /// Bare LaTeX, no dollar signs.
  final List<String> formulas;
  final String? example;

  factory NoteTopic.fromJson(Map<String, dynamic> j) => NoteTopic(
        name: j['name'] as String,
        points: [for (final p in (j['points'] as List? ?? const [])) p as String],
        formulas: [for (final f in (j['formulas'] as List? ?? const [])) f as String],
        example: j['example'] as String?,
      );
}

/// Revision notes of one chat, as written when the learner asked.
class ChatNotes {
  const ChatNotes({
    required this.id,
    required this.sessionId,
    required this.title,
    required this.topics,
    required this.summary,
    this.stoppedAt,
    required this.answers,
    required this.createdAt,
  });

  final String id;
  final String sessionId;
  final String title;
  final List<NoteTopic> topics;
  final String summary;
  final String? stoppedAt;

  /// How many answers of the chat these notes cover.
  final int answers;
  final DateTime createdAt;

  factory ChatNotes.fromJson(Map<String, dynamic> j) => ChatNotes(
        id: j['id'] as String,
        sessionId: j['session_id'] as String,
        title: j['title'] as String,
        topics: [for (final t in j['topics'] as List) NoteTopic.fromJson(t as Map<String, dynamic>)],
        summary: j['summary'] as String,
        stoppedAt: j['stopped_at'] as String?,
        answers: j['answers'] as int,
        createdAt: DateTime.parse(j['created_at'] as String),
      );

  /// A file name for the PDF: `versa-notes-<title>.pdf`.
  String get fileName {
    final slug = title.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '-').replaceAll(RegExp(r'^-+|-+$'), '');
    final cut = slug.length > 50 ? slug.substring(0, 50) : slug;
    return 'versa-notes-${cut.isEmpty ? 'chat' : cut}.pdf';
  }
}

/// What the Notes sheet opens on.
class NotesStatus {
  const NotesStatus({this.notes, required this.answers, required this.upToDate, required this.cost});

  /// The latest notes of this chat, or null if none were made yet.
  final ChatNotes? notes;

  /// How many answers the chat has now.
  final int answers;

  /// The notes cover every answer so far.
  final bool upToDate;

  /// Sparks that writing notes costs.
  final int cost;

  factory NotesStatus.fromJson(Map<String, dynamic> j) => NotesStatus(
        notes: j['notes'] == null ? null : ChatNotes.fromJson(j['notes'] as Map<String, dynamic>),
        answers: j['answers'] as int,
        upToDate: j['up_to_date'] as bool,
        cost: (j['cost'] as int?) ?? 0,
      );
}

/// The notes endpoints. Notes are only ever written by [generate] -- the
/// learner tapping "Generate notes"; [status] and [pdf] never make any.
class NotesApi {
  NotesApi(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();
  factory NotesApi.of(VersaApi api) => NotesApi(api.baseUrl, client: api.httpClient);

  final String baseUrl;
  final http.Client _http;

  Uri _uri(String path) => Uri.parse('$baseUrl/api$path');

  Future<NotesStatus> status(String sessionId) async {
    final r = await _http.get(_uri('/sessions/$sessionId/notes')).timeout(const Duration(seconds: 20));
    _check(r, 'Could not open the notes');
    return NotesStatus.fromJson(jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>);
  }

  /// Write the notes (or get the latest back, free, if nothing new was
  /// studied). Throws [OutOfSparksException] -- and opens the Sparks sheet --
  /// when the balance is too low.
  Future<ChatNotes> generate(String sessionId) async {
    final r = await _http.post(_uri('/sessions/$sessionId/notes')).timeout(const Duration(seconds: 150));
    _check(r, 'Could not write the notes');
    PaywallHub.changed(); // Sparks may have moved
    return ChatNotes.fromJson(jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>);
  }

  /// The notes drawn as a PDF.
  Future<Uint8List> pdf(ChatNotes notes) async {
    final r = await _http
        .get(_uri('/sessions/${notes.sessionId}/notes/${notes.id}/pdf'))
        .timeout(const Duration(seconds: 60));
    _check(r, 'Could not make the PDF');
    return r.bodyBytes;
  }

  void _check(http.Response r, String fallback) {
    PaywallHub.check(r); // 402: out of Sparks -> the Sparks sheet opens
    if (r.statusCode == 200) return;
    var detail = '$fallback (${r.statusCode})';
    try {
      final d = (jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>)['detail'];
      if (d is String) detail = d;
    } catch (_) {}
    throw ApiException(detail);
  }
}
