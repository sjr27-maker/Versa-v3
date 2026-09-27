import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'models.dart';

/// One open chat. An interface so the controller can be tested against a
/// scripted fake with no network.
abstract class ChatTransport {
  /// Server frames, in order. Completes (onDone) when the connection drops.
  Stream<ServerEvent> get events;

  /// [stage]: the stage (the slime) is showing, so the server should act
  /// this turn out on it (server.py's "stage" frames). [directions]: how this
  /// chat shows "where this could go" ('fork' or 'strip'), so the server
  /// offers a set after the answer (a set is only evidence if it was seen).
  void sendMessage(String text, {bool stage = false, String? directions});
  void selectOption(String optionId, {bool stage = false, String? directions});

  /// Take one of the directions under the latest answer. [continueAnswer]:
  /// it was a fork link, so the answer carries on instead of starting over.
  void pickDirection(String cardId, {bool stage = false, String? directions, bool continueAnswer = false});

  /// Rewrite the latest answer at the chat's current slider levels (and,
  /// with [directions], re-offer the directions for the new window).
  void regenerate(int requestId, {String? directions});
  Future<void> close();
}

typedef TransportFactory = Future<ChatTransport> Function(String sessionId);

class WebSocketChatTransport implements ChatTransport {
  WebSocketChatTransport._(this._channel);

  final WebSocketChannel _channel;

  static Future<WebSocketChatTransport> connect(Uri uri) async {
    final channel = WebSocketChannel.connect(uri);
    await channel.ready;
    return WebSocketChatTransport._(channel);
  }

  @override
  Stream<ServerEvent> get events => _channel.stream
      .map((raw) => parseServerEvent(jsonDecode(raw as String) as Map<String, dynamic>))
      .where((e) => e != null)
      .cast<ServerEvent>();

  @override
  void sendMessage(String text, {bool stage = false, String? directions}) => _channel.sink.add(
      jsonEncode({'type': 'message', 'text': text, if (stage) 'stage': true, 'directions': ?directions}));

  @override
  void selectOption(String optionId, {bool stage = false, String? directions}) =>
      _channel.sink.add(jsonEncode({
        'type': 'select_option',
        'option_id': optionId,
        if (stage) 'stage': true,
        'directions': ?directions,
      }));

  @override
  void pickDirection(String cardId, {bool stage = false, String? directions, bool continueAnswer = false}) =>
      _channel.sink.add(jsonEncode({
        'type': 'direction',
        'card_id': cardId,
        if (stage) 'stage': true,
        'directions': ?directions,
        if (continueAnswer) 'continue': true,
      }));

  @override
  void regenerate(int requestId, {String? directions}) => _channel.sink
      .add(jsonEncode({'type': 'regenerate', 'request_id': requestId, 'directions': ?directions}));

  @override
  Future<void> close() async {
    await _channel.sink.close();
  }
}
