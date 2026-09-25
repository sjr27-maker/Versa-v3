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

/// A chat's style controls (server: session_knobs.py): answer length and
/// depth as 0-100 slider levels. 50/50 is the untouched default.
class SessionKnobs {
  const SessionKnobs({this.answerLength = 50, this.depth = 50});

  final int answerLength;
  final int depth;

  factory SessionKnobs.fromJson(Map<String, dynamic> j) => SessionKnobs(
        answerLength: (j['answer_length'] as num?)?.toInt() ?? 50,
        depth: (j['depth'] as num?)?.toInt() ?? 50,
      );

  SessionKnobs copyWith({int? answerLength, int? depth}) => SessionKnobs(
        answerLength: answerLength ?? this.answerLength,
        depth: depth ?? this.depth,
      );

  @override
  bool operator ==(Object other) =>
      other is SessionKnobs && other.answerLength == answerLength && other.depth == depth;

  @override
  int get hashCode => Object.hash(answerLength, depth);
}

/// A claim update the sandbox-chat background flow made (or is offering to
/// undo) -- the inline "Noted: ..." note under a tutor turn (server:
/// reviews.apply_stated_preference_to_claims / session_history.py's replay
/// of it). `action` is one of created/approve/edit/archive/supported.
class ClaimUpdate {
  const ClaimUpdate({
    required this.action,
    required this.claimId,
    required this.statement,
    this.reviewId,
  });

  final String action;
  final String claimId;
  final String? statement;
  final String? reviewId;

  factory ClaimUpdate.fromJson(Map<String, dynamic> json) => ClaimUpdate(
        action: json['action'] as String,
        claimId: json['claim_id'] as String,
        statement: json['statement'] as String?,
        reviewId: json['review_id'] as String?,
      );

  String get noteText => switch (action) {
        'created' => 'Noted: "$statement" (added to your thinking style)',
        'approve' => 'Noted: confirmed "$statement"',
        'edit' => 'Noted: updated to "$statement"',
        'archive' => 'Noted: retired that preference',
        'supported' => 'Noted: this matches what you\'ve said before',
        _ => 'Noted.',
      };
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

  /// Set on a tutor turn the sandbox-chat claim-update flow touched -- see
  /// [ClaimUpdate].
  ClaimUpdate? claimUpdate;

  /// Being rewritten at new slider levels (the text streams in place).
  bool rewriting = false;

  /// Still awaiting a decision: at least one reading is clickable. False the
  /// moment ANY of a click, typing past instead (see ChatController.send),
  /// or (for a chat loaded from history) the server already recording every
  /// reading as resolved settles it — a stale button must never look live.
  bool optionsOpen;
  Timing? timing;

  /// Memory already knew what the student meant (server "recalled" frame,
  /// IDEAS.md "oh wait..."): the turn answered from it, taking back any
  /// options it had already shown.
  bool recalled = false;

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

/// Live rewrite of the latest answer at new slider levels. Every frame
/// carries the client's request id so pieces of a superseded rewrite can be
/// dropped.
class RegenStart extends ServerEvent {
  const RegenStart(this.requestId);
  final int requestId;
}

class RegenDelta extends ServerEvent {
  const RegenDelta(this.requestId, this.text);
  final int requestId;
  final String text;
}

class RegenDone extends ServerEvent {
  const RegenDone(this.requestId, this.text);
  final int requestId;
  final String text;
}

/// The rewrite ended without a new answer: nothing to rewrite, or it failed.
class RegenEnded extends ServerEvent {
  const RegenEnded(this.requestId);
  final int requestId;
}

class ClaimUpdateEvent extends ServerEvent {
  const ClaimUpdateEvent(this.update);
  final ClaimUpdate update;
}

/// The stage's performance for an answer turn (server.py "stage" frames):
/// `stage_start`, then one [StageActionEvent] per action as the director
/// writes it, then `stage_end`. Raw action JSON -- the stage parses it
/// (stage/script.dart), the chat doesn't need to understand it.
class StageStart extends ServerEvent {
  const StageStart(this.turnIndex);
  final int turnIndex;
}

class StageActionEvent extends ServerEvent {
  const StageActionEvent(this.turnIndex, this.action);
  final int turnIndex;
  final Map<String, dynamic> action;
}

/// Memory knew what the student meant (server.py "recalled" frame). With
/// [retracted], the options already shown this turn are taken back and the
/// answer's deltas follow.
class RecalledEvent extends ServerEvent {
  const RecalledEvent({required this.retracted});
  final bool retracted;
}

class StageEnd extends ServerEvent {
  const StageEnd(this.turnIndex);
  final int turnIndex;
}

/// A lesson task was judged complete (lesson chats only; server `progress`
/// frame, may arrive after `done`). Not chat content: it goes to
/// ChatController.progressEvents.
class ProgressEvent extends ServerEvent {
  const ProgressEvent({
    required this.lessonId,
    required this.taskId,
    required this.lessonPercent,
    required this.chapterPercent,
    required this.topicPercent,
    required this.lessonStatus,
  });

  final String lessonId;
  final String taskId;
  final int lessonPercent;
  final int chapterPercent;
  final int topicPercent;
  final String lessonStatus;
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
    case 'claim_update':
      return ClaimUpdateEvent(ClaimUpdate.fromJson(json));
    case 'regen_start':
      return RegenStart((json['request_id'] as num).toInt());
    case 'regen_delta':
      return RegenDelta((json['request_id'] as num).toInt(), json['text'] as String? ?? '');
    case 'regen_done':
      return RegenDone((json['request_id'] as num).toInt(), json['text'] as String? ?? '');
    case 'regen_skipped':
    case 'regen_error':
      return RegenEnded((json['request_id'] as num?)?.toInt() ?? -1);
    case 'stage_start':
      return StageStart((json['turn_index'] as num?)?.toInt() ?? -1);
    case 'stage':
      final action = json['action'];
      if (action is! Map) return null;
      return StageActionEvent((json['turn_index'] as num?)?.toInt() ?? -1, action.cast<String, dynamic>());
    case 'recalled':
      return RecalledEvent(retracted: json['retracted'] == true);
    case 'stage_end':
      return StageEnd((json['turn_index'] as num?)?.toInt() ?? -1);
    case 'progress':
      return ProgressEvent(
        lessonId: json['lesson_id'] as String? ?? '',
        taskId: json['task_id'] as String? ?? '',
        lessonPercent: (json['lesson_percent'] as num?)?.toInt() ?? 0,
        chapterPercent: (json['chapter_percent'] as num?)?.toInt() ?? 0,
        topicPercent: (json['topic_percent'] as num?)?.toInt() ?? 0,
        lessonStatus: json['lesson_status'] as String? ?? 'in_progress',
      );
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
    this.lessonId,
  });

  final String sessionId;
  final String appMode;
  final int turnCount;
  final DateTime createdAt;
  final DateTime lastActivityAt;
  final String? preview;

  /// Set on a Learn-a-topic chat: the lesson it belongs to.
  final String? lessonId;

  factory ChatSummary.fromJson(Map<String, dynamic> json) => ChatSummary(
        sessionId: json['session_id'] as String,
        appMode: json['app_mode'] as String,
        turnCount: (json['turn_count'] as num).toInt(),
        createdAt: DateTime.parse(json['created_at'] as String),
        lastActivityAt: DateTime.parse(json['last_activity_at'] as String),
        preview: json['preview'] as String?,
        lessonId: json['lesson_id'] as String?,
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
    final claimUpdateJson = turn['claim_update'] as Map<String, dynamic>?;
    final claimUpdate = claimUpdateJson == null ? null : ClaimUpdate.fromJson(claimUpdateJson);
    switch (turn['kind']) {
      case 'answer':
        messages.add(
          ChatMessage(id: id++, role: Role.tutor, text: turn['tutor_text'] as String? ?? '')
            ..claimUpdate = claimUpdate,
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

// ------------------------------------------------------------ thinking style

/// One row in the Thinking-style page's Confirmed/Emerging/Retired sections
/// or Claims list (`GET /api/learners/{id}/thinking-style`).
class ThinkingStyleItem {
  const ThinkingStyleItem({
    required this.id,
    required this.summary,
    required this.status,
    required this.confirmations,
    required this.sessions,
    required this.edited,
    required this.archived,
    this.test,
    this.confidence,
    this.evidenceCount,
  });

  final String id;
  final String summary;
  final String status;
  final int confirmations;
  final int sessions;
  final bool edited;
  final bool archived;
  // Claims only.
  final String? test;
  final double? confidence;
  final int? evidenceCount;

  factory ThinkingStyleItem.fromJson(Map<String, dynamic> j) => ThinkingStyleItem(
        id: j['id'] as String,
        summary: (j['summary'] ?? j['statement']) as String,
        status: j['status'] as String,
        confirmations: (j['confirmations'] as num?)?.toInt() ?? 0,
        sessions: (j['sessions'] as num).toInt(),
        edited: j['edited'] as bool,
        archived: j['archived'] as bool,
        test: j['test'] as String?,
        confidence: (j['confidence'] as num?)?.toDouble(),
        evidenceCount: (j['evidence_count'] as num?)?.toInt(),
      );
}

class ThinkingStyleOverview {
  const ThinkingStyleOverview({
    required this.promotionThreshold,
    required this.confirmed,
    required this.emerging,
    required this.retired,
    required this.claims,
  });

  final int promotionThreshold;
  final List<ThinkingStyleItem> confirmed;
  final List<ThinkingStyleItem> emerging;
  final List<ThinkingStyleItem> retired;
  final List<ThinkingStyleItem> claims;

  factory ThinkingStyleOverview.fromJson(Map<String, dynamic> j) => ThinkingStyleOverview(
        promotionThreshold: (j['promotion_threshold'] as num).toInt(),
        confirmed: [for (final r in j['confirmed'] as List) ThinkingStyleItem.fromJson(r)],
        emerging: [for (final r in j['emerging'] as List) ThinkingStyleItem.fromJson(r)],
        retired: [for (final r in j['retired'] as List) ThinkingStyleItem.fromJson(r)],
        claims: [for (final r in j['claims'] as List) ThinkingStyleItem.fromJson(r)],
      );
}

/// One entry in an item's review history (`ReviewOut`).
class ReviewEntry {
  const ReviewEntry({
    required this.id,
    required this.reviewType,
    required this.source,
    this.revisedStatement,
  });

  final String id;
  final String reviewType;
  final String source;
  final String? revisedStatement;

  factory ReviewEntry.fromJson(Map<String, dynamic> j) => ReviewEntry(
        id: j['id'] as String,
        reviewType: j['review_type'] as String,
        source: j['source'] as String,
        revisedStatement: j['revised_statement'] as String?,
      );
}

/// `GET /api/claims/{id}` -- the View action's full detail.
class ClaimDetail {
  const ClaimDetail({
    required this.id,
    required this.statement,
    required this.edited,
    required this.archived,
    required this.test,
    required this.status,
    required this.confidence,
    required this.evidenceCount,
    required this.reviews,
    this.evidenceLines = const [],
  });

  /// One readable line per evidence episode (date, topic, supports/contradicts).
  final List<String> evidenceLines;
  final String id;
  final String statement;
  final bool edited;
  final bool archived;
  final String test;
  final String status;
  final double confidence;
  final int evidenceCount;
  final List<ReviewEntry> reviews;

  factory ClaimDetail.fromJson(Map<String, dynamic> j) => ClaimDetail(
        id: j['id'] as String,
        statement: j['statement'] as String,
        edited: j['edited'] as bool,
        archived: j['archived'] as bool,
        test: j['test'] as String,
        status: j['status'] as String,
        confidence: (j['confidence'] as num).toDouble(),
        evidenceCount: (j['evidence'] as List).length,
        reviews: [for (final r in j['reviews'] as List) ReviewEntry.fromJson(r)],
        evidenceLines: [
          for (final e in j['evidence'] as List)
            '${(e['created_at'] as String).substring(0, 10)} · ${e['topic']} · ${e['direction']}',
        ],
      );
}

/// `GET /api/thinking-style/candidates/{id}` -- the View action's full detail.
class ThinkingStyleDetail {
  const ThinkingStyleDetail({
    required this.id,
    required this.summary,
    required this.edited,
    required this.archived,
    required this.status,
    required this.confirmations,
    required this.sessionCount,
    required this.reviews,
    this.sessionLines = const [],
  });

  /// One readable line per confirming session (its opening message, and
  /// whether it matched the pattern).
  final List<String> sessionLines;

  final String id;
  final String summary;
  final bool edited;
  final bool archived;
  final String status;
  final int confirmations;
  final int sessionCount;
  final List<ReviewEntry> reviews;

  factory ThinkingStyleDetail.fromJson(Map<String, dynamic> j) => ThinkingStyleDetail(
        id: j['id'] as String,
        summary: j['summary'] as String,
        edited: j['edited'] as bool,
        archived: j['archived'] as bool,
        status: j['status'] as String,
        confirmations: (j['confirmations'] as num).toInt(),
        sessionCount: (j['sessions'] as List).length,
        reviews: [for (final r in j['reviews'] as List) ReviewEntry.fromJson(r)],
        sessionLines: [
          for (final x in j['sessions'] as List)
            'Session ${(x['index'] as num).toInt() + 1}: "${x['topic_preview']}"'
                '${x['confirms'] == null ? ' (started this pattern)' : x['confirms'] == true ? ' (matched)' : ' (did not match)'}',
        ],
      );
}

class QnAEntry {
  const QnAEntry({
    required this.id,
    required this.question,
    required this.answer,
    required this.intent,
    this.appliedReviewId,
  });

  final String id;
  final String question;
  final String answer;
  final String intent;
  final String? appliedReviewId;

  factory QnAEntry.fromJson(Map<String, dynamic> j) => QnAEntry(
        id: j['id'] as String,
        question: j['question'] as String,
        answer: j['answer'] as String,
        intent: j['intent'] as String,
        appliedReviewId: j['applied_review_id'] as String?,
      );
}
