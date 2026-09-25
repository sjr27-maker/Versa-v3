import 'package:flutter/foundation.dart';

/// Text waiting to be put into the next message box that appears (or the one
/// already on screen) — how a Home feed card opens a chat with its starter
/// message typed but not sent. The composer takes it and clears it.
final composerDraft = ValueNotifier<String?>(null);
