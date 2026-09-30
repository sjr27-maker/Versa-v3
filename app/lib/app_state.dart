import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'auth/identity.dart';
import 'billing/billing.dart';
import 'billing/sparks.dart';
import 'chat_controller.dart';
import 'models.dart';
import 'theme.dart';

/// Who is using the app, and the few settings that outlive a restart.
class AppState extends ChangeNotifier {
  AppState({
    required this.api,
    SharedPreferences? prefs,
    Billing? billing,
    this.offerPlans = false,
    this.identity = const NoIdentity(),
  })  // ignore: prefer_initializing_formals -- a private field can't be a named formal
      : _prefs = prefs,
        sparks = SparksState(api: SparksApi.of(api), billing: billing ?? const NoBilling());

  final VersaApi api;

  /// Google / email sign-in (auth/firebase_identity.dart), or none.
  final IdentityService identity;

  /// Sparks and the plan (billing/sparks.dart), following whoever is signed in.
  final SparksState sparks;
  SharedPreferences? _prefs;

  /// This device's saved settings (null until [load]); rooms keep the list of
  /// rooms joined here in it (room/room_api.dart RoomMemberships).
  SharedPreferences? get prefs => _prefs;

  static const _kLabel = 'learner_label';
  static const _kToken = 'session_token';
  static const _kTiming = 'show_timing';
  static const _kStagePanel = 'show_stage_panel';
  static const _kDirections = 'directions_style';
  static const _kTheme = 'theme';
  static const _kProfileSkipped = 'profile_skipped_';
  static String _kPlansSeen(String learnerId) => 'plans_seen_$learnerId';

  /// Show the plans (billing/plans_screen.dart) once to each learner before
  /// the app, right after they first sign in. Off in widget tests.
  final bool offerPlans;

  bool get needsPlans {
    final l = learner;
    if (!offerPlans || l == null || _prefs == null) return false;
    if (sparks.status?.isPaid == true) return false;
    return !(_prefs!.getBool(_kPlansSeen(l.id)) ?? false);
  }

  Future<void> markPlansSeen() async {
    final l = learner;
    if (l == null) return;
    await _prefs?.setBool(_kPlansSeen(l.id), true);
    notifyListeners();
  }

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

  /// How "where this could go" shows under answers (server: directions.py):
  /// 'compass' -- one card per family around the answer, the default on a
  /// first open (2026-10-01) -- 'fork' -- links the answer ends with, a tap
  /// continues the same answer -- or 'strip' -- cards below it, a tap asks as
  /// a new message. This device's choice, kept once made; every set records
  /// which one was actually shown.
  String directionsStyle = 'compass';

  /// The colour theme (theme.dart's PaperPalette ids): this device's choice.
  String get themeId => Paper.palette.id;

  /// Which sign-in the server offers (null until it has answered).
  AuthConfig? authConfig;

  /// Whether the server could be reached at the last try.
  bool serverReachable = true;

  /// 'live' or 'stub', from the server's health check.
  String? llmMode;

  /// Whether the signed-in learner has filled in the sign-up profile
  /// (profiles.py). Always true on an open server, which has no sign-up.
  bool profileComplete = true;

  /// The account's email, when it signed in with one.
  String? email;

  /// How this account signed in: 'dev' (a Versa team tester), 'judge', or
  /// Firebase's method. Null on an open server.
  String? signInMethod;

  /// A tester or judge -- signed in by name -- may skip the sign-up profile.
  bool get isTester => signInMethod == 'dev' || signInMethod == 'judge';

  /// They chose "Skip for now" on this device (kept per account).
  bool _profileSkipped = false;

  StreamSubscription<void>? _expiry;

  /// Asked once after the first sign-in, before the plans and the app.
  bool get needsProfile =>
      learner != null && (authConfig?.required ?? false) && !profileComplete && !(isTester && _profileSkipped);

  Future<void> load() async {
    _prefs ??= await SharedPreferences.getInstance();
    showTiming = _prefs!.getBool(_kTiming) ?? true;
    showStagePanel = _prefs!.getBool(_kStagePanel) ?? false;
    directionsStyle = switch (_prefs!.getString(_kDirections)) {
      'strip' => 'strip',
      'fork' => 'fork',
      _ => 'compass', // nothing chosen yet: the compass
    };
    Paper.palette = PaperPalette.byId(_prefs!.getString(_kTheme));
    _expiry ??= api.session.expired.listen((_) {
      if (learner != null) signOut();
    });
    await _resume();
    loaded = true;
    notifyListeners();
  }

  /// Ask the server again (the sign-in screen's "Check again"): which
  /// sign-in it offers, and whether the saved sign-in still holds.
  Future<void> reconnect() async {
    await _resume();
    notifyListeners();
  }

  Future<void> _resume() async {
    try {
      final health = await api.health();
      authConfig = AuthConfig.fromHealth(health);
      llmMode = health['llm'] as String?;
      serverReachable = true;
    } catch (_) {
      // Server not up yet: stay signed out; the sign-in screen retries.
      serverReachable = false;
      return;
    }
    if (authConfig!.required) {
      final token = _prefs!.getString(_kToken);
      if (token == null || token.isEmpty) return;
      api.session.token = token;
      try {
        final me = await api.me();
        _become(me.learner, profileComplete: me.profileComplete, email: me.email, signInMethod: me.signInMethod);
      } on SignedOut {
        await _forget();
      } catch (_) {
        // reachable a moment ago, not now: try again from the sign-in screen
        api.session.token = null;
      }
      return;
    }
    final label = _prefs!.getString(_kLabel);
    if (label != null && label.isNotEmpty) {
      try {
        // Get-or-create by name: always yields the CURRENT id (e.g. if the
        // dev database was wiped since last time).
        _become(await api.upsertLearner(label), profileComplete: true);
      } catch (_) {
        // Server not up yet: stay signed out; the sign-in screen retries.
      }
    }
  }

  void _become(Learner who, {required bool profileComplete, String? email, String? signInMethod}) {
    learner = who;
    this.profileComplete = profileComplete;
    this.email = email;
    this.signInMethod = signInMethod;
    _profileSkipped = _prefs?.getBool('$_kProfileSkipped${who.id}') ?? false;
    sparks.signedIn(who.id);
  }

  /// An open server (sign-in off): a name is enough.
  Future<void> signIn(String name) async {
    final clean = name.trim();
    _become(await api.upsertLearner(clean), profileComplete: true);
    await _prefs!.setString(_kLabel, learner!.label);
    notifyListeners();
  }

  Future<void> _signedIn(SignInResult result, {String? email}) async {
    api.session.token = result.token;
    await _prefs!.setString(_kToken, result.token);
    _become(result.learner, profileComplete: result.profileComplete, email: email,
        signInMethod: result.signInMethod);
    notifyListeners();
  }

  /// Google, then the server. Throws [InviteRequired] for a new account
  /// without a working code -- the Google sign-in stays, so
  /// [continueWithInvite] can finish it without asking Google again.
  Future<void> signInWithGoogle({String? inviteCode}) async {
    final idToken = await identity.signInWithGoogle();
    await _signedIn(await api.signInWithFirebase(idToken, inviteCode: inviteCode));
  }

  Future<void> signInWithEmail(String email, String password, {required bool create, String? inviteCode}) async {
    final idToken = create
        ? await identity.createAccountWithEmail(email, password)
        : await identity.signInWithEmail(email, password);
    await _signedIn(await api.signInWithFirebase(idToken, inviteCode: inviteCode), email: email.trim());
  }

  /// Finish a sign-in the server held back for an invite code.
  Future<void> continueWithInvite(String inviteCode) async {
    final idToken = await identity.currentIdToken();
    if (idToken == null) throw ApiException('Sign in again, then enter your code.');
    await _signedIn(await api.signInWithFirebase(idToken, inviteCode: inviteCode));
  }

  /// The Versa team's testers, by name.
  Future<void> signInAsTester(String name, {String? code}) async {
    await _signedIn(await api.signInAsTester(name.trim(), code: code));
  }

  /// A judge: any name, plus the shared judge code.
  Future<void> signInAsJudge(String name, String code) async {
    await _signedIn(await api.signInAsJudge(name.trim(), code.trim()));
  }

  /// A tester or judge skips the sign-up profile (on this device, for this
  /// account); it stays reachable from Settings.
  Future<void> skipProfile() async {
    if (!isTester || learner == null) return;
    _profileSkipped = true;
    await _prefs?.setBool('$_kProfileSkipped${learner!.id}', true);
    notifyListeners();
  }

  /// The sign-up profile was saved: on to the plans and the app.
  void profileSaved(LearnerProfile profile) {
    profileComplete = true;
    final name = profile.name;
    if (learner != null && name != null && name.isNotEmpty) {
      learner = Learner(id: learner!.id, label: name);
    }
    notifyListeners();
  }

  Future<void> _forget() async {
    api.session.token = null;
    await _prefs?.remove(_kToken);
  }

  Future<void> signOut() async {
    learner = null;
    email = null;
    await _prefs!.remove(_kLabel);
    await _forget();
    try {
      await identity.signOut();
    } catch (_) {}
    sparks.signedOut();
    notifyListeners();
  }

  @override
  void dispose() {
    _expiry?.cancel();
    super.dispose();
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

  void setThemeId(String id) {
    final next = PaperPalette.byId(id);
    if (next == Paper.palette) return;
    Paper.palette = next;
    _prefs?.setString(_kTheme, next.id);
    notifyListeners();
  }

  void setDirectionsStyle(String value) {
    directionsStyle = value == 'strip' || value == 'fork' ? value : 'compass';
    _prefs?.setString(_kDirections, directionsStyle);
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

  /// Where the learner dragged the handle between the stage and the chat
  /// (widgets/stage_split.dart): the stage's height above the chat on a
  /// phone, and its share of the row beside the chat on a wide screen.
  /// Ephemeral, like the collapsed flags above.
  double stageHeight = 280;
  double stageFraction = 0.5;

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

  /// Tapping the tab you're already on takes it back to its start: Modes,
  /// tapped from inside a mode, returns to the mode picker.
  void goTab(int index) {
    if (index == tab && index == tabModes && inMode) {
      closeModes();
      return;
    }
    tab = index;
    notifyListeners();
  }

  /// The nested navigators of the modes that have their own screen stacks
  /// (topics_root / rooms_root / exams_root), so the system back button
  /// can step back inside them -- a nested Navigator doesn't get it itself.
  final topicsNavigator = GlobalKey<NavigatorState>();
  final roomsNavigator = GlobalKey<NavigatorState>();
  final examsNavigator = GlobalKey<NavigatorState>();

  bool get inMode => inSandbox || inTopics || inRooms || inExams;

  void closeModes() {
    inSandbox = false;
    inTopics = false;
    inRooms = false;
    inExams = false;
    notifyListeners();
  }

  /// The system back button (Android): one screen back inside a mode, then
  /// out to the mode picker, then to Home. False when there's nowhere left
  /// to go, and the app should close.
  Future<bool> back() async {
    if (tab == tabModes) {
      final nav = (inTopics
              ? topicsNavigator
              : inRooms
                  ? roomsNavigator
                  : inExams
                      ? examsNavigator
                      : null)
          ?.currentState;
      if (nav != null && nav.canPop()) {
        await nav.maybePop(); // respects a screen's own PopScope
        return true;
      }
      if (inMode) {
        closeModes();
        return true;
      }
    }
    if (tab != tabHome) {
      goTab(tabHome);
      return true;
    }
    return false;
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
  /// Every chat's Sparks frames go to the shared SparksState; a paywall
  /// frame opens the Sparks sheet (SparksPaywallListener in the shell).
  ChatController _wireSparks(ChatController c) {
    c.sparkEvents.listen((e) {
      switch (e) {
        case SparksEvent(:final balance):
          app.sparks.charged(balance);
        case SparksRewardEvent(:final reason, :final amount):
          app.sparks.rewarded(reason, amount);
        case PaywallEvent(:final detail):
          PaywallHub.request(PaywallRequest.fromDetail(detail));
          app.sparks.refresh();
        default:
          break;
      }
    });
    return c;
  }

  void _wireSandbox(ChatController c) {
    _wireSparks(c);
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
      _wireSparks(_chatFactory(app, resumeSessionId: resumeSessionId));

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
