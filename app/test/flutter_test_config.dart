import 'dart:async';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

/// Flutter's default test font (Ahem) draws every character as a full-width
/// square, which makes every label look 2-3x wider than it is and reports
/// "overflows" that don't exist. Load the Roboto that ships with the SDK (the
/// app's normal font on Android and the web) so layout tests are realistic.
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  TestWidgetsFlutterBinding.ensureInitialized();
  final root = Platform.environment['FLUTTER_ROOT'];
  if (root != null) {
    final dir = '$root/bin/cache/artifacts/material_fonts';
    final loader = FontLoader('Roboto');
    for (final name in ['roboto-regular', 'roboto-medium', 'roboto-bold', 'roboto-italic']) {
      final file = File('$dir/$name.ttf');
      if (file.existsSync()) {
        loader.addFont(Future.value(ByteData.sublistView(Uint8List.fromList(file.readAsBytesSync()))));
      }
    }
    await loader.load();
  }
  await testMain();
}
