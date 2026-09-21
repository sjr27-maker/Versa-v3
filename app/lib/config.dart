import 'package:flutter/foundation.dart';

/// Where the Versa API lives.
///
///  * Served by `versa serve` (the web build at http://localhost:8000): the
///    page's own origin, so there is nothing to configure.
///  * `flutter run -d edge` / desktop / mobile: http://localhost:8000, or
///    whatever `--dart-define=VERSA_API=http://host:port` says.
String defaultApiBase() {
  const fromEnv = String.fromEnvironment('VERSA_API');
  if (fromEnv.isNotEmpty) return fromEnv;
  if (kIsWeb) return Uri.base.origin;
  return 'http://localhost:8000';
}
