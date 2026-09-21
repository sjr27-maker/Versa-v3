import 'package:flutter/material.dart';

/// The "paper" palette from the design: warm cream surfaces, ink text, one
/// terracotta accent. Typography is deliberately plain: the platform's normal
/// UI font everywhere (no custom or decorative fonts).
class Paper {
  static const page = Color(0xFFF5F1E8);
  static const surface = Color(0xFFFDFBF4);
  static const card = Color(0xFFFFFFFF);
  static const sliver = Color(0xFFFAF5EA);
  static const border = Color(0xFFE8DCC4);
  static const borderStrong = Color(0xFFE2D6BC);
  static const ink = Color(0xFF2B2823);
  static const body = Color(0xFF6B6255);
  static const muted = Color(0xFF7A7062);
  static const faint = Color(0xFFA89B7F);
  static const accent = Color(0xFFC85A2E);
  static const accentDark = Color(0xFFA84520);
  static const accentSoft = Color(0xFFFDF2EA);
  static const accentLine = Color(0xFFF4DCC6);
  static const olive = Color(0xFF6D9A5C);
  static const warn = Color(0xFFA8843F);
  static const warnSoft = Color(0xFFFAEDD4);
  static const danger = Color(0xFFB3402F);
}

/// Headings: the normal font, semi-bold.
TextStyle serif(double size, {Color color = Paper.ink, bool italic = false, double? height}) =>
    TextStyle(
      fontSize: size,
      color: color,
      fontWeight: FontWeight.w600,
      fontStyle: italic ? FontStyle.italic : FontStyle.normal,
      height: height,
    );

/// Body text.
TextStyle sans(double size,
        {Color color = Paper.ink, FontWeight weight = FontWeight.w400, double? height}) =>
    TextStyle(fontSize: size, color: color, fontWeight: weight, height: height);

/// Small labels and timings: same font, slightly spaced.
TextStyle mono(double size, {Color color = Paper.faint, FontWeight weight = FontWeight.w500}) =>
    TextStyle(fontSize: size, color: color, fontWeight: weight, letterSpacing: 0.6);

ThemeData buildTheme() {
  final base = ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(
      seedColor: Paper.accent,
      brightness: Brightness.light,
      surface: Paper.surface,
    ).copyWith(primary: Paper.accent, onPrimary: Colors.white),
    scaffoldBackgroundColor: Paper.page,
  );
  return base.copyWith(
    textTheme: base.textTheme.apply(bodyColor: Paper.ink, displayColor: Paper.ink),
    dividerColor: Paper.border,
    snackBarTheme: SnackBarThemeData(
      backgroundColor: Paper.ink,
      contentTextStyle: sans(13, color: Paper.surface),
      behavior: SnackBarBehavior.floating,
    ),
  );
}
