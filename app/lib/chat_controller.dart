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
    this.knobDebounce = const Duration(milliseconds: 600),
  }) : _transportFactory = transportFactory ??
            ((sessionId) => WebSocketChatTransport.connect(api.chatUri(sessionId),
                protocols: api.session.socketProtocols));

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

  /// This chat's length / depth / breadth sliders; loaded when a chat is resumed,
  /// moved with [setKnobs].
  SessionKnobs knobs = const SessionKnobs();

  /// How long the sliders must rest before the change is saved and the
  /// latest answer is rewritten.
  final Duration knobDebounce;
  Timer? _knobTimer;

  /// The rewrite currently allowed to change the screen; frames from any
  /// other (superseded) request id are dropped.
  int? _activeRegenId;
  int _nextRegenId = 1;
  ChatMessage? _rewriting;
  String? _textBeforeRewrite;
  bool _rewriteStarted = false;

  bool get rewriting => _activeRegenId != null;

  /// Human-readable reason when [status] is `disconnected`.
  String? problem;

  ChatTransport? _transport;
  StreamSubscription<ServerEvent>? _subscription;
  int _nextId = 0;
  ChatMessage? _current;
  Stopwatch? _turnClock;
  int? _firstOutputMs;
  bool _disposed = false;

  bool get canSend => status == ChatStatus.ready && _activeRegenId == null;

  /// Set by the stage panel while it is showing: turns then ask the server
  /// to act themselves out on it, and the performance arrives on
  /// [stageEvents] (never through [messages] -- it isn't chat content).
  bool stageEnabled = false;
  final _stageEvents = StreamController<ServerEvent>.broadcast();
  Stream<ServerEvent> get stageEvents => _stageEvents.stream;

  /// Lesson chats only: a task was judged complete. Like [stageEvents],
  /// never chat content.
  final _progressEvents = StreamController<ProgressEvent>.broadcast();
  Stream<ProgressEvent> get progressEvents => _progressEvents.stream;

  /// Sparks (src/versa/sparks.py): an answer's charge, a reward, or a
  /// paywall. Not chat content either -- the shell's SparksState keeps the
  /// balance and opens the sheet.
  final _sparkEvents = StreamController<ServerEvent>.broadcast();
  Stream<ServerEvent> get sparkEvents => _sparkEvents.stream;
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
          try {
            knobs = await api.getKnobs(resumeSessionId!);
          } catch (_) {
            // controls are a nicety: a chat still opens with the defaults
          }
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

  /// Move a slider. The value shows at once; once the sliders have rested
  /// for [knobDebounce] the change is saved and the latest answer is
  /// rewritten at the new levels, streaming in place of the old text.
  void setKnobs({int? answerLength, int? depth, int? breadth}) {
    final next = knobs.copyWith(answerLength: answerLength, depth: depth, breadth: breadth);
    if (next == knobs) return;
    knobs = next;
    _notify();
    _knobTimer?.cancel();
    _knobTimer = Timer(knobDebounce, _commitKnobs);
  }

  Future<void> _commitKnobs() async {
    final id = sessionId;
    if (id == null || _disposed) return;
    final wanted = knobs;
    try {
      knobs = await api.patchKnobs(id,
          answerLength: wanted.answerLength, depth: wanted.depth, breadth: wanted.breadth);
    } catch (_) {
      // keep the slider where the person put it; the next move retries
      return;
    }
    // A newer move is already waiting to be saved: let that one rewrite.
    if (_disposed || _knobTimer?.isActive == true) return;
    _notify();
    _requestRewrite();
  }

  /// The answer a rewrite would replace: the last tutor turn, when it is a
  /// finished answer (not a set of options, not an error).
  ChatMessage? get _rewritable {
    if (messages.isEmpty) return null;
    // chatter ("ok" -> "Great.") after an answer doesn't replace it: the
    // answer is still the latest thing a rewrite can change
    var i = messages.length - 1;
    while (i > 0 && messages[i].chatter && messages[i - 1].role == Role.user) {
      i -= 2;
    }
    if (i < 0) return null;
    final last = messages[i];
    if (last.role != Role.tutor || last.hasOptions || last.isError || last.pending) return null;
    if (last.text.isEmpty) return null;
    return last;
  }

  void _requestRewrite() {
    final target = _rewritable;
    final transport = _transport;
    final idle = status == ChatStatus.ready;
    if (target == null || transport == null || !idle) return;
    if (_rewriting != null && _rewriting != target) _endRewrite(restore: true);
    final requestId = _nextRegenId++;
    _activeRegenId = requestId;
    _rewriting = target;
    _textBeforeRewrite ??= target.text;
    _rewriteStarted = false;
    target.rewriting = true;
    // the old directions were pitched for the old window; new ones follow
    // the rewrite
    target.directions = const [];
    transport.regenerate(requestId, directions: directionsStyle);
    _notify();
  }

  void _endRewrite({required bool restore}) {
    final m = _rewriting;
    if (m != null) {
      if (restore && _textBeforeRewrite != null) m.text = _textBeforeRewrite!;
      m.rewriting = false;
    }
    _rewriting = null;
    _textBeforeRewrite = null;
    _activeRegenId = null;
    _rewriteStarted = false;
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
    _parkOpenChoices();
    _invalidateStaleOptions();
    _closeDirections(); // asking their own question passes the strip
    messages.add(ChatMessage(id: _nextId++, role: Role.user, text: trimmed));
    _beginTurn();
    _transport!.sendMessage(trimmed, stage: stageEnabled, directions: directionsStyle);
    _notify();
  }

  /// The student answered the stage's quick check for [turnIndex]: send it
  /// to be kept (right or wrong is evidence of what landed).
  void recordStageCheck({
    required int turnIndex,
    required String question,
    required List<(String, String)> choices,
    required String picked,
    String? answer,
  }) =>
      _transport?.sendStageCheck({
        'turn_index': turnIndex,
        'question': question,
        'choices': [for (final (id, text) in choices) {'id': id, 'text': text}],
        'picked': picked,
        'answer': answer,
      });

  /// How this chat shows "where this could go": 'fork' (links the answer
  /// ends with) or 'strip' (cards below it). Set by the screen from
  /// AppState.directionsStyle.
  String directionsStyle = 'fork';

  /// Take one of the directions under the latest answer. A fork link carries
  /// the same answer on (no bubble of their own, the reply is headed with
  /// the link); a card reads as the learner's own next message (the cards
  /// are written in their voice).
  void pickDirection(ChatMessage message, DirectionCard card) {
    if (!canSend || messages.isEmpty || !identical(messages.last, message)) return;
    if (!message.directions.any((c) => c.id == card.id)) return;
    final fork = directionsStyle == 'fork';
    _closeDirections();
    if (!fork) messages.add(ChatMessage(id: _nextId++, role: Role.user, text: card.text));
    _beginTurn();
    if (fork) _current!.continuationOf = card.text;
    _transport!.pickDirection(card.id, stage: stageEnabled, directions: directionsStyle, continueAnswer: fork);
    _notify();
  }

  /// "Other directions": none of these matched, deal another hand. The
  /// cards stay until the new ones arrive (or the server has no more).
  void moreDirections(ChatMessage message) {
    final setId = message.directionsSetId;
    if (!canSend || setId == null || message.moreDirectionsPending || !identical(messages.last, message)) return;
    message.moreDirectionsPending = true;
    _transport!.moreDirections(setId);
    _notify();
  }

  /// What was still open under the latest reply when they typed: if the
  /// server answers with chatter (an "ok" is not a question), it comes back.
  ChatMessage? _parkedFrom;
  List<DirectionCard> _parkedCards = const [];
  String? _parkedSetId;
  bool _parkedOptionsOpen = false;

  void _parkOpenChoices() {
    _parkedFrom = messages.isNotEmpty && messages.last.role == Role.tutor ? messages.last : null;
    _parkedCards = _parkedFrom?.directions ?? const [];
    _parkedSetId = _parkedFrom?.directionsSetId;
    _parkedOptionsOpen = _parkedFrom != null && _parkedFrom!.hasOptions && _parkedFrom!.optionsOpen;
  }

  void _closeDirections() {
    for (final m in messages) {
      if (m.directions.isNotEmpty) m.directions = const [];
    }
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
    _transport!.selectOption(option.id, stage: stageEnabled, directions: directionsStyle);
    _notify();
  }

  /// After an Undo on a claim-update note succeeds server-side: clear the
  /// note so it doesn't look like it's still offering an undo that already
  /// happened.
  void clearClaimUpdate(ChatMessage message) {
    message.claimUpdate = null;
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
      case StageStart() || StageActionEvent() || StageEnd():
        // Runs alongside the turn (and may outlive it); nothing in the
        // chat itself changes.
        if (!_stageEvents.isClosed) _stageEvents.add(event);
        return;
      case ProgressEvent():
        if (!_progressEvents.isClosed) _progressEvents.add(event);
        return;
      case SparksEvent() || SparksRewardEvent():
        if (!_sparkEvents.isClosed) _sparkEvents.add(event);
        return;
      case PaywallEvent():
        // The turn never ran: close it with a plain note and hand the
        // reason to whoever shows the Sparks sheet.
        if (!_sparkEvents.isClosed) _sparkEvents.add(event);
        final m = _current;
        const note = "You're out of Sparks for now. They refill on their own, "
            'or you can top up.';
        if (m != null) {
          m.pending = false;
          m.streaming = false;
          m.text = note;
        } else {
          messages.add(ChatMessage(id: _nextId++, role: Role.tutor, text: note));
        }
        _finishTurn();
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
      case RecalledEvent():
        // "Oh wait, I remember": whatever options this turn showed are
        // taken back, and the answer is on its way.
        final m = _current;
        if (!_stageEvents.isClosed) _stageEvents.add(event);
        if (m == null) return;
        m.recalled = true;
        if (m.hasOptions) {
          m.options = const [];
          m.optionsOpen = false;
          m.text = '';
          m.pending = true;
          status = ChatStatus.thinking;
        }
      case Done():
        final m = _current;
        if (m == null) return;
        final total = _turnClock?.elapsedMilliseconds ?? event.totalMs;
        m.pending = false;
        m.streaming = false;
        m.text = event.text;
        m.turnIndex = event.turnIndex;
        m.timing = Timing(
          firstOutputMs: _firstOutputMs ?? total,
          totalMs: total,
          serverFirstOutputMs: event.firstOutputMs,
          serverTotalMs: event.totalMs,
        );
        _finishTurn();
      case RegenStart():
        break;
      case RegenDelta(:final requestId, :final text):
        final m = _rewriting;
        if (requestId != _activeRegenId || m == null) return;
        if (!_rewriteStarted) {
          m.text = '';
          _rewriteStarted = true;
        }
        m.text += text;
      case RegenDone(:final requestId, :final text):
        final m = _rewriting;
        if (requestId != _activeRegenId || m == null) return;
        m.text = text;
        _endRewrite(restore: false);
      case RegenEnded(:final requestId):
        if (requestId != _activeRegenId) return;
        _endRewrite(restore: true);
      case DirectionsExhaustedEvent(:final turnIndex):
        if (messages.isEmpty) return;
        final last = messages.last;
        if (last.role != Role.tutor || last.turnIndex != turnIndex) return;
        last.moreDirectionsPending = false;
        last.directionsExhausted = true;
      case DirectionsEvent(:final turnIndex, :final cards, :final setId):
        // Only under the answer it follows, and only while that answer is
        // still the latest thing in the chat.
        if (messages.isEmpty) return;
        final last = messages.last;
        if (last.role != Role.tutor || last.turnIndex != turnIndex || last.hasOptions) return;
        last.directions = cards;
        last.directionsSetId = setId;
        last.moreDirectionsPending = false;
      case AdaptedEvent(:final shaping):
        _current?.adapted = shaping;
      case GuessEvent(:final guess):
        // Heads the reply the taken direction started.
        _current?.guess = guess;
      case ClaimUpdateEvent(:final update):
        // Fired from a background step, any time after `done` -- attach to
        // the last tutor turn regardless of whether `_current` is still
        // live (usually it isn't: the answer already finished).
        for (var i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role == Role.tutor) {
            messages[i].claimUpdate = update;
            break;
          }
        }
      case ChatterEvent(:final text):
        final m = _current;
        if (m == null) return;
        m.pending = false;
        m.streaming = false;
        m.chatter = true;
        m.text = text;
        final from = _parkedFrom;
        if (from != null) {
          // nothing was passed: the cards move down under the reply, still
          // takeable, and the readings they typed past are open again
          m.turnIndex = from.turnIndex;
          m.directions = _parkedCards;
          m.directionsSetId = _parkedSetId;
          if (_parkedOptionsOpen) from.optionsOpen = true;
        }
        _parkedFrom = null;
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
    if (_rewriting != null) _endRewrite(restore: true);
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
    _stageEvents.close();
    _progressEvents.close();
    _sparkEvents.close();
    _knobTimer?.cancel();
    _subscription?.cancel();
    _transport?.close();
    // Best-effort, fire-and-forget: a new chat, a different chat opened, or
    // switching learner all tear down this controller the same way, so this
    // single hook covers every "the user is leaving this chat" case the
    // session-end consolidation flow needs (server.py's `POST .../end`).
    final id = sessionId;
    if (id != null) unawaited(api.endSession(id));
    super.dispose();
  }
}
