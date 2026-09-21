// Data shapes shared by the API client, the chat controller and the screens.

class Learner {
  const Learner({required this.id, required this.label});
  final String id;
  final String label;
}

class ChatOption {
  const ChatOption({required this.id, required this.text});
  final String id;
  final String text;
}

enum Role { user, tutor }

/// What the person experienced for one answer, measured on the device (so it
/// includes the network), plus the server's own numbers for comparison.
class Timing {
  const Timing({
    required this.firstOutputMs,
    required this.totalMs,
    this.serverFirstOutputMs,
    this.serverTotalMs,
  });
  final int firstOutputMs;
  final int totalMs;
  final int? serverFirstOutputMs;
  final int? serverTotalMs;
}

class ChatMessage {
  ChatMessage({
    required this.id,
    required this.role,
    this.text = '',
    this.pending = false,
    this.streaming = false,
    this.isError = false,
    this.options = const [],
  });

  final int id;
  final Role role;
  String text;

  /// Waiting for the first word (shows the typing indicator).
  bool pending;

  /// Words are arriving.
  bool streaming;
  bool isError;

  /// Clickable readings offered instead of an answer ("which did you mean?").
  List<ChatOption> options;
  String? chosenOptionId;
  Timing? timing;

  bool get hasOptions => options.isNotEmpty;
  bool get optionsResolved => chosenOptionId != null;
}

// --------------------------------------------------------- server events

/// One frame from the server's chat WebSocket (see src/versa/server.py).
sealed class ServerEvent {
  const ServerEvent();
}

class TurnStart extends ServerEvent {
  const TurnStart(this.turnIndex);
  final int turnIndex;
}

class Delta extends ServerEvent {
  const Delta(this.text);
  final String text;
}

class OptionsEvent extends ServerEvent {
  const OptionsEvent(this.message, this.options);
  final String message;
  final List<ChatOption> options;
}

class Done extends ServerEvent {
  const Done({
    required this.turnIndex,
    required this.kind,
    required this.text,
    required this.firstOutputMs,
    required this.totalMs,
  });
  final int turnIndex;
  final String kind; // "answer" | "options"
  final String text;
  final int firstOutputMs;
  final int totalMs;
}

class ErrorEvent extends ServerEvent {
  const ErrorEvent(this.message);
  final String message;
}

ServerEvent? parseServerEvent(Map<String, dynamic> json) {
  switch (json['type']) {
    case 'turn_start':
      return TurnStart((json['turn_index'] as num).toInt());
    case 'delta':
      return Delta(json['text'] as String? ?? '');
    case 'options':
      return OptionsEvent(
        json['message'] as String? ?? '',
        [
          for (final o in (json['options'] as List? ?? const []))
            ChatOption(id: o['id'] as String, text: o['text'] as String),
        ],
      );
    case 'done':
      final timing = (json['timing'] as Map?) ?? const {};
      return Done(
        turnIndex: (json['turn_index'] as num).toInt(),
        kind: json['kind'] as String? ?? 'answer',
        text: json['text'] as String? ?? '',
        firstOutputMs: (timing['first_output_ms'] as num?)?.toInt() ?? 0,
        totalMs: (timing['total_ms'] as num?)?.toInt() ?? 0,
      );
    case 'error':
      return ErrorEvent(json['message'] as String? ?? 'something went wrong');
  }
  return null; // unknown frame types are ignored, never fatal
}
