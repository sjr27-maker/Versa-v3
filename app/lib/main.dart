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
import 'screens/onboarding_screen.dart';
import 'screens/shell.dart';
import 'theme.dart';

void main() => runApp(const VersaApp());

class VersaApp extends StatefulWidget {
  const VersaApp({super.key, this.api, this.prefs, this.chatFactory, this.billing, this.offerPlans});

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
    )..load();
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
      return const Scaffold(
        backgroundColor: Paper.page,
        body: Center(child: CircularProgressIndicator(color: Paper.accent)),
      );
    }
    final learner = app.learner;
    if (learner == null) return const OnboardingScreen();
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
