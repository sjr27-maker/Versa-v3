import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:versa_app/api.dart';
import 'package:versa_app/chat_transport.dart';
import 'package:versa_app/models.dart';

/// A scripted chat connection: the test decides which frames the "server"
/// sends and when, and can see what the app sent.
class FakeTransport implements ChatTransport {
  final _controller = StreamController<ServerEvent>();
  final List<Map<String, String>> sent = [];
  bool closed = false;

  @override
  Stream<ServerEvent> get events => _controller.stream;

  void emit(ServerEvent event) => _controller.add(event);

  /// The connection drops.
  Future<void> drop() => _controller.close();

  @override
  void sendMessage(String text) => sent.add({'type': 'message', 'text': text});

  @override
  void selectOption(String optionId) => sent.add({'type': 'select_option', 'option_id': optionId});

  @override
  Future<void> close() async {
    closed = true;
    if (!_controller.isClosed) await _controller.close();
  }
}

/// A scripted REST server. Counts session creations so tests can prove a
/// reconnect reuses the same chat.
class FakeBackend {
  FakeBackend({this.up = true, this.llm = 'live'});

  bool up;
  String llm;
  int sessionsCreated = 0;
  final List<String> learnersSeen = [];

  late final http.Client client = MockClient((request) async {
    if (!up) throw http.ClientException('connection refused');
    final path = request.url.path;
    if (path == '/api/health') {
      return http.Response(jsonEncode({'status': 'ok', 'llm': llm}), 200);
    }
    if (path == '/api/learners') {
      final label = (jsonDecode(request.body) as Map)['label'] as String;
      learnersSeen.add(label);
      return http.Response(jsonEncode({'id': 'learner-$label', 'label': label}), 200);
    }
    if (path == '/api/sessions') {
      sessionsCreated++;
      return http.Response(
          jsonEncode({'session_id': 'session-$sessionsCreated', 'learner_id': 'x', 'mode': 'sandbox'}),
          200);
    }
    return http.Response('not found', 404);
  });

  VersaApi get api => VersaApi('http://test', client: client);
}
