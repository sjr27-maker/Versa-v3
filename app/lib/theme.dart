import 'package:flutter/material.dart';

/// One set of the app's colours. Every screen reads them through [Paper],
/// so switching [Paper.palette] (AppState.setThemeId) re-colours the app.
class PaperPalette {
  const PaperPalette({
    required this.id,
    required this.name,
    required this.brightness,
    required this.page,
    required this.surface,
    required this.card,
    required this.sliver,
    required this.border,
    required this.borderStrong,
    required this.ink,
    required this.body,
    required this.muted,
    required this.faint,
    required this.accent,
    required this.accentDark,
    required this.accentSoft,
    required this.accentLine,
    required this.olive,
    required this.warn,
    required this.warnSoft,
    required this.danger,
    required this.dangerSoft,
    required this.oliveSoft,
    required this.oliveLine,
  });

  final String id;
  final String name;
  final Brightness brightness;
  final Color page;
  final Color surface;
  final Color card;
  final Color sliver;
  final Color border;
  final Color borderStrong;
  final Color ink;
  final Color body;
  final Color muted;
  final Color faint;
  final Color accent;
  final Color accentDark;
  final Color accentSoft;
  final Color accentLine;
  final Color olive;
  final Color warn;
  final Color warnSoft;
  final Color danger;
  final Color dangerSoft;
  final Color oliveSoft;
  final Color oliveLine;

  /// The design's own: warm cream surfaces, ink text, one terracotta accent.
  static const paper = PaperPalette(
    id: 'paper',
    name: 'Paper',
    brightness: Brightness.light,
    page: Color(0xFFF5F1E8),
    surface: Color(0xFFFDFBF4),
    card: Color(0xFFFFFFFF),
    sliver: Color(0xFFFAF5EA),
    border: Color(0xFFE8DCC4),
    borderStrong: Color(0xFFE2D6BC),
    ink: Color(0xFF2B2823),
    body: Color(0xFF6B6255),
    muted: Color(0xFF7A7062),
    faint: Color(0xFFA89B7F),
    accent: Color(0xFFC85A2E),
    accentDark: Color(0xFFA84520),
    accentSoft: Color(0xFFFDF2EA),
    accentLine: Color(0xFFF4DCC6),
    olive: Color(0xFF6D9A5C),
    warn: Color(0xFFA8843F),
    warnSoft: Color(0xFFFAEDD4),
    danger: Color(0xFFB3402F),
    dangerSoft: Color(0xFFFBECE8),
    oliveSoft: Color(0xFFEAF3E6),
    oliveLine: Color(0xFFCFE2C7),
  );

  /// Cool and light: grey-blue paper, a blue accent.
  static const mist = PaperPalette(
    id: 'mist',
    name: 'Mist',
    brightness: Brightness.light,
    page: Color(0xFFEEF1F5),
    surface: Color(0xFFF8FAFC),
    card: Color(0xFFFFFFFF),
    sliver: Color(0xFFF3F6F9),
    border: Color(0xFFDCE2EA),
    borderStrong: Color(0xFFCFD7E1),
    ink: Color(0xFF1F2733),
    body: Color(0xFF4E5B6B),
    muted: Color(0xFF5F6B7A),
    faint: Color(0xFF8C97A6),
    accent: Color(0xFF3F6FD8),
    accentDark: Color(0xFF2F57B0),
    accentSoft: Color(0xFFEAF0FC),
    accentLine: Color(0xFFCFDCF6),
    olive: Color(0xFF4E9A6B),
    warn: Color(0xFFA8843F),
    warnSoft: Color(0xFFFAEFD6),
    danger: Color(0xFFC0443A),
    dangerSoft: Color(0xFFFBEAE8),
    oliveSoft: Color(0xFFE6F3EB),
    oliveLine: Color(0xFFC6E2D0),
  );

  /// Dark: warm charcoal, cream text, a brighter terracotta.
  static const night = PaperPalette(
    id: 'night',
    name: 'Night',
    brightness: Brightness.dark,
    page: Color(0xFF171513),
    surface: Color(0xFF1F1C19),
    card: Color(0xFF272320),
    sliver: Color(0xFF1C1917),
    border: Color(0xFF3A342D),
    borderStrong: Color(0xFF474037),
    ink: Color(0xFFEDE6DA),
    body: Color(0xFFC4B9A8),
    muted: Color(0xFFAFA391),
    faint: Color(0xFF857A69),
    accent: Color(0xFFE07A4C),
    accentDark: Color(0xFFF0946A),
    accentSoft: Color(0xFF3A2419),
    accentLine: Color(0xFF5A3624),
    olive: Color(0xFF8DBA7A),
    warn: Color(0xFFD2AE66),
    warnSoft: Color(0xFF3A2F1A),
    danger: Color(0xFFE0695A),
    dangerSoft: Color(0xFF3A201C),
    oliveSoft: Color(0xFF1F2E1C),
    oliveLine: Color(0xFF34502C),
  );

  static const all = [paper, mist, night];

  static PaperPalette byId(String? id) => all.firstWhere((p) => p.id == id, orElse: () => paper);
}

/// The app's colours, from the chosen [palette]. Typography is deliberately
/// plain: the platform's normal UI font everywhere (no custom or decorative
/// fonts).
class Paper {
  static PaperPalette palette = PaperPalette.paper;

  static bool get isDark => palette.brightness == Brightness.dark;
  static Color get page => palette.page;
  static Color get surface => palette.surface;
  static Color get card => palette.card;
  static Color get sliver => palette.sliver;
  static Color get border => palette.border;
  static Color get borderStrong => palette.borderStrong;
  static Color get ink => palette.ink;
  static Color get body => palette.body;
  static Color get muted => palette.muted;
  static Color get faint => palette.faint;
  static Color get accent => palette.accent;
  static Color get accentDark => palette.accentDark;
  static Color get accentSoft => palette.accentSoft;
  static Color get accentLine => palette.accentLine;
  static Color get olive => palette.olive;
  static Color get warn => palette.warn;
  static Color get warnSoft => palette.warnSoft;
  static Color get danger => palette.danger;
  static Color get dangerSoft => palette.dangerSoft;
  static Color get oliveSoft => palette.oliveSoft;
  static Color get oliveLine => palette.oliveLine;
}

/// Headings: the normal font, semi-bold.
TextStyle serif(double size, {Color? color, bool italic = false, double? height}) =>
    TextStyle(
      fontSize: size,
      color: color ?? Paper.ink,
      fontWeight: FontWeight.w600,
      fontStyle: italic ? FontStyle.italic : FontStyle.normal,
      height: height,
    );

/// Body text.
TextStyle sans(double size,
        {Color? color, FontWeight weight = FontWeight.w400, double? height}) =>
    TextStyle(fontSize: size, color: color ?? Paper.ink, fontWeight: weight, height: height);

/// Small labels and timings: same font, slightly spaced.
TextStyle mono(double size, {Color? color, FontWeight weight = FontWeight.w500}) =>
    TextStyle(fontSize: size, color: color ?? Paper.faint, fontWeight: weight, letterSpacing: 0.6);

ThemeData buildTheme() {
  final base = ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(
      seedColor: Paper.accent,
      brightness: Paper.palette.brightness,
      surface: Paper.surface,
      onSurface: Paper.ink,
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
