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

  /// This device's saved settings (null until [load]); rooms keep the list of
  /// rooms joined here in it (room/room_api.dart RoomMemberships).
  SharedPreferences? get prefs => _prefs;

  static const _kLabel = 'learner_label';
  static const _kTiming = 'show_timing';
  static const _kStagePanel = 'show_stage_panel';

  Learner? learner;
  bool loaded = false;

  /// Under each answer: how long until the first words / the whole answer.
  /// On by default so the speed can be judged while testing by hand.
  bool showTiming = true;

  /// The "Animations" knob (session_knobs, sandbox_screen.dart): whether the
  /// stage panel (widgets/stage_panel.dart) is available at all, in any
  /// mode. Off by default — the design is primarily for topic mode, not
  /// built yet, and stays opt-in for Sandbox until there's something to
  /// actually show there. A global app setting, not per-session, same tier
  /// as showTiming: the animation itself doesn't exist yet, only the
  /// layout it will live in, so there is nothing session-specific to store.
  bool showStagePanel = false;

  Future<void> load() async {
    _prefs ??= await SharedPreferences.getInstance();
    showTiming = _prefs!.getBool(_kTiming) ?? true;
    showStagePanel = _prefs!.getBool(_kStagePanel) ?? false;
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

  void setShowStagePanel(bool value) {
    showStagePanel = value;
    _prefs?.setBool(_kStagePanel, value);
    notifyListeners();
  }
}

typedef ChatFactory = ChatController Function(
  AppState app, {
  String? resumeSessionId,
});

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

  /// Minimized state for the two side panels a chat screen can show — the
  /// chat-history rail and the stage panel (widgets/stage_panel.dart).
  /// Separate from AppState.showStagePanel: that's whether the stage panel
  /// exists at all (the "Animations" knob); this is just whether it's
  /// currently taking up space in THIS view. Ephemeral (not persisted) —
  /// unlike the knob, minimizing is a per-glance convenience, not a
  /// setting worth remembering across restarts.
  bool historyRailCollapsed = false;
  bool stagePanelCollapsed = false;

  /// The app's own main navigation rail (shell.dart) and, inside Sandbox,
  /// the session-knobs rail — same ephemeral, un-persisted minimize
  /// pattern as the two above, added so the stage panel can genuinely get
  /// equal room with the chat instead of competing with app chrome.
  bool navRailCollapsed = false;
  bool knobsRailCollapsed = false;

  void toggleHistoryRailCollapsed() {
    historyRailCollapsed = !historyRailCollapsed;
    notifyListeners();
  }

  void toggleStagePanelCollapsed() {
    stagePanelCollapsed = !stagePanelCollapsed;
    notifyListeners();
  }

  void toggleNavRailCollapsed() {
    navRailCollapsed = !navRailCollapsed;
    notifyListeners();
  }

  void toggleKnobsRailCollapsed() {
    knobsRailCollapsed = !knobsRailCollapsed;
    notifyListeners();
  }

  void goTab(int index) {
    tab = index;
    notifyListeners();
  }

  void openSandbox() {
    tab = tabModes;
    inSandbox = true;
    inTopics = false;
    inRooms = false;
    inExams = false;
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

  /// Reopen a past Sandbox chat picked from the sidebar, or from the
  /// History page (which isn't already showing Sandbox, unlike the
  /// sidebar's own callers) -- so this always navigates there too.
  void openSandboxChat(ChatSummary chat) {
    tab = tabModes;
    inSandbox = true;
    if (sandbox?.sessionId == chat.sessionId) {
      notifyListeners();
      return; // already the open one
    }
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

  // ------------------------------------------------------- Learn a topic

  /// The Learn-a-topic screens are showing in the Modes tab (their own
  /// nested navigation: topics home -> explorer / topic -> path / lesson).
  bool inTopics = false;

  /// A lesson asked for from outside the topic screens (a History row);
  /// the topic screens open it and clear it via [takePendingLesson].
  String? pendingLessonId;

  void openTopics() {
    tab = tabModes;
    inSandbox = false;
    inTopics = true;
    inRooms = false;
    inExams = false;
    notifyListeners();
  }

  void closeTopics() {
    inTopics = false;
    notifyListeners();
  }

  void openLesson(String lessonId) {
    pendingLessonId = lessonId;
    openTopics();
  }

  String? takePendingLesson() {
    final id = pendingLessonId;
    pendingLessonId = null;
    return id;
  }

  // ---------------------------------------------- Study with others

  /// The rooms screens (room/, experimental) are showing in the Modes tab.
  bool inRooms = false;

  void openRooms() {
    tab = tabModes;
    inSandbox = false;
    inTopics = false;
    inRooms = true;
    inExams = false;
    notifyListeners();
  }

  void closeRooms() {
    inRooms = false;
    notifyListeners();
  }

  // ------------------------------------------------ Exam preparation

  /// The exam screens (exam/) are showing in the Modes tab.
  bool inExams = false;

  void openExams() {
    tab = tabModes;
    inSandbox = false;
    inTopics = false;
    inRooms = false;
    inExams = true;
    notifyListeners();
  }

  void closeExams() {
    inExams = false;
    notifyListeners();
  }

  /// A chat controller built the same way the Sandbox one is (so tests'
  /// scripted connections apply to lesson chats too). The caller owns it.
  ChatController makeChat({String? resumeSessionId}) =>
      _chatFactory(app, resumeSessionId: resumeSessionId);

  /// Forget the live chat (e.g. after switching learner).
  void reset() {
    sandbox?.dispose();
    sandbox = null;
    inSandbox = false;
    inTopics = false;
    inRooms = false;
    inExams = false;
    pendingLessonId = null;
    tab = tabHome;
    sandboxHistory.clear();
    historyRailCollapsed = false;
    stagePanelCollapsed = false;
    knobsRailCollapsed = false;
    notifyListeners();
  }

  @override
  void dispose() {
    sandbox?.dispose();
    super.dispose();
  }
}
