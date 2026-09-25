import 'dart:convert';

import 'package:http/http.dart' as http;

import 'api.dart';
import 'models.dart';

/// One suggested topic: tapping it starts a new Sandbox chat with [starter]
/// waiting in the message box.
class FeedItem {
  const FeedItem({
    required this.title,
    required this.hook,
    required this.reason,
    required this.starter,
  });

  final String title;
  final String hook;
  final String reason;
  final String starter;

  factory FeedItem.fromJson(Map<String, dynamic> json) => FeedItem(
        title: json['title'] as String,
        hook: json['hook'] as String,
        reason: json['reason'] as String,
        starter: json['starter'] as String,
      );
}

/// `GET /api/learners/{id}/feed` (src/versa/feed.py).
class HomeFeed {
  const HomeFeed({
    required this.hasHistory,
    required this.generatedAt,
    required this.continueChats,
    required this.related,
    required this.explore,
  });

  final bool hasHistory;
  final DateTime? generatedAt;
  final List<ChatSummary> continueChats;
  final List<FeedItem> related;
  final List<FeedItem> explore;

  factory HomeFeed.fromJson(Map<String, dynamic> json) {
    List<FeedItem> items(String key) => [
          for (final row in json[key] as List) FeedItem.fromJson(row as Map<String, dynamic>),
        ];
    final generated = json['generated_at'] as String?;
    return HomeFeed(
      hasHistory: json['has_history'] as bool,
      generatedAt: generated == null ? null : DateTime.parse(generated),
      continueChats: [
        for (final row in json['continue'] as List) ChatSummary.fromJson(row as Map<String, dynamic>),
      ],
      related: items('related'),
      explore: items('explore'),
    );
  }
}

class FeedApi {
  FeedApi(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();

  /// Shares the app's own HTTP client (and so its test fakes).
  factory FeedApi.of(VersaApi api) => FeedApi(api.baseUrl, client: api.httpClient);

  final String baseUrl;
  final http.Client _http;

  /// The feed is generated at most every 6 hours on the server; [refresh]
  /// asks for a new one now.
  Future<HomeFeed> getFeed(String learnerId, {bool refresh = false}) async {
    final uri = Uri.parse('$baseUrl/api/learners/$learnerId/feed')
        .replace(queryParameters: refresh ? {'refresh': 'true'} : null);
    // Generating a feed is one model call; give it longer than a plain read.
    final r = await _http.get(uri).timeout(const Duration(seconds: 45));
    if (r.statusCode != 200) throw ApiException('could not load your feed (${r.statusCode})');
    return HomeFeed.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }
}
