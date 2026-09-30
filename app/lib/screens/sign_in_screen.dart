import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api.dart';
import '../app_state.dart';
import '../auth/auth_widgets.dart';
import '../auth/identity.dart';
import '../theme.dart';

/// First launch, and after signing out.
///
/// With sign-in on (src/versa/accounts.py): Google, or email and password,
/// and -- for a brand-new account -- an invite code, since Versa is
/// invite-only. The Versa team's two testers can sign in by name. On an open
/// server (sign-in off, a laptop) it is the old "what should I call you?".
/// Also the place that says plainly when the server can't be reached.
class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key});

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _invite = TextEditingController();
  final _testerName = TextEditingController();
  final _testerCode = TextEditingController();
  final _judgeName = TextEditingController();
  final _judgeCode = TextEditingController();

  bool _busy = false;
  bool _creating = false;
  bool _showInvite = false;

  /// The server held a Google / email sign-in back for an invite code: the
  /// account is signed in on this device, the code finishes it.
  bool _awaitingInvite = false;
  bool _showTester = false;
  bool _showJudge = false;
  bool _hidePassword = true;
  String? _error;
  String? _notice;

  @override
  void dispose() {
    for (final c in [_name, _email, _password, _invite, _testerName, _testerCode, _judgeName, _judgeCode]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
      _notice = null;
    });
    try {
      await action();
    } on InviteRequired catch (e) {
      if (mounted) {
        setState(() {
          _awaitingInvite = true;
          _showInvite = true;
          _error = e.message;
        });
      }
    } on IdentityException catch (e) {
      if (mounted && !e.cancelled) setState(() => _error = e.message);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not sign in: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String? get _inviteCode => _invite.text.trim().isEmpty ? null : _invite.text.trim();

  Future<void> _google() => _run(() => context.read<AppState>().signInWithGoogle(inviteCode: _inviteCode));

  Future<void> _emailSubmit(AuthConfig config) => _run(() async {
        final app = context.read<AppState>();
        final email = _email.text.trim();
        if (!email.contains('@') || _password.text.isEmpty) {
          throw ApiException('Enter your email and password.');
        }
        if (_creating && config.invites) {
          // check the code BEFORE making the account, so a typo doesn't
          // leave an account that can't get in
          // (ApiException, not InviteRequired: nothing is signed in yet, so
          // the form stays -- its invite field is right there)
          final code = _inviteCode;
          if (code == null) throw ApiException('Versa is invite-only for now: enter your invite code.');
          final problem = await app.api.checkInvite(code);
          if (problem != null) throw ApiException(problem);
        }
        await app.signInWithEmail(email, _password.text, create: _creating, inviteCode: _inviteCode);
      });

  Future<void> _continueWithInvite() => _run(() async {
        final code = _inviteCode;
        if (code == null) throw InviteRequired('Enter your invite code.');
        await context.read<AppState>().continueWithInvite(code);
      });

  Future<void> _resetPassword() => _run(() async {
        final email = _email.text.trim();
        if (!email.contains('@')) throw ApiException('Type your email above first.');
        await context.read<AppState>().identity.sendPasswordReset(email);
        if (mounted) setState(() => _notice = 'Check $email for a link to set a new password.');
      });

  Future<void> _tester(AuthConfig config) => _run(() async {
        if (_testerName.text.trim().isEmpty) throw ApiException('Enter your name.');
        await context.read<AppState>().signInAsTester(
              _testerName.text,
              code: config.devCode ? _testerCode.text.trim() : null,
            );
      });

  Future<void> _judge() => _run(() async {
        if (_judgeName.text.trim().length < 2) throw ApiException('Enter your name.');
        if (_judgeCode.text.trim().isEmpty) throw ApiException('Enter the judge code.');
        await context.read<AppState>().signInAsJudge(_judgeName.text, _judgeCode.text);
      });

  Future<void> _openSignIn() => _run(() async {
        final name = _name.text.trim();
        if (name.isEmpty) return;
        await context.read<AppState>().signIn(name);
      });

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final config = app.authConfig;
    return AuthScaffold(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(18),
            child: Image.asset('assets/brand/versa-logo.png', width: 72, height: 72),
          ),
          const SizedBox(height: 14),
          Text('Versa', style: serif(44)),
          const SizedBox(height: 6),
          Text('Learn, how you think.', style: sans(15, color: Paper.muted, height: 1.5)),
          const SizedBox(height: 28),
          if (!app.serverReachable || config == null)
            const SizedBox.shrink()
          else if (!config.required)
            ..._openServerForm()
          else
            ..._signedServerForm(app, config),
          if (_notice != null) FormMessage(_notice!, error: false),
          if (_error != null) FormMessage(_error!),
          const SizedBox(height: 22),
          _ServerStatus(app: app),
        ],
      ),
    );
  }

  List<Widget> _openServerForm() => [
        Text('What should I call you?', style: sans(13, weight: FontWeight.w600)),
        const SizedBox(height: 8),
        TextField(
          key: const ValueKey('name-field'),
          controller: _name,
          autofocus: true,
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => _openSignIn(),
          style: sans(15),
          decoration: paperInput('Your name'),
        ),
        const SizedBox(height: 6),
        Text('Same name next time = same memory of you. This server has sign-in off.',
            style: sans(12, color: Paper.faint)),
        const SizedBox(height: 18),
        PrimaryButton(
          key: const ValueKey('continue-button'),
          label: _busy ? 'Signing in…' : 'Continue',
          onPressed: _openSignIn,
        ),
      ];

  List<Widget> _signedServerForm(AppState app, AuthConfig config) {
    final canFirebase = config.firebase && app.identity.available;
    return [
      if (_awaitingInvite) ..._inviteStep() else ...[
        if (canFirebase) ...[
          SecondaryButton(
            key: const ValueKey('google-button'),
            label: 'Continue with Google',
            icon: const GoogleMark(),
            onPressed: _busy ? null : _google,
          ),
          const OrDivider(),
          TextField(
            key: const ValueKey('email-field'),
            controller: _email,
            keyboardType: TextInputType.emailAddress,
            autofillHints: const [AutofillHints.email],
            textInputAction: TextInputAction.next,
            style: sans(15),
            decoration: paperInput('you@example.com', label: 'Email'),
          ),
          const SizedBox(height: 10),
          TextField(
            key: const ValueKey('password-field'),
            controller: _password,
            obscureText: _hidePassword,
            autofillHints: [_creating ? AutofillHints.newPassword : AutofillHints.password],
            textInputAction: TextInputAction.done,
            onSubmitted: (_) => _emailSubmit(config),
            style: sans(15),
            decoration: paperInput(
              _creating ? 'At least 6 characters' : 'Your password',
              label: 'Password',
              suffix: IconButton(
                tooltip: _hidePassword ? 'Show password' : 'Hide password',
                icon: Icon(_hidePassword ? Icons.visibility_outlined : Icons.visibility_off_outlined,
                    color: Paper.muted, size: 20),
                onPressed: () => setState(() => _hidePassword = !_hidePassword),
              ),
            ),
          ),
          if (_creating && config.invites) ...[
            const SizedBox(height: 10),
            _inviteField(),
          ],
          const SizedBox(height: 14),
          PrimaryButton(
            key: const ValueKey('email-submit'),
            label: _creating ? 'Create account' : 'Sign in',
            busy: _busy,
            onPressed: () => _emailSubmit(config),
          ),
          const SizedBox(height: 6),
          Wrap(
            alignment: WrapAlignment.spaceBetween,
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: 8,
            children: [
              TextButton(
                key: const ValueKey('create-toggle'),
                onPressed: _busy ? null : () => setState(() => _creating = !_creating),
                child: Text(_creating ? 'Have an account? Sign in' : 'New to Versa? Create an account'),
              ),
              if (!_creating)
                TextButton(onPressed: _busy ? null : _resetPassword, child: const Text('Forgot password?')),
            ],
          ),
          if (config.invites && !_creating) ...[
            if (_showInvite) ...[
              const SizedBox(height: 4),
              _inviteField(helper: 'New here? Enter your code, then continue with Google.'),
            ] else
              TextButton(
                key: const ValueKey('invite-toggle'),
                onPressed: () => setState(() => _showInvite = true),
                child: const Text('Have an invite code?'),
              ),
          ],
        ] else if (config.firebase)
          FormMessage(
            'Google and email sign-in work in the Versa app on your phone and in a web browser.',
            error: false,
          ),
        if (config.judge) _judgeSection(),
        if (config.dev) _testerSection(config),
      ],
    ];
  }

  Widget _inviteField({String? helper}) => TextField(
        key: const ValueKey('invite-field'),
        controller: _invite,
        textCapitalization: TextCapitalization.characters,
        style: sans(15, weight: FontWeight.w600).copyWith(letterSpacing: 2),
        decoration: paperInput('ABCD2345', label: 'Invite code', helper: helper),
      );

  List<Widget> _inviteStep() => [
        Text('One more step', style: serif(22)),
        const SizedBox(height: 6),
        Text('Versa is invite-only for now. Enter the code from your invite link to finish creating your account.',
            style: sans(14, color: Paper.muted, height: 1.45)),
        const SizedBox(height: 16),
        _inviteField(),
        const SizedBox(height: 14),
        PrimaryButton(
          key: const ValueKey('invite-continue'),
          label: 'Continue',
          busy: _busy,
          onPressed: _continueWithInvite,
        ),
        TextButton(
          onPressed: _busy
              ? null
              : () async {
                  await context.read<AppState>().signOut();
                  if (mounted) {
                    setState(() {
                      _awaitingInvite = false;
                      _error = null;
                    });
                  }
                },
          child: const Text('Use a different account'),
        ),
      ];

  /// Judges: any name, plus the one code they were given.
  Widget _judgeSection() => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextButton(
              key: const ValueKey('judge-toggle'),
              onPressed: () => setState(() => _showJudge = !_showJudge),
              child: Text(_showJudge ? 'Hide judge sign-in' : 'Judging Versa?'),
            ),
            if (_showJudge) ...[
              TextField(
                key: const ValueKey('judge-name'),
                controller: _judgeName,
                textInputAction: TextInputAction.next,
                style: sans(15),
                decoration: paperInput('Your name', label: 'Your name'),
              ),
              const SizedBox(height: 10),
              TextField(
                key: const ValueKey('judge-code'),
                controller: _judgeCode,
                obscureText: true,
                onSubmitted: (_) => _judge(),
                style: sans(15),
                decoration: paperInput('The code you were given', label: 'Judge code'),
              ),
              const SizedBox(height: 12),
              SecondaryButton(
                key: const ValueKey('judge-submit'),
                label: 'Sign in as judge',
                onPressed: _busy ? null : _judge,
              ),
            ],
          ],
        ),
      );

  Widget _testerSection(AuthConfig config) => Padding(
        padding: const EdgeInsets.only(top: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextButton(
              key: const ValueKey('tester-toggle'),
              onPressed: () => setState(() => _showTester = !_showTester),
              child: Text(_showTester ? 'Hide tester sign-in' : 'Versa team tester?'),
            ),
            if (_showTester) ...[
              TextField(
                key: const ValueKey('tester-name'),
                controller: _testerName,
                textInputAction: config.devCode ? TextInputAction.next : TextInputAction.done,
                onSubmitted: config.devCode ? null : (_) => _tester(config),
                style: sans(15),
                decoration: paperInput('Your name', label: 'Tester name'),
              ),
              if (config.devCode) ...[
                const SizedBox(height: 10),
                TextField(
                  key: const ValueKey('tester-code'),
                  controller: _testerCode,
                  obscureText: true,
                  onSubmitted: (_) => _tester(config),
                  style: sans(15),
                  decoration: paperInput('Tester code', label: 'Tester code'),
                ),
              ],
              const SizedBox(height: 12),
              SecondaryButton(
                key: const ValueKey('tester-submit'),
                label: 'Sign in as tester',
                onPressed: _busy ? null : () => _tester(config),
              ),
            ],
          ],
        ),
      );
}

class _ServerStatus extends StatefulWidget {
  const _ServerStatus({required this.app});
  final AppState app;

  @override
  State<_ServerStatus> createState() => _ServerStatusState();
}

class _ServerStatusState extends State<_ServerStatus> {
  bool _checking = false;

  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    if (!app.serverReachable) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Can\'t reach the server at ${app.api.baseUrl}',
              key: const ValueKey('server-down'),
              style: sans(12.5, color: Paper.danger, weight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text('Check your connection. Running it yourself? Start it with:  uv run versa serve',
              style: sans(12, color: Paper.muted)),
          TextButton(
            onPressed: _checking
                ? null
                : () async {
                    setState(() => _checking = true);
                    await app.reconnect();
                    if (mounted) setState(() => _checking = false);
                  },
            child: Text(_checking ? 'Checking…' : 'Check again'),
          ),
        ],
      );
    }
    final llm = app.llmMode == 'live' ? 'live Gemini' : 'stub (no real model)';
    return Text('Server connected · $llm',
        key: const ValueKey('server-ok'), style: sans(12, color: Paper.olive, weight: FontWeight.w500));
  }
}
