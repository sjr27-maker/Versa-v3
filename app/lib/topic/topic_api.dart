import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../api.dart';
import 'topic_models.dart';

/// The Learn-a-topic REST endpoints (src/versa/topics.py). Shares the app's
/// HTTP client, and so its test fakes.
class TopicApi {
  TopicApi(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();

  factory TopicApi.of(VersaApi api) => TopicApi(api.baseUrl, client: api.httpClient);

  final String baseUrl;
  final http.Client _http;

  // Generating branches, lessons and tasks are model calls: give them longer
  // than a plain read.
  static const _generate = Duration(seconds: 120);
  static const _read = Duration(seconds: 15);
  static const _json = {'content-type': 'application/json'};

  Uri _uri(String path) => Uri.parse('$baseUrl/api$path');

  Future<Map<String, dynamic>> _post(String path, Object body, String fail, Duration timeout) async {
    final r = await _http.post(_uri(path), headers: _json, body: jsonEncode(body)).timeout(timeout);
    return _decode(r, fail) as Map<String, dynamic>;
  }

  Future<Object?> _get(String path, String fail) async {
    final r = await _http.get(_uri(path)).timeout(_read);
    return _decode(r, fail);
  }

  Object? _decode(http.Response r, String fail) {
    if (r.statusCode != 200) {
      var detail = '$fail (${r.statusCode})';
      try {
        final d = (jsonDecode(r.body) as Map<String, dynamic>)['detail'];
        if (d is String) detail = d;
      } catch (_) {}
      throw ApiException(detail);
    }
    return jsonDecode(utf8.decode(r.bodyBytes));
  }

  Future<Exploration> exploreSearch(String learnerId, String query) async => Exploration.fromJson(
      await _post('/topic-explorations', {'learner_id': learnerId, 'query': query},
          'could not map that topic', _generate));

  Future<Exploration> exploreLink(String learnerId, String url) async => Exploration.fromJson(
      await _post('/topic-explorations/from-link', {'learner_id': learnerId, 'url': url},
          'could not read that link', _generate));

  Future<Exploration> explorePdf(String learnerId, String filename, Uint8List bytes) async {
    final request = http.MultipartRequest('POST', _uri('/topic-explorations/from-pdf'))
      ..fields['learner_id'] = learnerId
      ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
    final streamed = await _http.send(request).timeout(_generate);
    final r = await http.Response.fromStream(streamed);
    return Exploration.fromJson(_decode(r, 'could not read that PDF') as Map<String, dynamic>);
  }

  Future<Exploration> getExploration(String id) async =>
      Exploration.fromJson(await _get('/topic-explorations/$id', 'could not load this map') as Map<String, dynamic>);

  /// A node's children. Idempotent unless [more], which asks for additional,
  /// distinct branches on a node that already has some.
  Future<List<TopicNode>> expand(String nodeId, {bool more = false}) async {
    final r = await _http
        .post(_uri('/topic-nodes/$nodeId/expand'), headers: _json, body: jsonEncode({'more': more}))
        .timeout(_generate);
    return [
      for (final n in _decode(r, 'could not branch further') as List)
        TopicNode.fromJson(n as Map<String, dynamic>),
    ];
  }

  Future<Topic> createTopic({
    required String learnerId,
    required String explorationId,
    required List<String> selectedNodeIds,
    String? title,
  }) async =>
      Topic.fromJson(await _post(
          '/topics',
          {
            'learner_id': learnerId,
            'exploration_id': explorationId,
            if (title != null && title.trim().isNotEmpty) 'title': title.trim(),
            'selected_node_ids': selectedNodeIds,
          },
          'could not build the course',
          _generate));

  Future<List<TopicSummary>> listTopics(String learnerId) async => [
        for (final row in await _get('/learners/$learnerId/topics', 'could not load your topics') as List)
          TopicSummary.fromJson(row as Map<String, dynamic>),
      ];

  Future<Topic> getTopic(String id) async =>
      Topic.fromJson(await _get('/topics/$id', 'could not load this topic') as Map<String, dynamic>);

  Future<Lesson> getLesson(String id) async =>
      Lesson.fromJson(await _get('/lessons/$id', 'could not load this lesson') as Map<String, dynamic>);

  /// The lesson's chat session (its latest one, or a new one).
  Future<String> startLesson(String id) async =>
      (await _post('/lessons/$id/start', const {}, 'could not start this lesson', _generate))['session_id']
          as String;
}
