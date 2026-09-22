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
    this.chosenOptionId,
    this.optionsOpen = true,
    this.timing,
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

  /// Still awaiting a decision: at least one reading is clickable. False the
  /// moment ANY of a click, typing past instead (see ChatController.send),
  /// or (for a chat loaded from history) the server already recording every
  /// reading as resolved settles it — a stale button must never look live.
  bool optionsOpen;
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

// ------------------------------------------------------------- chat history

/// One row of the chat-history sidebar (`GET /api/learners/{id}/sessions`) --
/// this learner's chats within ONE app mode, newest-active first. `preview`
/// is null for a chat with no messages yet ("New chat" in the UI).
class ChatSummary {
  const ChatSummary({
    required this.sessionId,
    required this.appMode,
    required this.turnCount,
    required this.createdAt,
    required this.lastActivityAt,
    this.preview,
  });

  final String sessionId;
  final String appMode;
  final int turnCount;
  final DateTime createdAt;
  final DateTime lastActivityAt;
  final String? preview;

  factory ChatSummary.fromJson(Map<String, dynamic> json) => ChatSummary(
        sessionId: json['session_id'] as String,
        appMode: json['app_mode'] as String,
        turnCount: (json['turn_count'] as num).toInt(),
        createdAt: DateTime.parse(json['created_at'] as String),
        lastActivityAt: DateTime.parse(json['last_activity_at'] as String),
        preview: json['preview'] as String?,
      );
}

const _noResponseText = 'No response was recorded for this message.';

/// `GET /api/sessions/{id}/history`'s turn-by-turn record, expanded into the
/// same `ChatMessage` shape the live chat builds turn by turn (see
/// ChatController._onEvent) -- one rendering path either way, not two.
List<ChatMessage> parseSessionHistory(List<dynamic> json) {
  final messages = <ChatMessage>[];
  var id = 0;
  for (final raw in json) {
    final turn = raw as Map<String, dynamic>;
    // null (not just empty) on a resumed click-resolution answer turn --
    // the live chat shows no bubble for a click either (see
    // ChatController.pickOption), so a resumed chat must not add one just
    // because the option's own copy is sitting in student_text.
    final studentText = turn['student_text'] as String?;
    if (studentText != null) {
      messages.add(ChatMessage(id: id++, role: Role.user, text: studentText));
    }
    switch (turn['kind']) {
      case 'answer':
        messages.add(
          ChatMessage(id: id++, role: Role.tutor, text: turn['tutor_text'] as String? ?? ''),
        );
      case 'options':
        final rows = (turn['options'] as List? ?? const []).cast<Map<String, dynamic>>();
        final options = [
          for (final o in rows) ChatOption(id: o['id'] as String, text: o['text'] as String),
        ];
        final selected = rows.where((o) => o['status'] == 'selected');
        messages.add(
          ChatMessage(
            id: id++,
            role: Role.tutor,
            text: turn['options_message'] as String? ?? '',
            options: options,
            chosenOptionId: selected.isEmpty ? null : selected.first['id'] as String,
            optionsOpen: rows.any((o) => o['status'] == 'open'),
          ),
        );
      case 'pending':
        messages.add(
          ChatMessage(id: id++, role: Role.tutor, text: _noResponseText, isError: true),
        );
    }
  }
  return messages;
}
