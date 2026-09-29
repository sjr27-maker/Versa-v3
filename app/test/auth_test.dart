import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:versa_app/app_state.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/main.dart';

import 'support/fakes.dart';

void _size(WidgetTester tester, double w, double h) {
  tester.view.physicalSize = Size(w, h);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

Future<void> _boot(
  WidgetTester tester, {
  required FakeBackend backend,
  FakeIdentity? identity,
  Map<String, Object> prefs = const {},
  bool offerPlans = false,
}) async {
  SharedPreferences.setMockInitialValues(prefs);
  final p = await SharedPreferences.getInstance();
  await tester.pumpWidget(VersaApp(
    api: backend.api,
    prefs: p,
    offerPlans: offerPlans,
    identity: identity ?? FakeIdentity(),
    chatFactory: (AppState app, {resumeSessionId}) => ChatController(
      api: app.api,
      learner: app.learner!,
      resumeSessionId: resumeSessionId,
      transportFactory: (_) async => FakeTransport(),
    ),
  ));
  await tester.pumpAndSettle();
}

Future<void> _tap(WidgetTester tester, String key) async {
  final finder = find.byKey(ValueKey(key));
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<void> _enter(WidgetTester tester, String key, String text) async {
  final finder = find.byKey(ValueKey(key));
  await tester.ensureVisible(finder);
  await tester.enterText(finder, text);
  await tester.pump();
}

Future<void> _pick(WidgetTester tester, String key, String item) async {
  await _tap(tester, key);
  final search = find.byKey(const ValueKey('picker-search'));
  if (search.evaluate().isNotEmpty) {
    await tester.enterText(search, item);
    await tester.pumpAndSettle();
  }
  await tester.tap(find.byKey(ValueKey('pick-$item')));
  await tester.pumpAndSettle();
}

/// Fills in the sign-up questions as a Class 11 CBSE student in Kerala.
Future<void> _fillSchoolProfile(WidgetTester tester, {String age = '16', bool guardian = true}) async {
  await _enter(tester, 'profile-name', 'Asha');
  await _enter(tester, 'profile-age', age);
  await _tap(tester, 'profile-next');
  await _tap(tester, 'occupation-school');
  await _tap(tester, 'profile-next');
  await _pick(tester, 'profile-state', 'Kerala');
  await _pick(tester, 'profile-board', 'CBSE');
  await _pick(tester, 'profile-class', 'Class 11');
  await _tap(tester, 'profile-next');
  await _enter(tester, 'profile-subjects', 'Physics, Maths');
  await _tap(tester, 'consent-data');
  if (guardian) await _tap(tester, 'consent-guardian');
}

void main() {
  testWidgets('a tester signs in by name, answers the sign-up questions once, and lands on Home', (tester) async {
    _size(tester, 1400, 1800);
    final backend = FakeBackend(auth: true);
    await _boot(tester, backend: backend);

    expect(find.byKey(const ValueKey('google-button')), findsOneWidget);
    expect(find.byKey(const ValueKey('name-field')), findsNothing); // no open name sign-in
    await _tap(tester, 'tester-toggle');
    await _enter(tester, 'tester-name', 'Sooraj');
    await _tap(tester, 'tester-submit');

    // straight to the questions; Next won't move without an answer
    expect(find.text('Tell Versa about you'), findsOneWidget);
    await _enter(tester, 'profile-name', 'Sooraj');
    await _enter(tester, 'profile-age', '4');
    await _tap(tester, 'profile-next');
    expect(find.textContaining('aged 5 and up'), findsOneWidget);
    await _enter(tester, 'profile-age', '140');
    await _tap(tester, 'profile-next');
    expect(find.textContaining('real age'), findsOneWidget);

    await tester.pumpWidget(const SizedBox());
    await _boot(tester, backend: backend);
    await _tap(tester, 'tester-toggle');
    await _enter(tester, 'tester-name', 'sooraj');
    await _tap(tester, 'tester-submit');
    await _fillSchoolProfile(tester);
    await _tap(tester, 'profile-save');

    expect(find.text('Hello, Asha'), findsOneWidget);
    final saved = backend.profiles['learner-Sooraj']!;
    expect(saved['level'], 'Class 11');
    expect(saved['curriculum'], 'CBSE');
    expect(saved['region'], 'Kerala');
    expect(saved['consent'], {'data': true, 'guardian': true});
    expect(backend.unauthorized, isEmpty); // every request carried the token

    // the next launch resumes the saved sign-in, no questions
    final token = 'tok-learner-Sooraj';
    await tester.pumpWidget(const SizedBox());
    await _boot(tester, backend: backend, prefs: {'session_token': token});
    expect(find.text('Hello, Asha'), findsOneWidget);
  });

  testWidgets('under 18 needs a guardian; an age that does not fit asks "are you sure?"', (tester) async {
    _size(tester, 1400, 1800);
    final backend = FakeBackend(auth: true);
    await _boot(tester, backend: backend);
    await _tap(tester, 'tester-toggle');
    await _enter(tester, 'tester-name', 'adithya');
    await _tap(tester, 'tester-submit');

    await _fillSchoolProfile(tester, guardian: false);
    await _tap(tester, 'profile-save');
    expect(find.textContaining('parent or guardian needs to agree'), findsOneWidget);
    expect(backend.profiles, isEmpty);

    // back to the start: 45 and at school
    for (var i = 0; i < 3; i++) {
      await _tap(tester, 'profile-back');
    }
    await _enter(tester, 'profile-age', '45');
    for (var i = 0; i < 3; i++) {
      await _tap(tester, 'profile-next');
    }
    expect(find.byKey(const ValueKey('consent-guardian')), findsNothing); // an adult
    await _tap(tester, 'profile-save');
    expect(find.textContaining("You said you're 45 and at school"), findsOneWidget);
    await _tap(tester, 'age-confirm');
    expect(find.text('Hello, Asha'), findsOneWidget);
    expect(backend.profiles['learner-Adithya']!['age'], 45);
  });

  testWidgets('a new Google account is held back for an invite code, then goes on to the plans', (tester) async {
    _size(tester, 1400, 1800);
    final backend = FakeBackend(auth: true);
    await _boot(tester, backend: backend, identity: FakeIdentity(googleUid: 'meera'), offerPlans: true);

    await _tap(tester, 'google-button');
    expect(find.text('One more step'), findsOneWidget);
    await _enter(tester, 'invite-field', 'WRONG123');
    await _tap(tester, 'invite-continue');
    expect(find.textContaining("isn't valid"), findsOneWidget);
    await _enter(tester, 'invite-field', 'goodcode');
    await _tap(tester, 'invite-continue');

    expect(find.text('Tell Versa about you'), findsOneWidget);
    await _fillSchoolProfile(tester);
    await _tap(tester, 'profile-save');
    // Adithya's plans screen comes after the questions
    expect(find.byKey(const ValueKey('plans-continue-free')), findsOneWidget);
    await _tap(tester, 'plans-continue-free');
    expect(find.text('Hello, Asha'), findsOneWidget);
    expect(backend.firebaseSignIns.last['invite_code'], 'goodcode');
  });

  testWidgets('creating an email account checks the invite code first', (tester) async {
    _size(tester, 1400, 1800);
    final backend = FakeBackend(auth: true);
    await _boot(tester, backend: backend);
    await _tap(tester, 'create-toggle');
    await _enter(tester, 'email-field', 'ravi@example.com');
    await _enter(tester, 'password-field', 'secret123');
    await _tap(tester, 'email-submit');
    expect(find.textContaining('invite-only'), findsOneWidget);
    expect(backend.firebaseSignIns, isEmpty); // nothing was created

    await _enter(tester, 'invite-field', 'NOPE0000');
    await _tap(tester, 'email-submit');
    expect(find.textContaining("isn't valid"), findsOneWidget);
    expect(backend.firebaseSignIns, isEmpty);

    await _enter(tester, 'invite-field', 'GOODCODE');
    await _tap(tester, 'email-submit');
    expect(find.text('Tell Versa about you'), findsOneWidget);
  });

  testWidgets('Sign out from Settings returns to sign-in and forgets the token', (tester) async {
    _size(tester, 1400, 1800);
    final backend = FakeBackend(auth: true);
    backend.seedProfile('learner-Sooraj', {'name': 'Sooraj', 'age': 30, 'occupation': 'working', 'country': 'India'});
    final identity = FakeIdentity();
    await _boot(tester, backend: backend, identity: identity);
    await _tap(tester, 'tester-toggle');
    await _enter(tester, 'tester-name', 'Sooraj');
    await _tap(tester, 'tester-submit');
    expect(find.text('Hello, Sooraj'), findsOneWidget);

    await _tap(tester, 'nav-Settings');
    expect(find.byKey(const ValueKey('profile-summary')), findsOneWidget);
    expect(find.text('Sign out'), findsOneWidget);
    await _tap(tester, 'switch-learner');
    expect(find.byKey(const ValueKey('google-button')), findsOneWidget);
    expect(identity.signOuts, 1);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('session_token'), isNull);
  });

  testWidgets('an expired saved sign-in goes back to the sign-in screen', (tester) async {
    _size(tester, 1400, 1800);
    await _boot(tester, backend: FakeBackend(auth: true), prefs: {'session_token': 'tok-stale'});
    expect(find.byKey(const ValueKey('google-button')), findsOneWidget);
  });
}
