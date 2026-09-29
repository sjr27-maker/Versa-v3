import 'package:flutter/material.dart';

import '../theme.dart';

/// The paper card the sign-in and sign-up screens sit on: full width on a
/// phone, a centred card on anything wider.
class AuthScaffold extends StatelessWidget {
  const AuthScaffold({super.key, required this.child, this.maxWidth = 460});

  final Widget child;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    final narrow = MediaQuery.sizeOf(context).width < 520;
    return Scaffold(
      backgroundColor: narrow ? Paper.surface : Paper.page,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: EdgeInsets.symmetric(horizontal: narrow ? 20 : 24, vertical: 24),
            child: ConstrainedBox(
              constraints: BoxConstraints(maxWidth: maxWidth),
              child: narrow
                  ? child
                  : Container(
                      padding: const EdgeInsets.all(32),
                      decoration: BoxDecoration(
                        color: Paper.surface,
                        border: Border.all(color: Paper.borderStrong),
                        borderRadius: BorderRadius.circular(18),
                      ),
                      child: child,
                    ),
            ),
          ),
        ),
      ),
    );
  }
}

InputDecoration paperInput(String hint, {String? label, Widget? suffix, String? helper}) => InputDecoration(
      hintText: hint,
      labelText: label,
      helperText: helper,
      helperMaxLines: 3,
      suffixIcon: suffix,
      hintStyle: sans(15, color: Paper.faint),
      labelStyle: sans(14, color: Paper.muted),
      helperStyle: sans(12, color: Paper.faint),
      filled: true,
      fillColor: Paper.card,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: Paper.borderStrong),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: Paper.ink, width: 1.5),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: Paper.danger),
      ),
      focusedErrorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: Paper.danger, width: 1.5),
      ),
    );

class PrimaryButton extends StatelessWidget {
  const PrimaryButton({super.key, required this.label, required this.onPressed, this.busy = false});

  final String label;
  final VoidCallback? onPressed;
  final bool busy;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: double.infinity,
        child: FilledButton(
          onPressed: busy ? null : onPressed,
          style: FilledButton.styleFrom(
            backgroundColor: Paper.accent,
            disabledBackgroundColor: Paper.accentLine,
            padding: const EdgeInsets.symmetric(vertical: 16),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
            textStyle: sans(15, weight: FontWeight.w600),
          ),
          child: busy
              ? const SizedBox(
                  width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : Text(label),
        ),
      );
}

class SecondaryButton extends StatelessWidget {
  const SecondaryButton({super.key, required this.label, required this.onPressed, this.icon});

  final String label;
  final VoidCallback? onPressed;
  final Widget? icon;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: double.infinity,
        child: OutlinedButton(
          onPressed: onPressed,
          style: OutlinedButton.styleFrom(
            foregroundColor: Paper.ink,
            backgroundColor: Paper.card,
            side: const BorderSide(color: Paper.borderStrong),
            padding: const EdgeInsets.symmetric(vertical: 15),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
            textStyle: sans(15, weight: FontWeight.w600),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            if (icon != null) ...[icon!, const SizedBox(width: 10)],
            Text(label),
          ]),
        ),
      );
}

/// A "G" mark drawn with text, so no image asset or brand artwork is needed.
class GoogleMark extends StatelessWidget {
  const GoogleMark({super.key});

  @override
  Widget build(BuildContext context) => Container(
        width: 22,
        height: 22,
        alignment: Alignment.center,
        decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: Paper.borderStrong)),
        child: Text('G', style: sans(13, weight: FontWeight.w700, color: const Color(0xFF4285F4))),
      );
}

class OrDivider extends StatelessWidget {
  const OrDivider({super.key});

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 18),
        child: Row(children: [
          const Expanded(child: Divider(color: Paper.border)),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Text('or', style: sans(12, color: Paper.faint)),
          ),
          const Expanded(child: Divider(color: Paper.border)),
        ]),
      );
}

class FormMessage extends StatelessWidget {
  const FormMessage(this.text, {super.key, this.error = true});

  final String text;
  final bool error;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        margin: const EdgeInsets.only(top: 14),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: error ? const Color(0xFFFBEAE6) : Paper.accentSoft,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(text, style: sans(13.5, color: error ? Paper.danger : Paper.ink, height: 1.4)),
      );
}
