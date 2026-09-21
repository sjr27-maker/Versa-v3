import 'package:flutter/material.dart';

import '../theme.dart';

/// A page for a feature that is designed but not built yet: says so plainly and
/// lists what is planned, so the shape of the app is visible while it grows.
class PlaceholderPage extends StatelessWidget {
  const PlaceholderPage({
    super.key,
    required this.kicker,
    required this.title,
    required this.blurb,
    required this.planned,
    this.icon = Icons.hourglass_empty_rounded,
  });

  final String kicker;
  final String title;
  final String blurb;
  final List<String> planned;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(40, 36, 40, 60),
      child: Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(kicker.toUpperCase(), style: mono(11)),
              const SizedBox(height: 8),
              Text(title, style: serif(34)),
              const SizedBox(height: 12),
              Text(blurb, style: sans(14.5, color: Paper.muted, height: 1.6)),
              const SizedBox(height: 28),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(22),
                decoration: BoxDecoration(
                  color: Paper.card,
                  border: Border.all(color: Paper.borderStrong),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Icon(icon, size: 18, color: Paper.warn),
                      const SizedBox(width: 8),
                      const _ComingSoonPill(),
                    ]),
                    const SizedBox(height: 14),
                    Text('Planned', style: sans(13, weight: FontWeight.w600)),
                    const SizedBox(height: 8),
                    for (final item in planned)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Padding(
                              padding: const EdgeInsets.only(top: 7, right: 10),
                              child: Container(
                                width: 5,
                                height: 5,
                                decoration: const BoxDecoration(
                                    color: Paper.faint, shape: BoxShape.circle),
                              ),
                            ),
                            Expanded(
                                child: Text(item, style: sans(13.5, color: Paper.body, height: 1.5))),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ComingSoonPill extends StatelessWidget {
  const _ComingSoonPill();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      decoration: BoxDecoration(
        color: Paper.warnSoft,
        border: Border.all(color: const Color(0xFFF0D99A)),
        borderRadius: BorderRadius.circular(100),
      ),
      child: Text('COMING SOON', style: mono(10, color: Paper.warn, weight: FontWeight.w600)),
    );
  }
}

/// The small pill reused on mode cards and settings rows.
class ComingSoonPill extends StatelessWidget {
  const ComingSoonPill({super.key});

  @override
  Widget build(BuildContext context) => const _ComingSoonPill();
}
