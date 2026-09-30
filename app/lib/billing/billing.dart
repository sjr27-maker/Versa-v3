import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:purchases_flutter/purchases_flutter.dart';
import 'package:purchases_ui_flutter/purchases_ui_flutter.dart';

/// What a purchase attempt ended as.
enum PurchaseOutcome { purchased, cancelled, failed, unavailable }

/// The store side of paying for Versa, through RevenueCat. The RevenueCat
/// customer is the Versa learner ([identify] logs in with the learner id), so
/// the server (src/versa/billing.py) can look the same person up.
///
/// Versa's server stays the judge of what a learner may do: after any
/// purchase the app asks it to re-read RevenueCat (SparksState.afterPurchase),
/// and the balance / plan on screen come from the server, not from here.
abstract class Billing {
  /// Purchases work on this device (Android / iOS with a RevenueCat key).
  bool get supported;

  /// Why [supported] is false, in plain words.
  String get unsupportedReason;

  Future<void> identify(String learnerId);
  Future<void> reset();

  /// RevenueCat's Versa Plus paywall (the `default` offering).
  Future<PurchaseOutcome> showPlusPaywall();

  /// Buy the first package of a one-time offering: `exam_pass` or `sparks`
  /// -- or `default:annual`, Versa Plus paid yearly.
  Future<PurchaseOutcome> buyOffering(String offeringId);

  /// Restore earlier purchases (required by the App Store).
  Future<PurchaseOutcome> restore();

  /// RevenueCat's Customer Center: manage / cancel a subscription.
  Future<void> manageSubscription();

  /// Price shown on a button, e.g. "$2.99", or null if unknown.
  Future<String?> priceOf(String offeringId);
}

/// No purchases here (Windows, web without Web Billing, or no key): the app
/// still shows Sparks and plans, and says where to buy.
class NoBilling implements Billing {
  const NoBilling([this.unsupportedReason = 'Purchases are available in the Android and iOS app.']);

  @override
  final String unsupportedReason;

  @override
  bool get supported => false;

  @override
  Future<void> identify(String learnerId) async {}

  @override
  Future<void> reset() async {}

  @override
  Future<PurchaseOutcome> showPlusPaywall() async => PurchaseOutcome.unavailable;

  @override
  Future<PurchaseOutcome> buyOffering(String offeringId) async => PurchaseOutcome.unavailable;

  @override
  Future<PurchaseOutcome> restore() async => PurchaseOutcome.unavailable;

  @override
  Future<void> manageSubscription() async {}

  @override
  Future<String?> priceOf(String offeringId) async => null;
}

class RevenueCatBilling implements Billing {
  RevenueCatBilling._(this._apiKey, {this.onWeb = false});

  final String _apiKey;

  /// Running in a browser: RevenueCat's paywall and Customer Center screens
  /// (purchases_ui_flutter) don't exist there, so Plus is bought straight
  /// from the `default` offering through RevenueCat's web checkout.
  final bool onWeb;
  bool _configured = false;

  /// The RevenueCat public SDK key for this platform, passed at build time:
  ///   flutter run --dart-define=RC_TEST_KEY=test_...        (Test Store, any platform)
  ///   flutter build web --dart-define=RC_WEB_KEY=rcb_...     (Web Billing)
  ///   flutter build apk --dart-define=RC_GOOGLE_KEY=goog_... (Google Play)
  ///   flutter build ios --dart-define=RC_APPLE_KEY=appl_...  (App Store)
  /// A Test Store key must never ship: RevenueCat refuses it in release
  /// store builds.
  ///
  /// On the web (a laptop browser) purchases go through RevenueCat's web
  /// checkout: the Test Store key works there too, or a Web Billing key.
  /// Windows / macOS desktop builds have no purchases.
  static Billing create() {
    const test = String.fromEnvironment('RC_TEST_KEY');
    const web = String.fromEnvironment('RC_WEB_KEY');
    const google = String.fromEnvironment('RC_GOOGLE_KEY');
    const apple = String.fromEnvironment('RC_APPLE_KEY');
    if (kIsWeb) {
      final key = test.isNotEmpty ? test : web;
      if (key.isEmpty) {
        return const NoBilling('This web build has no RevenueCat key: build it with '
            '--dart-define=RC_TEST_KEY=... (see app/README.md).');
      }
      return RevenueCatBilling._(key, onWeb: true);
    }
    final platform = defaultTargetPlatform;
    final mobile = platform == TargetPlatform.android || platform == TargetPlatform.iOS;
    if (!mobile) return const NoBilling();
    final key = test.isNotEmpty
        ? test
        : (platform == TargetPlatform.iOS ? apple : google);
    if (key.isEmpty) {
      return const NoBilling('This build has no RevenueCat key (see lib/billing/billing.dart).');
    }
    return RevenueCatBilling._(key);
  }

  @override
  bool get supported => true;

  @override
  String get unsupportedReason => '';

  Future<void> _ensureConfigured() async {
    if (_configured) return;
    if (kDebugMode) await Purchases.setLogLevel(LogLevel.info);
    await Purchases.configure(PurchasesConfiguration(_apiKey));
    _configured = true;
  }

  @override
  Future<void> identify(String learnerId) async {
    try {
      await _ensureConfigured();
      await Purchases.logIn(learnerId);
    } catch (e) {
      debugPrint('RevenueCat logIn failed: $e');
    }
  }

  @override
  Future<void> reset() async {
    if (!_configured) return;
    try {
      await Purchases.logOut();
    } catch (e) {
      debugPrint('RevenueCat logOut failed: $e'); // logOut of an anonymous user throws
    }
  }

  PurchaseOutcome _fromError(PlatformException e) {
    final code = PurchasesErrorHelper.getErrorCode(e);
    if (code == PurchasesErrorCode.purchaseCancelledError) return PurchaseOutcome.cancelled;
    debugPrint('RevenueCat purchase failed: $code ${e.message}');
    return PurchaseOutcome.failed;
  }

  @override
  Future<PurchaseOutcome> showPlusPaywall() async {
    if (onWeb) return buyOffering('default');
    try {
      await _ensureConfigured();
      final result = await RevenueCatUI.presentPaywall(displayCloseButton: true);
      return switch (result) {
        PaywallResult.purchased || PaywallResult.restored => PurchaseOutcome.purchased,
        PaywallResult.error => PurchaseOutcome.failed,
        _ => PurchaseOutcome.cancelled,
      };
    } on PlatformException catch (e) {
      return _fromError(e);
    }
  }

  /// The package to buy for [offeringId]. `default:annual` is Plus paid
  /// yearly (the `default` offering's annual package; no free month) --
  /// null when the offering has no annual package.
  Future<Package?> _firstPackage(String offeringId) async {
    await _ensureConfigured();
    final offerings = await Purchases.getOfferings();
    final parts = offeringId.split(':');
    final offering = offerings.getOffering(parts.first);
    if (offering == null) return null;
    if (parts.length > 1 && parts[1] == 'annual') return offering.annual;
    // Plus (`default`): the monthly plan, the one with the free month.
    if (offering.monthly != null) return offering.monthly;
    final packages = offering.availablePackages;
    return packages.isEmpty ? null : packages.first;
  }

  @override
  Future<PurchaseOutcome> buyOffering(String offeringId) async {
    try {
      final package = await _firstPackage(offeringId);
      if (package == null) return PurchaseOutcome.unavailable;
      await Purchases.purchasePackage(package);
      return PurchaseOutcome.purchased;
    } on PlatformException catch (e) {
      return _fromError(e);
    }
  }

  @override
  Future<PurchaseOutcome> restore() async {
    try {
      await _ensureConfigured();
      await Purchases.restorePurchases();
      return PurchaseOutcome.purchased;
    } on PlatformException catch (e) {
      return _fromError(e);
    }
  }

  @override
  Future<void> manageSubscription() async {
    if (onWeb) return; // no Customer Center on the web
    try {
      await _ensureConfigured();
      await RevenueCatUI.presentCustomerCenter();
    } on PlatformException catch (e) {
      debugPrint('RevenueCat Customer Center failed: ${e.message}');
    }
  }

  @override
  Future<String?> priceOf(String offeringId) async {
    try {
      return (await _firstPackage(offeringId))?.storeProduct.priceString;
    } catch (_) {
      return null;
    }
  }
}
