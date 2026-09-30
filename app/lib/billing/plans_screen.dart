import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../theme.dart';
import 'sparks.dart';

/// The plans, shown once to a learner right after they first sign in
/// (AppState.needsPlans): Versa Plus with its free month up front (or paid
/// yearly, no free month), and the Exam Pass. Free is offered only where
/// nothing can be bought (no store on this platform). Whatever they pick,
/// [onDone] lets them in.
class PlansScreen extends StatefulWidget {
  const PlansScreen({super.key, required this.name, required this.onDone});

  final String name;
  final VoidCallback onDone;

  @override
  State<PlansScreen> createState() => _PlansScreenState();
}

class _PlansScreenState extends State<PlansScreen> {
  bool _busy = false;
  String? _message;
  String? _examPrice;
  String? _yearPrice;

  @override
  void initState() {
    super.initState();
    final billing = context.read<SparksState>().billing;
    if (billing.supported) {
      billing.priceOf('exam_pass').then((p) {
        if (mounted && p != null) setState(() => _examPrice = p);
      });
      billing.priceOf('default:annual').then((p) {
        if (mounted && p != null) setState(() => _yearPrice = p);
      });
    }
  }

  Future<void> _buy(Future<String> Function() run) async {
    final sparks = context.read<SparksState>();
    if (!sparks.billing.supported) {
      setState(() => _message = sparks.billing.unsupportedReason);
      return;
    }
    setState(() {
      _busy = true;
      _message = null;
    });
    final line = await run();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = line;
    });
    if (sparks.status?.isPaid == true) widget.onDone();
  }

  @override
  Widget build(BuildContext context) {
    final sparks = context.watch<SparksState>();
    final billing = sparks.billing;
    final free = sparks.status?.balance ?? 20;

    return Scaffold(
      backgroundColor: Paper.page,
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: ListView(
              padding: const EdgeInsets.fromLTRB(22, 28, 22, 22),
              children: [
                Text('Welcome, ${widget.name}', style: serif(30)),
                const SizedBox(height: 8),
                Text(
                  'Versa runs on Sparks. Real work costs a few, clarifying questions are free, '
                  'and passing quizzes, finishing lessons and study streaks earn them back.',
                  style: sans(14, color: Paper.body),
                ),
                const SizedBox(height: 22),

                // Versa Plus: the headline, with the free month.
                Container(
                  key: const ValueKey('plans-plus'),
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    color: Paper.accentSoft,
                    border: Border.all(color: Paper.accent, width: 1.5),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Icon(Icons.workspace_premium_rounded, color: Paper.accent),
                      const SizedBox(width: 8),
                      Text('Versa Plus', style: serif(22)),
                      const Spacer(),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(color: Paper.accent, borderRadius: BorderRadius.circular(20)),
                        child: Text('1 MONTH FREE', style: mono(10, color: Colors.white, weight: FontWeight.w700)),
                      ),
                    ]),
                    const SizedBox(height: 12),
                    for (final line in const [
                      'Refills of +50 Sparks every 12 hours, up to 100',
                      'Build as many courses and mock tests as you need',
                      'Keeps every Spark you earn back by learning',
                    ])
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Icon(Icons.check_rounded, size: 18, color: Paper.olive),
                          const SizedBox(width: 8),
                          Expanded(child: Text(line, style: sans(13.5, color: Paper.ink))),
                        ]),
                      ),
                    const SizedBox(height: 6),
                    Text(r'Free for 1 month, then $4.99 a month. Cancel anytime.',
                        style: sans(12.5, color: Paper.body)),
                    const SizedBox(height: 14),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton(
                        key: const ValueKey('plans-start-trial'),
                        style: FilledButton.styleFrom(
                          backgroundColor: Paper.accent,
                          padding: const EdgeInsets.symmetric(vertical: 14),
                        ),
                        onPressed: _busy ? null : () => _buy(() => sparks.buy(billing.showPlusPaywall)),
                        child: Text('Start your free month', style: sans(15, color: Colors.white, weight: FontWeight.w600)),
                      ),
                    ),
                    const SizedBox(height: 10),
                    // Plus paid yearly: the `default` offering's annual package.
                    // The free month is only on the monthly plan.
                    SizedBox(
                      width: double.infinity,
                      child: OutlinedButton(
                        key: const ValueKey('plans-yearly'),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: Paper.accentDark,
                          backgroundColor: Paper.card,
                          side: BorderSide(color: Paper.accent, width: 1.2),
                          padding: const EdgeInsets.symmetric(vertical: 12),
                        ),
                        onPressed: _busy ? null : () => _buy(() => sparks.buy(() => billing.buyOffering('default:annual'))),
                        child: Column(children: [
                          Text('Get the yearly pass · ${_yearPrice ?? r'$39.99'} a year',
                              style: sans(14.5, color: Paper.accentDark, weight: FontWeight.w600)),
                          const SizedBox(height: 2),
                          Text('Save 33% · no free month, paid today', style: sans(11.5, color: Paper.body)),
                        ]),
                      ),
                    ),
                  ]),
                ),
                const SizedBox(height: 12),

                // Exam Pass.
                Material(
                  color: Paper.card,
                  shape: RoundedRectangleBorder(
                    side: BorderSide(color: Paper.border),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: InkWell(
                    key: const ValueKey('plans-exam-pass'),
                    borderRadius: BorderRadius.circular(14),
                    onTap: _busy ? null : () => _buy(() => sparks.buy(() => billing.buyOffering('exam_pass'))),
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Row(children: [
                        Icon(Icons.event_available_rounded, color: Paper.accent),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text('Exam Pass · ${_examPrice ?? r'$2.99'}', style: sans(15, weight: FontWeight.w600)),
                            const SizedBox(height: 2),
                            Text('Plus until your exam day, then it simply ends. Pay once, no subscription.',
                                style: sans(12.5, color: Paper.body)),
                          ]),
                        ),
                        Icon(Icons.chevron_right_rounded, color: Paper.faint),
                      ]),
                    ),
                  ),
                ),

                // Free is only offered where nothing can be bought here (no
                // store on this platform) -- otherwise the free month is the way in.
                if (!billing.supported) ...[
                  const SizedBox(height: 12),
                  OutlinedButton(
                    key: const ValueKey('plans-continue-free'),
                    style: OutlinedButton.styleFrom(
                      side: BorderSide(color: Paper.borderStrong),
                      padding: const EdgeInsets.symmetric(vertical: 14),
                    ),
                    onPressed: _busy ? null : widget.onDone,
                    child: Text('Continue with Free · $free Sparks, +10 every 12 hours',
                        style: sans(14, color: Paper.ink)),
                  ),
                ],

                if (_busy)
                  Padding(padding: EdgeInsets.only(top: 12), child: LinearProgressIndicator(color: Paper.accent)),
                if (_message != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(_message!,
                        key: const ValueKey('plans-message'),
                        textAlign: TextAlign.center,
                        style: sans(13, color: Paper.accentDark)),
                  ),
                const SizedBox(height: 10),
                Text('You can change plans anytime from Settings or the ⚡ Sparks chip.',
                    textAlign: TextAlign.center, style: sans(12, color: Paper.muted)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
