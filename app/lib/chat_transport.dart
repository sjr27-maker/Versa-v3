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

  /// "Other directions": nothing in hand [setId] matched; the server deals
  /// the next hand (or says there are none left).
  void moreDirections(String setId);

  /// Rewrite the latest answer at the chat's current slider levels (and,
  /// with [directions], re-offer the directions for the new window).
  void regenerate(int requestId, {String? directions});

  /// The student answered the stage's quick check (server keeps it).
  void sendStageCheck(Map<String, Object?> check);
  Future<void> close();
}

typedef TransportFactory = Future<ChatTransport> Function(String sessionId);

class WebSocketChatTransport implements ChatTransport {
  WebSocketChatTransport._(this._channel);

  final WebSocketChannel _channel;

  /// `protocols`: the session token as a subprotocol (api.dart AuthSession).
  static Future<WebSocketChatTransport> connect(Uri uri, {List<String>? protocols}) async {
    final channel = WebSocketChannel.connect(uri, protocols: protocols);
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
  void moreDirections(String setId) =>
      _channel.sink.add(jsonEncode({'type': 'more_directions', 'set_id': setId}));

  @override
  void sendStageCheck(Map<String, Object?> check) =>
      _channel.sink.add(jsonEncode({'type': 'stage_check', ...check}));

  @override
  void regenerate(int requestId, {String? directions}) => _channel.sink
      .add(jsonEncode({'type': 'regenerate', 'request_id': requestId, 'directions': ?directions}));

  @override
  Future<void> close() async {
    await _channel.sink.close();
  }
}
