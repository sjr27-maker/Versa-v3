import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'models.dart';

/// One open chat. An interface so the controller can be tested against a
/// scripted fake with no network.
abstract class ChatTransport {
  /// Server frames, in order. Completes (onDone) when the connection drops.
  Stream<ServerEvent> get events;

  void sendMessage(String text);
  void selectOption(String optionId);
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
  void sendMessage(String text) =>
      _channel.sink.add(jsonEncode({'type': 'message', 'text': text}));

  @override
  void selectOption(String optionId) =>
      _channel.sink.add(jsonEncode({'type': 'select_option', 'option_id': optionId}));

  @override
  Future<void> close() async {
    await _channel.sink.close();
  }
}
