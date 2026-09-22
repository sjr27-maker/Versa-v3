import 'dart:async';

import 'package:flutter/foundation.dart';

import 'api.dart';
import 'chat_transport.dart';
import 'models.dart';

enum ChatStatus {
  /// Creating the chat / opening the socket.
  connecting,

  /// Idle: the person can type or click.
  ready,

  /// Sent; waiting for the first word (or the options).
  thinking,

  /// Words are arriving.
  streaming,

  /// The connection is gone; [ChatController.reconnect] tries again.
  disconnected,
}

/// One Sandbox chat: its messages and the live turn in progress.
///
/// What it guarantees (each is covered by test/chat_controller_test.dart):
///  * words appear as they arrive; the server's `done.text` then replaces them
///    (authoritative, e.g. when an answer failed part-way);
///  * an ambiguous message shows the question with clickable readings; a click
///    is a normal turn, and each set of readings can only be used once;
///  * timings are measured HERE, so they include the network (what the person
///    actually waited), next to the server's own numbers;
///  * a dropped connection never leaves a spinner behind, and the same chat
///    (same session, same memory) can be resumed.
class ChatController extends ChangeNotifier {
  ChatController({
    required this.api,
    required this.learner,
    this.resumeSessionId,
    TransportFactory? transportFactory,
  }) : _transportFactory = transportFactory ??
            ((sessionId) => WebSocketChatTransport.connect(api.chatUri(sessionId)));

  final VersaApi api;
  final Learner learner;
  final TransportFactory _transportFactory;

  /// Set to reopen an EXISTING chat (a page reload, or one picked from the
  /// chat-history sidebar) instead of starting a new one: `start()` then
  /// loads its history via `api.getSessionHistory` rather than calling
  /// `api.createSession`.
  final String? resumeSessionId;
  bool _historyLoaded = false;

  final List<ChatMessage> messages = [];
  ChatStatus status = ChatStatus.connecting;
  String? sessionId;

  /// Human-readable reason when [status] is `disconnected`.
  String? problem;

  ChatTransport? _transport;
  StreamSubscription<ServerEvent>? _subscription;
  int _nextId = 0;
  ChatMessage? _current;
  Stopwatch? _turnClock;
  int? _firstOutputMs;
  bool _disposed = false;

  bool get canSend => status == ChatStatus.ready;
  bool get busy => status == ChatStatus.thinking || status == ChatStatus.streaming;

  Future<void> start() async {
    status = ChatStatus.connecting;
    problem = null;
    _notify();
    try {
      if (resumeSessionId != null) {
        sessionId = resumeSessionId;
        if (!_historyLoaded) {
          messages.addAll(await api.getSessionHistory(resumeSessionId!));
          _nextId = messages.isEmpty
              ? 0
              : messages.map((m) => m.id).reduce((a, b) => a > b ? a : b) + 1;
          _historyLoaded = true; // a later reconnect() must not replay it again
        }
      } else {
        sessionId ??= await api.createSession(learner.id);
      }
      await _connect();
    } catch (e) {
      status = ChatStatus.disconnected;
      problem = 'Could not reach the server ($e)';
      _notify();
    }
  }

  /// Reopen the socket for the SAME chat. The old, dead connection is torn down
  /// in the background: closing a half-dead socket can take a long time, and
  /// coming back must never wait on it.
  Future<void> reconnect() async {
    final oldSubscription = _subscription;
    final oldTransport = _transport;
    _subscription = null;
    _transport = null;
    unawaited(_discard(oldSubscription, oldTransport));
    await start();
  }

  Future<void> _discard(StreamSubscription<ServerEvent>? sub, ChatTransport? transport) async {
    try {
      await sub?.cancel();
      await transport?.close();
    } catch (_) {
      // it was already dead; nothing to clean up
    }
  }

  Future<void> _connect() async {
    final transport = await _transportFactory(sessionId!);
    if (_disposed) {
      await transport.close();
      return;
    }
    _transport = transport;
    _subscription = transport.events.listen(
      _onEvent,
      onError: (Object e) => _onDropped('$e'),
      onDone: () => _onDropped(null),
    );
    status = ChatStatus.ready;
    problem = null;
    _notify();
  }

  // ---------------------------------------------------------------- actions

  void send(String text) {
    final trimmed = text.trim();
    if (!canSend || trimmed.isEmpty) return;
    _invalidateStaleOptions();
    messages.add(ChatMessage(id: _nextId++, role: Role.user, text: trimmed));
    _beginTurn();
    _transport!.sendMessage(trimmed);
    _notify();
  }

  /// Typing a fresh message instead of clicking supersedes whatever options
  /// are still open on the SERVER (see disambiguate.py's module docstring,
  /// step 3b) — mirrored locally the instant it happens, not only after the
  /// next round-trip, so the old buttons stop looking clickable right away.
  /// Only the immediately preceding tutor turn can still be open: nothing
  /// can already follow an unresolved options message except a click.
  void _invalidateStaleOptions() {
    if (messages.isEmpty) return;
    final last = messages.last;
    if (last.role == Role.tutor && last.hasOptions && last.optionsOpen) {
      last.optionsOpen = false;
    }
  }

  void pickOption(ChatMessage message, ChatOption option) {
    if (!canSend || !message.optionsOpen || message.optionsResolved) return;
    message.chosenOptionId = option.id;
    message.optionsOpen = false;
    // No echoed "you said" bubble: the chosen reading already shows via
    // the highlighted chip on `message` itself (see MessageView's
    // _OptionChip) -- adding a second bubble here just repeated the
    // option's own question-phrased copy back as if the person had said
    // it themselves.
    _beginTurn();
    _transport!.selectOption(option.id);
    _notify();
  }

  void _beginTurn() {
    _current = ChatMessage(id: _nextId++, role: Role.tutor, pending: true);
    messages.add(_current!);
    status = ChatStatus.thinking;
    _turnClock = Stopwatch()..start();
    _firstOutputMs = null;
  }

  // ----------------------------------------------------------------- events

  void _onEvent(ServerEvent event) {
    switch (event) {
      case TurnStart():
        break;
      case Delta(:final text):
        final m = _current;
        if (m == null) return;
        _markFirstOutput();
        m.pending = false;
        m.streaming = true;
        m.text += text;
        status = ChatStatus.streaming;
      case OptionsEvent(:final message, :final options):
        final m = _current;
        if (m == null) return;
        _markFirstOutput();
        m.pending = false;
        m.streaming = false;
        m.text = message;
        m.options = options;
      case Done():
        final m = _current;
        if (m == null) return;
        final total = _turnClock?.elapsedMilliseconds ?? event.totalMs;
        m.pending = false;
        m.streaming = false;
        m.text = event.text;
        m.timing = Timing(
          firstOutputMs: _firstOutputMs ?? total,
          totalMs: total,
          serverFirstOutputMs: event.firstOutputMs,
          serverTotalMs: event.totalMs,
        );
        _finishTurn();
      case ErrorEvent(:final message):
        final m = _current;
        if (m != null) {
          m.pending = false;
          m.streaming = false;
          m.isError = true;
          m.text = m.text.isEmpty ? message : '${m.text}\n\n$message';
        } else {
          messages.add(ChatMessage(id: _nextId++, role: Role.tutor, text: message, isError: true));
        }
        _finishTurn();
    }
    _notify();
  }

  void _markFirstOutput() {
    _firstOutputMs ??= _turnClock?.elapsedMilliseconds;
  }

  void _finishTurn() {
    _current = null;
    _turnClock?.stop();
    status = ChatStatus.ready;
  }

  void _onDropped(String? reason) {
    if (_disposed) return;
    final m = _current;
    if (m != null) {
      m.pending = false;
      m.streaming = false;
      m.isError = true;
      m.text = m.text.isEmpty
          ? 'The connection was lost before I could answer.'
          : '${m.text}\n\n(connection lost)';
      _current = null;
    }
    status = ChatStatus.disconnected;
    problem = reason == null ? 'The connection to the server was lost.' : 'Connection error ($reason)';
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _subscription?.cancel();
    _transport?.close();
    super.dispose();
  }
}
