import 'dart:async';

import 'package:flutter/foundation.dart';

import 'room_api.dart';
import 'room_models.dart';

enum RoomStatus { connecting, live, reconnecting, closed }

/// One open room: the group chat, the board around it, who's typing, and the
/// stage performances (on [stageEvents]).
///
/// The server sends the whole state on every (re)connect, so a dropped
/// connection just reconnects with a growing backoff and picks up where it
/// was; messages are kept by id, so nothing shows twice.
class RoomController extends ChangeNotifier {
  RoomController({
    required this.code,
    required this.memberId,
    required this.connect,
    this.onSeen,
  });

  final String code;
  final String memberId;

  /// Opens the room's socket (a scripted fake in tests).
  final RoomTransportFactory connect;

  /// Called with the newest seq shown (the rooms list's unread badge).
  final void Function(int seq)? onSeen;

  RoomStatus status = RoomStatus.connecting;
  RoomInfo? room;
  RoomMe? me;
  RoomBoard board = RoomBoard.empty;
  String? error;

  final List<RoomMessage> _messages = [];
  final Set<String> _ids = {};
  List<RoomMessage> get messages => List.unmodifiable(_messages);

  /// Option sets clicked here and not yet confirmed by the server.
  final Set<String> pickedSetIds = {};

  /// Who is typing right now, by name ("Versa" included), until when.
  final Map<String, DateTime> _typing = {};
  Timer? _typingSweep;
  DateTime _lastTypingSent = DateTime.fromMillisecondsSinceEpoch(0);

  final _stage = StreamController<RoomEvent>.broadcast();
  Stream<RoomEvent> get stageEvents => _stage.stream;

  RoomTransport? _transport;
  StreamSubscription<RoomEvent>? _sub;
  Timer? _retry;
  int _attempt = 0;
  bool _disposed = false;

  bool get isLive => status == RoomStatus.live;

  List<String> get typingNames {
    final now = DateTime.now();
    return [for (final e in _typing.entries) if (e.value.isAfter(now)) e.key];
  }

  bool get versaTyping => typingNames.contains('Versa');

  int get lastSeq => _messages.isEmpty ? 0 : _messages.last.seq;

  Future<void> start() => _open();

  Future<void> _open() async {
    if (_disposed) return;
    try {
      final t = await connect(code, memberId);
      if (_disposed) {
        await t.close();
        return;
      }
      _transport = t;
      _sub = t.events.listen(_onEvent, onDone: _onDropped, onError: (_) => _onDropped());
    } catch (e) {
      error = 'Can\'t reach the room ($e)';
      _onDropped();
    }
  }

  void _onDropped() {
    _sub?.cancel();
    _sub = null;
    _transport = null;
    if (_disposed) return;
    status = RoomStatus.reconnecting;
    _typing.clear();
    notifyListeners();
    final seconds = [1, 2, 4, 8, 10][_attempt.clamp(0, 4)];
    _attempt++;
    _retry?.cancel();
    _retry = Timer(Duration(seconds: seconds), _open);
  }

  void _onEvent(RoomEvent event) {
    switch (event) {
      case RoomStateEvent(:final room, :final me, :final messages, :final board):
        this.room = room;
        this.me = me;
        this.board = board;
        for (final m in messages) {
          _add(m);
        }
        status = RoomStatus.live;
        error = null;
        _attempt = 0;
        pickedSetIds.clear();
      case RoomMessageEvent(:final message):
        _add(message);
        final who = message.fromVersa ? 'Versa' : message.senderName;
        _typing.remove(who);
      case RoomBoardEvent(:final board):
        this.board = board;
        final open = {for (final s in board.options) s.setId};
        pickedSetIds.removeWhere((id) => !open.contains(id));
      case RoomTypingEvent(:final name, :final on, :final memberId):
        if (memberId == this.memberId) return;
        if (on) {
          // Versa's "typing" lasts until it says it's done; people's fade.
          _typing[name] = DateTime.now().add(Duration(seconds: memberId == null ? 90 : 4));
          _scheduleTypingSweep();
        } else {
          _typing.remove(name);
        }
      case RoomStageStart() || RoomStageAction() || RoomStageEnd():
        _stage.add(event);
        return;
      case RoomErrorEvent(:final message):
        error = message;
        pickedSetIds.clear();
    }
    notifyListeners();
  }

  void _add(RoomMessage m) {
    if (!_ids.add(m.id)) return;
    if (_messages.isEmpty || _messages.last.seq < m.seq) {
      _messages.add(m);
    } else {
      final at = _messages.indexWhere((x) => x.seq > m.seq);
      _messages.insert(at < 0 ? _messages.length : at, m);
    }
    onSeen?.call(lastSeq);
  }

  void _scheduleTypingSweep() {
    _typingSweep?.cancel();
    _typingSweep = Timer(const Duration(seconds: 1), () {
      if (_disposed) return;
      final now = DateTime.now();
      final before = _typing.length;
      _typing.removeWhere((_, until) => !until.isAfter(now));
      if (_typing.length != before) notifyListeners();
      if (_typing.isNotEmpty) _scheduleTypingSweep();
    });
  }

  // ------------------------------------------------------------ actions

  bool send(String text) {
    final clean = text.trim();
    if (clean.isEmpty || _transport == null || !isLive) return false;
    _transport!.sendMessage(clean);
    error = null;
    notifyListeners();
    return true;
  }

  void pick(RoomOptionSet set, RoomOption option) {
    if (_transport == null || !isLive || pickedSetIds.contains(set.setId)) return;
    pickedSetIds.add(set.setId);
    _transport!.pick(option.id);
    notifyListeners();
  }

  /// Tell the others this person is typing (at most every 2.5 s).
  void typing() {
    final now = DateTime.now();
    if (_transport == null || !isLive || now.difference(_lastTypingSent).inMilliseconds < 2500) return;
    _lastTypingSent = now;
    _transport!.typing();
  }

  void clearError() {
    if (error == null) return;
    error = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    status = RoomStatus.closed;
    _retry?.cancel();
    _typingSweep?.cancel();
    _sub?.cancel();
    _transport?.close();
    _stage.close();
    super.dispose();
  }
}
