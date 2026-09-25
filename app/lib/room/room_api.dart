import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../api.dart';
import 'room_models.dart';

/// The rooms REST endpoints (src/versa/rooms/router.py). Shares the app's
/// HTTP client, and so its test fakes.
class RoomApi {
  RoomApi(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();

  factory RoomApi.of(VersaApi api) => RoomApi(api.baseUrl, client: api.httpClient);

  final String baseUrl;
  final http.Client _http;

  // Creating a room maps its topic with a model call.
  static const _generate = Duration(seconds: 120);
  static const _read = Duration(seconds: 15);
  static const _json = {'content-type': 'application/json'};

  Uri _uri(String path) => Uri.parse('$baseUrl/api$path');

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

  Future<RoomJoined> create({required String code, required String name, String? topic, String? link}) async {
    final r = await _http
        .post(_uri('/rooms'),
            headers: _json,
            body: jsonEncode({'code': code, 'name': name, 'topic': ?topic, 'link': ?link}))
        .timeout(_generate);
    return RoomJoined.fromJson(_decode(r, 'could not create the room') as Map<String, dynamic>);
  }

  Future<RoomJoined> createFromPdf({
    required String code,
    required String name,
    required String filename,
    required Uint8List bytes,
  }) async {
    final request = http.MultipartRequest('POST', _uri('/rooms/from-pdf'))
      ..fields['code'] = code
      ..fields['name'] = name
      ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
    final streamed = await _http.send(request).timeout(_generate);
    final r = await http.Response.fromStream(streamed);
    return RoomJoined.fromJson(_decode(r, 'could not create the room') as Map<String, dynamic>);
  }

  Future<RoomJoined> join({required String code, required String name}) async {
    final r = await _http
        .post(_uri('/rooms/${Uri.encodeComponent(code)}/join'), headers: _json, body: jsonEncode({'name': name}))
        .timeout(_read);
    return RoomJoined.fromJson(_decode(r, 'could not join the room') as Map<String, dynamic>);
  }

  Future<List<RoomSummary>> summaries(List<RoomMembership> memberships) async {
    if (memberships.isEmpty) return const [];
    final r = await _http
        .post(_uri('/rooms/summaries'),
            headers: _json,
            body: jsonEncode({
              'memberships': [
                for (final m in memberships) {'member_id': m.memberId, 'seen_seq': m.seenSeq},
              ],
            }))
        .timeout(_read);
    return [
      for (final row in _decode(r, 'could not load your rooms') as List)
        RoomSummary.fromJson(row as Map<String, dynamic>),
    ];
  }

  Uri socketUri(String code, String memberId) {
    final u = Uri.parse(baseUrl);
    return u.replace(
      scheme: u.scheme == 'https' ? 'wss' : 'ws',
      path: '/api/rooms/${Uri.encodeComponent(code)}/ws',
      queryParameters: {'member_id': memberId},
    );
  }
}

// ------------------------------------------------------------ the socket

/// One open room. An interface so the controller can be tested against a
/// scripted fake with no network.
abstract class RoomTransport {
  /// Server events, in order. Completes (onDone) when the connection drops.
  Stream<RoomEvent> get events;
  void sendMessage(String text);
  void pick(String optionId);
  void typing();
  Future<void> close();
}

typedef RoomTransportFactory = Future<RoomTransport> Function(String code, String memberId);

class WebSocketRoomTransport implements RoomTransport {
  WebSocketRoomTransport._(this._channel);

  final WebSocketChannel _channel;

  static Future<WebSocketRoomTransport> connect(Uri uri) async {
    final channel = WebSocketChannel.connect(uri);
    await channel.ready;
    return WebSocketRoomTransport._(channel);
  }

  @override
  Stream<RoomEvent> get events => _channel.stream
      .map((raw) => parseRoomEvent(jsonDecode(raw as String) as Map<String, dynamic>))
      .where((e) => e != null)
      .cast<RoomEvent>();

  void _send(Map<String, Object?> frame) => _channel.sink.add(jsonEncode(frame));

  @override
  void sendMessage(String text) => _send({'type': 'message', 'text': text});

  @override
  void pick(String optionId) => _send({'type': 'pick', 'option_id': optionId});

  @override
  void typing() => _send({'type': 'typing'});

  @override
  Future<void> close() async => _channel.sink.close();
}

// -------------------------------------------------- rooms on this device

/// A room this person is in, remembered on this device (the server has no
/// accounts: a name + room code is who you are in a room).
class RoomMembership {
  const RoomMembership({required this.code, required this.memberId, required this.name, this.seenSeq = 0});

  final String code;
  final String memberId;
  final String name;

  /// The newest message seq read here (for the unread badge).
  final int seenSeq;

  RoomMembership copyWith({int? seenSeq}) =>
      RoomMembership(code: code, memberId: memberId, name: name, seenSeq: seenSeq ?? this.seenSeq);

  Map<String, Object> toJson() => {'code': code, 'member_id': memberId, 'name': name, 'seen_seq': seenSeq};

  factory RoomMembership.fromJson(Map<String, dynamic> j) => RoomMembership(
        code: j['code'] as String,
        memberId: j['member_id'] as String,
        name: j['name'] as String,
        seenSeq: j['seen_seq'] as int? ?? 0,
      );
}

/// The rooms list for one signed-in learner on this device.
class RoomMemberships {
  RoomMemberships(this._prefs, String learnerId) : _key = 'rooms.$learnerId';

  final SharedPreferences? _prefs;
  final String _key;

  List<RoomMembership> load() {
    final raw = _prefs?.getString(_key);
    if (raw == null) return [];
    try {
      return [
        for (final j in jsonDecode(raw) as List) RoomMembership.fromJson(j as Map<String, dynamic>),
      ];
    } catch (_) {
      return [];
    }
  }

  Future<void> _save(List<RoomMembership> all) async =>
      _prefs?.setString(_key, jsonEncode([for (final m in all) m.toJson()]));

  /// Remember a room (a re-join replaces the old entry for that code).
  Future<void> add(RoomMembership m) async {
    final all = load()..removeWhere((x) => x.code.toLowerCase() == m.code.toLowerCase());
    await _save([m, ...all]);
  }

  Future<void> markSeen(String code, int seq) async {
    final all = load();
    final i = all.indexWhere((x) => x.code.toLowerCase() == code.toLowerCase());
    if (i < 0 || all[i].seenSeq >= seq) return;
    all[i] = all[i].copyWith(seenSeq: seq);
    await _save(all);
  }

  /// Take a room off this device's list (the room and its history stay on
  /// the server; rejoining with the same name and code brings it back).
  Future<void> forget(String code) async {
    await _save(load()..removeWhere((x) => x.code.toLowerCase() == code.toLowerCase()));
  }
}
