import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'chat_controller.dart';
import 'models.dart';

/// Who is using the app, and the few settings that outlive a restart.
class AppState extends ChangeNotifier {
  // ignore: prefer_initializing_formals -- a private field can't be a named formal
  AppState({required this.api, SharedPreferences? prefs}) : _prefs = prefs;

  final VersaApi api;
  SharedPreferences? _prefs;

  static const _kLabel = 'learner_label';
  static const _kTiming = 'show_timing';

  Learner? learner;
  bool loaded = false;

  /// Under each answer: how long until the first words / the whole answer.
  /// On by default so the speed can be judged while testing by hand.
  bool showTiming = true;

  Future<void> load() async {
    _prefs ??= await SharedPreferences.getInstance();
    showTiming = _prefs!.getBool(_kTiming) ?? true;
    final label = _prefs!.getString(_kLabel);
    if (label != null && label.isNotEmpty) {
      try {
        // Get-or-create by name: always yields the CURRENT id (e.g. if the
        // dev database was wiped since last time).
        learner = await api.upsertLearner(label);
      } catch (_) {
        // Server not up yet: stay signed out; the sign-in screen retries.
      }
    }
    loaded = true;
    notifyListeners();
  }

  Future<void> signIn(String name) async {
    final clean = name.trim();
    learner = await api.upsertLearner(clean);
    await _prefs!.setString(_kLabel, learner!.label);
    notifyListeners();
  }

  Future<void> signOut() async {
    learner = null;
    await _prefs!.remove(_kLabel);
    notifyListeners();
  }

  void setShowTiming(bool value) {
    showTiming = value;
    _prefs?.setBool(_kTiming, value);
    notifyListeners();
  }
}

typedef ChatFactory = ChatController Function(AppState app, {String? resumeSessionId});

/// Where the person is in the app, and the one live Sandbox chat, which
/// survives switching tabs (so browsing Settings doesn't lose the
/// conversation).
class ShellState extends ChangeNotifier {
  ShellState({required this.app, ChatFactory? chatFactory})
      : _chatFactory = chatFactory ??
            ((app, {resumeSessionId}) => ChatController(
                  api: app.api,
                  learner: app.learner!,
                  resumeSessionId: resumeSessionId,
                ));

  final AppState app;
  final ChatFactory _chatFactory;

  static const tabHome = 0;
  static const tabModes = 1;
  static const tabHistory = 2;
  static const tabThinking = 3;
  static const tabSettings = 4;

  int tab = tabHome;
  ChatController? sandbox;

  /// The Sandbox chat is showing (as opposed to the mode picker).
  bool inSandbox = false;

  /// This learner's Sandbox chats (the chat-history sidebar) — one mode's
  /// own list, never mixed with another mode's. Kept on `ShellState`, not
  /// on `ChatController`, since it outlives any one chat.
  final List<ChatSummary> sandboxHistory = [];
  bool sandboxHistoryLoading = false;

  void goTab(int index) {
    tab = index;
    notifyListeners();
  }

  void openSandbox() {
    tab = tabModes;
    inSandbox = true;
    if (sandbox == null) {
      sandbox = _chatFactory(app)..start();
      _wireSandbox(sandbox!);
    }
    refreshSandboxHistory();
    notifyListeners();
  }

  void newSandboxChat() {
    sandbox?.dispose();
    sandbox = _chatFactory(app)..start();
    _wireSandbox(sandbox!);
    refreshSandboxHistory();
    notifyListeners();
  }

  /// Reopen a past Sandbox chat picked from the sidebar.
  void openSandboxChat(ChatSummary chat) {
    if (sandbox?.sessionId == chat.sessionId) return; // already the open one
    sandbox?.dispose();
    sandbox = _chatFactory(app, resumeSessionId: chat.sessionId)..start();
    _wireSandbox(sandbox!);
    refreshSandboxHistory(); // keeps the highlighted row in step with the switch
    notifyListeners();
  }

  /// Refetches the sidebar list. Best-effort: a failure (e.g. a hiccup mid-
  /// turn) leaves the list as it was rather than surfacing a second error
  /// on top of whatever the chat itself is already showing.
  Future<void> refreshSandboxHistory() async {
    sandboxHistoryLoading = true;
    notifyListeners();
    try {
      final rows = await app.api.listSessions(app.learner!.id, mode: 'sandbox');
      sandboxHistory
        ..clear()
        ..addAll(rows);
    } catch (_) {
      // leave sandboxHistory as it was
    }
    sandboxHistoryLoading = false;
    notifyListeners();
  }

  /// Keeps the sidebar's preview/recency in step with the active chat: a
  /// refetch each time a turn finishes (busy -> ready), not on every delta.
  void _wireSandbox(ChatController c) {
    var previous = c.status;
    c.addListener(() {
      final wasBusy = previous == ChatStatus.thinking || previous == ChatStatus.streaming;
      if (wasBusy && c.status == ChatStatus.ready) refreshSandboxHistory();
      previous = c.status;
    });
  }

  void closeSandbox() {
    inSandbox = false;
    notifyListeners();
  }

  /// Forget the live chat (e.g. after switching learner).
  void reset() {
    sandbox?.dispose();
    sandbox = null;
    inSandbox = false;
    tab = tabHome;
    sandboxHistory.clear();
    notifyListeners();
  }

  @override
  void dispose() {
    sandbox?.dispose();
    super.dispose();
  }
}
