import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';

/// First launch: say who you are (a name, no password yet). Also the place that
/// tells you plainly if the server isn't running.
class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _name = TextEditingController();
  bool _busy = false;
  String? _error;
  late Future<Map<String, dynamic>> _health;

  @override
  void initState() {
    super.initState();
    _health = context.read<AppState>().api.health();
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final name = _name.text.trim();
    if (name.isEmpty || _busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<AppState>().signIn(name);
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not sign in: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final api = context.read<AppState>().api;
    return Scaffold(
      backgroundColor: Paper.page,
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Container(
              padding: const EdgeInsets.all(32),
              decoration: BoxDecoration(
                color: Paper.surface,
                border: Border.all(color: Paper.borderStrong),
                borderRadius: BorderRadius.circular(18),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Versa', style: serif(44)),
                  const SizedBox(height: 6),
                  Text('A tutor that stops guessing.',
                      style: sans(15, color: Paper.muted, height: 1.5)),
                  const SizedBox(height: 28),
                  Text('What should I call you?', style: sans(13, weight: FontWeight.w600)),
                  const SizedBox(height: 8),
                  TextField(
                    key: const ValueKey('name-field'),
                    controller: _name,
                    autofocus: true,
                    textInputAction: TextInputAction.done,
                    onSubmitted: (_) => _submit(),
                    style: sans(15),
                    decoration: InputDecoration(
                      hintText: 'Your name',
                      hintStyle: sans(15, color: Paper.faint),
                      filled: true,
                      fillColor: Paper.card,
                      contentPadding:
                          const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(color: Paper.borderStrong),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(color: Paper.ink, width: 1.5),
                      ),
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    'Same name next time = same memory of you. No password yet.',
                    style: sans(12, color: Paper.faint),
                  ),
                  const SizedBox(height: 18),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      key: const ValueKey('continue-button'),
                      onPressed: _busy ? null : _submit,
                      style: FilledButton.styleFrom(
                        backgroundColor: Paper.accent,
                        padding: const EdgeInsets.symmetric(vertical: 16),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                        textStyle: sans(14, weight: FontWeight.w600),
                      ),
                      child: Text(_busy ? 'Signing in…' : 'Continue'),
                    ),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(_error!, style: sans(13, color: Paper.danger)),
                  ],
                  const SizedBox(height: 22),
                  FutureBuilder<Map<String, dynamic>>(
                    future: _health,
                    builder: (context, snap) {
                      if (snap.connectionState != ConnectionState.done) {
                        return Text('Checking the server…', style: sans(12, color: Paper.faint));
                      }
                      if (snap.hasError) {
                        return Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('Can\'t reach the server at ${api.baseUrl}',
                                key: const ValueKey('server-down'),
                                style: sans(12.5, color: Paper.danger, weight: FontWeight.w600)),
                            const SizedBox(height: 4),
                            Text('Start it with:  uv run versa serve',
                                style: sans(12, color: Paper.muted)),
                            TextButton(
                              onPressed: () => setState(() => _health = api.health()),
                              child: const Text('Check again'),
                            ),
                          ],
                        );
                      }
                      final llm = snap.data!['llm'] == 'live' ? 'live Gemini' : 'stub (no real model)';
                      return Text('Server connected · $llm',
                          key: const ValueKey('server-ok'),
                          style: sans(12, color: Paper.olive, weight: FontWeight.w500));
                    },
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
