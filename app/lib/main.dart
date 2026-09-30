import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'app_state.dart';
import 'billing/billing.dart';
import 'billing/plans_screen.dart';
import 'billing/sparks.dart';
import 'billing/sparks_widgets.dart';
import 'config.dart';
import 'auth/firebase_identity.dart';
import 'auth/identity.dart';
import 'screens/profile_screen.dart';
import 'screens/sign_in_screen.dart';
import 'screens/shell.dart';
import 'theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Google / email sign-in, when this build has a Firebase project
  // (auth/firebase_identity.dart); otherwise only the testers' sign-in.
  final identity = await FirebaseIdentity.create();
  runApp(VersaApp(identity: identity));
}

class VersaApp extends StatefulWidget {
  const VersaApp({super.key, this.api, this.prefs, this.chatFactory, this.billing, this.offerPlans, this.identity});

  /// Google / email sign-in (auth/identity.dart). Default: none.
  final IdentityService? identity;

  /// Overrides for tests: a scripted API client / in-memory prefs / fake chat.
  final VersaApi? api;
  final SharedPreferences? prefs;
  final ChatFactory? chatFactory;

  /// Store purchases (billing/billing.dart). Default: RevenueCat on Android
  /// and iOS when the build has a key, otherwise none.
  final Billing? billing;

  /// Show the plans once to a new learner (default: in the real app only).
  final bool? offerPlans;

  @override
  State<VersaApp> createState() => _VersaAppState();
}

class _VersaAppState extends State<VersaApp> {
  late final AppState _app;

  @override
  void initState() {
    super.initState();
    _app = AppState(
      api: widget.api ?? VersaApi(defaultApiBase()),
      prefs: widget.prefs,
      billing: widget.billing ?? (widget.api == null ? RevenueCatBilling.create() : const NoBilling()),
      offerPlans: widget.offerPlans ?? widget.api == null,
      identity: widget.identity ?? const NoIdentity(),
    )..load();
    _app.addListener(_onApp);
  }

  late String _themeId = Paper.palette.id;

  /// A new theme: every screen reads its colours from Paper when it builds,
  /// so rebuild all of them -- including const widgets, which a normal
  /// rebuild would skip.
  void _onApp() {
    if (_app.themeId == _themeId) return;
    void rebuild(Element e) {
      e.markNeedsBuild();
      e.visitChildren(rebuild);
    }

    (context as Element).visitChildren(rebuild);
    setState(() => _themeId = _app.themeId);
  }

  @override
  void dispose() {
    _app.removeListener(_onApp);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AppState>.value(value: _app),
        ChangeNotifierProvider<SparksState>.value(value: _app.sparks),
      ],
      child: MaterialApp(
        title: 'Versa',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(),
        home: _Root(chatFactory: widget.chatFactory),
      ),
    );
  }
}

/// Splash -> sign in -> the app.
class _Root extends StatelessWidget {
  const _Root({this.chatFactory});
  final ChatFactory? chatFactory;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    context.watch<SparksState>(); // a paid plan skips the plans screen
    if (!app.loaded) {
      return Scaffold(
        backgroundColor: Paper.page,
        body: Center(child: CircularProgressIndicator(color: Paper.accent)),
      );
    }
    final learner = app.learner;
    if (learner == null) return const SignInScreen();
    // Sign-in -> the sign-up questions (once) -> the plans (once) -> the app.
    if (app.needsProfile) return ProfileScreen(key: ValueKey('profile-${learner.id}'));
    if (app.needsPlans) {
      return PlansScreen(key: ValueKey('plans-${learner.id}'), name: learner.label, onDone: app.markPlansSeen);
    }
    return ChangeNotifierProvider<ShellState>(
      // A fresh shell (and so a fresh chat) per learner.
      key: ValueKey(learner.id),
      create: (_) => ShellState(app: app, chatFactory: chatFactory),
      child: const SparksPaywallListener(child: Shell()),
    );
  }
}
