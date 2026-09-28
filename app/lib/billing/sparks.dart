import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import '../api.dart';
import 'billing.dart';

/// A learner's Sparks and plan, as the server sees them
/// (GET /api/learners/{id}/sparks and /billing, src/versa/sparks.py + billing.py).
class SparksStatus {
  const SparksStatus({
    required this.balance,
    required this.tier,
    required this.cap,
    required this.refillAmount,
    required this.nextRefillAt,
    required this.costs,
    required this.enabled,
    this.examPassEndsAt,
    this.plusExpiresAt,
    this.spentToday = 0,
    this.streakDays = 0,
    this.studiedToday = false,
    this.streakGoal = 5,
    this.rewards = const {},
  });

  final int balance;

  /// "free", "plus" or "exam_pass".
  final String tier;
  final int cap;
  final int refillAmount;
  final DateTime? nextRefillAt;
  final Map<String, int> costs;
  final bool enabled;
  final DateTime? examPassEndsAt;
  final DateTime? plusExpiresAt;

  /// The usage meter: Sparks spent today (net of refunds), and the study
  /// streak -- consecutive days with learning, alive until a day is missed.
  /// Every [streakGoal] days earns the study-streak reward.
  final int spentToday;
  final int streakDays;
  final bool studiedToday;
  final int streakGoal;

  /// What learning pays back, by reason ('study_streak', 'unit_quiz_passed', ...).
  final Map<String, int> rewards;
  int rewardFor(String reason) => rewards[reason] ?? (reason == 'study_streak' ? 5 : 0);

  /// How full the tank is, 0..1 (a balance above the cap reads as full).
  double get fill => cap <= 0 ? 0 : (balance / cap).clamp(0.0, 1.0);

  /// Days into the current streak cycle, 0..[streakGoal].
  int get streakProgress {
    if (streakGoal <= 0 || streakDays == 0) return 0;
    final r = streakDays % streakGoal;
    return r == 0 ? streakGoal : r;
  }

  bool get isPaid => tier == 'plus' || tier == 'exam_pass';

  String get planName => switch (tier) {
        'plus' => 'Versa Plus',
        'exam_pass' => 'Exam Pass',
        _ => 'Free',
      };

  factory SparksStatus.fromJson(Map<String, dynamic> j, {Map<String, dynamic>? billing}) => SparksStatus(
        balance: (j['balance'] as num?)?.toInt() ?? 0,
        tier: (billing?['tier'] ?? j['tier']) as String? ?? 'free',
        cap: (j['cap'] as num?)?.toInt() ?? 0,
        refillAmount: (j['refill_amount'] as num?)?.toInt() ?? 0,
        nextRefillAt: DateTime.tryParse(j['next_refill_at'] as String? ?? '')?.toLocal(),
        costs: {
          for (final e in ((j['costs'] as Map?) ?? const {}).entries) '${e.key}': (e.value as num).toInt(),
        },
        enabled: j['enabled'] as bool? ?? true,
        examPassEndsAt: DateTime.tryParse(billing?['exam_pass_ends_at'] as String? ?? '')?.toLocal(),
        plusExpiresAt: DateTime.tryParse(billing?['plus_expires_at'] as String? ?? '')?.toLocal(),
        spentToday: (j['spent_today'] as num?)?.toInt() ?? 0,
        streakDays: (j['streak_days'] as num?)?.toInt() ?? 0,
        studiedToday: j['studied_today'] as bool? ?? false,
        streakGoal: (j['streak_goal'] as num?)?.toInt() ?? 5,
        rewards: {
          for (final e in ((j['rewards'] as Map?) ?? const {}).entries) '${e.key}': (e.value as num).toInt(),
        },
      );

  SparksStatus withBalance(int value) => SparksStatus(
        balance: value, tier: tier, cap: cap, refillAmount: refillAmount, nextRefillAt: nextRefillAt,
        costs: costs, enabled: enabled, examPassEndsAt: examPassEndsAt, plusExpiresAt: plusExpiresAt,
        spentToday: spentToday + (value < balance ? balance - value : 0),
        streakDays: value < balance && !studiedToday ? streakDays + 1 : streakDays,
        studiedToday: studiedToday || value < balance,
        streakGoal: streakGoal,
        rewards: rewards,
      );
}

/// Why the paywall is showing.
class PaywallRequest {
  const PaywallRequest({
    required this.action,
    this.needed,
    this.balance,
    this.nextRefillAt,
    this.examId,
  });

  /// The priced action that was refused ("answer", "mock_test", ...), or
  /// "upgrade" when the person opened the plans themselves.
  final String action;
  final int? needed;
  final int? balance;
  final DateTime? nextRefillAt;

  /// The exam being worked on, so an Exam Pass bought here is tied to it.
  final String? examId;

  factory PaywallRequest.fromDetail(Map<String, dynamic> d, {String? examId}) => PaywallRequest(
        action: d['action'] as String? ?? 'answer',
        needed: (d['needed'] as num?)?.toInt(),
        balance: (d['balance'] as num?)?.toInt(),
        nextRefillAt: DateTime.tryParse(d['next_refill_at'] as String? ?? '')?.toLocal(),
        examId: examId,
      );

  bool get forExam => const {'create_exam', 'unit_quiz', 'mock_test'}.contains(action) || examId != null;
}

/// The server refused an action for want of Sparks (HTTP 402).
class OutOfSparksException extends ApiException {
  OutOfSparksException(this.request) : super("You're out of Sparks for now.");
  final PaywallRequest request;
}

/// A 402 from any API client becomes an [OutOfSparksException] AND a paywall
/// request here, so the screen that made the call doesn't have to know about
/// Sparks: the shell (SparksPaywallListener) opens the sheet.
class PaywallHub {
  PaywallHub._();
  static final _requests = StreamController<PaywallRequest>.broadcast();
  static Stream<PaywallRequest> get requests => _requests.stream;
  static void request(PaywallRequest r) => _requests.add(r);

  /// Something that may have spent or earned Sparks just succeeded (a POST
  /// from an API client): the shared SparksState re-reads the balance.
  static final _changed = StreamController<void>.broadcast();
  static Stream<void> get changes => _changed.stream;
  static void changed() => _changed.add(null);

  /// For API clients: throw (and announce) when [r] is a 402.
  static void check(http.Response r, {String? examId}) {
    if (r.statusCode != 402) return;
    var detail = <String, dynamic>{};
    try {
      final d = (jsonDecode(r.body) as Map<String, dynamic>)['detail'];
      if (d is Map) detail = d.cast<String, dynamic>();
    } catch (_) {}
    final request = PaywallRequest.fromDetail(detail, examId: examId);
    PaywallHub.request(request);
    throw OutOfSparksException(request);
  }
}

class SparksApi {
  SparksApi(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();
  factory SparksApi.of(VersaApi api) => SparksApi(api.baseUrl, client: api.httpClient);

  final String baseUrl;
  final http.Client _http;

  Uri _uri(String path) => Uri.parse('$baseUrl/api$path');

  Future<Map<String, dynamic>?> _json(Future<http.Response> call) async {
    final r = await call.timeout(const Duration(seconds: 20));
    if (r.statusCode != 200) return null;
    return jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
  }

  Future<SparksStatus?> status(String learnerId) async {
    final sparks = await _json(_http.get(_uri('/learners/$learnerId/sparks')));
    if (sparks == null) return null;
    final billing = await _json(_http.get(_uri('/learners/$learnerId/billing')));
    return SparksStatus.fromJson(sparks, billing: billing);
  }

  /// After a purchase: the server re-reads RevenueCat and applies anything new.
  /// -> what was granted, e.g. [{kind: sparks, amount: 50}].
  Future<(SparksStatus?, List<Map<String, dynamic>>)> sync(String learnerId, {String? examId}) async {
    final r = await _json(_http.post(
      _uri('/learners/$learnerId/billing/sync'),
      headers: const {'content-type': 'application/json'},
      body: jsonEncode({if (examId != null) 'exam_id': examId}),
    ));
    if (r == null) return (null, const <Map<String, dynamic>>[]);
    final status = SparksStatus.fromJson(r['sparks'] as Map<String, dynamic>,
        billing: r['billing'] as Map<String, dynamic>?);
    final granted = [for (final g in (r['granted'] as List? ?? const [])) (g as Map).cast<String, dynamic>()];
    return (status, granted);
  }
}

/// The learner's Sparks on screen, kept current from the server and from the
/// chat's `sparks` / `sparks_reward` frames. Everything here is best-effort:
/// a server without Sparks (or a hiccup) leaves the last known numbers.
class SparksState extends ChangeNotifier {
  SparksState({required this.api, required this.billing}) {
    _changes = PaywallHub.changes.listen((_) => _scheduleRefresh());
  }

  StreamSubscription<void>? _changes;
  bool _refreshQueued = false;

  /// Coalesces a burst of changes into one re-read (no timers: a pending
  /// Timer would outlive widget tests).
  void _scheduleRefresh() {
    if (_refreshQueued) return;
    _refreshQueued = true;
    scheduleMicrotask(() {
      _refreshQueued = false;
      refresh(gainNote: 'learning paid back');
    });
  }

  final SparksApi api;
  final Billing billing;

  String? _learnerId;
  SparksStatus? status;

  /// Short-lived notes for a snackbar: "+2 Sparks, quiz passed".
  final _notes = StreamController<String>.broadcast();
  Stream<String> get notes => _notes.stream;

  Future<void> signedIn(String learnerId) async {
    _learnerId = learnerId;
    await billing.identify(learnerId);
    await refresh();
  }

  Future<void> signedOut() async {
    _learnerId = null;
    status = null;
    notifyListeners();
    await billing.reset();
  }

  /// Re-read the balance and plan. With [gainNote], a balance that went UP
  /// (a passed quiz, say) is announced: "+2 Sparks: quiz passed".
  Future<void> refresh({String? gainNote}) async {
    final id = _learnerId;
    if (id == null) return;
    try {
      final before = status?.balance;
      final fresh = await api.status(id);
      if (fresh != null && id == _learnerId) {
        status = fresh;
        notifyListeners();
        if (gainNote != null && before != null && fresh.balance > before && !_notes.isClosed) {
          _notes.add('+${fresh.balance - before} Sparks: $gainNote');
        }
      }
    } catch (_) {}
  }

  /// An answer was charged (chat `sparks` frame).
  void charged(int balance) {
    final s = status;
    if (s != null) {
      status = s.withBalance(balance);
      notifyListeners();
    } else {
      refresh();
    }
  }

  /// Learning paid back (chat `sparks_reward` frame, or a passed quiz).
  void rewarded(String reason, int amount) {
    if (amount <= 0) return;
    _notes.add('+$amount Sparks: ${rewardLabel(reason)}');
    refresh();
  }

  static String rewardLabel(String reason) => switch (reason) {
        'unit_quiz_passed' => 'quiz passed',
        'mock_test_passed' => 'mock test passed',
        'lesson_completed' => 'lesson finished',
        'study_streak' => 'study streak',
        _ => reason.replaceAll('_', ' '),
      };

  /// Run a purchase and let the server apply it. -> a line to show.
  Future<String> buy(Future<PurchaseOutcome> Function() purchase, {String? examId}) async {
    final outcome = await purchase();
    switch (outcome) {
      case PurchaseOutcome.cancelled:
        return 'No purchase made.';
      case PurchaseOutcome.unavailable:
        return billing.supported ? 'That is not available right now.' : billing.unsupportedReason;
      case PurchaseOutcome.failed:
        return 'The purchase did not go through.';
      case PurchaseOutcome.purchased:
        break;
    }
    return afterPurchase(examId: examId);
  }

  /// Ask the server to re-read RevenueCat now, rather than wait for its webhook.
  Future<String> afterPurchase({String? examId}) async {
    final id = _learnerId;
    if (id == null) return 'Signed out.';
    try {
      final (fresh, granted) = await api.sync(id, examId: examId);
      if (fresh != null) {
        status = fresh;
        notifyListeners();
      } else {
        await refresh();
      }
      for (final g in granted) {
        if (g['kind'] == 'sparks') return '+${g['amount']} Sparks added.';
        if (g['kind'] == 'exam_pass') return 'Exam Pass active until your exam.';
      }
      return status?.isPaid == true ? 'You are on ${status!.planName}.' : 'Purchase received.';
    } catch (_) {
      return 'Purchase received. It may take a moment to show.';
    }
  }

  @override
  void dispose() {
    _changes?.cancel();
    _notes.close();
    super.dispose();
  }
}
