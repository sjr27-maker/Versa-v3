# versa_app

A new Flutter project.

## Sparks and purchases (RevenueCat)

Purchases run through RevenueCat on Android, iOS and the web (`lib/billing/`). The public SDK key is passed at build time, never committed:

```
flutter pub get
flutter run -d <android-device> --dart-define=RC_TEST_KEY=test_...   # RevenueCat Test Store
flutter build web --release --dart-define=RC_TEST_KEY=test_...       # laptop browser, Test Store
flutter build web --release --dart-define=RC_WEB_KEY=rcb_...         # laptop browser, Web Billing
```

Store releases use `RC_GOOGLE_KEY` / `RC_APPLE_KEY`; a Test Store key is refused in store release builds. On the web there is no RevenueCat paywall screen (purchases_ui_flutter is mobile-only): "Start your free month" buys the `default` offering's monthly package through RevenueCat's web checkout, and Manage subscription is not available. The Windows desktop build has no purchases. Without a key the app still shows Sparks and plans, and the buy buttons explain why they can't buy.

The balance chip sits on Home and in the side rail; Settings has a Plan & Sparks card. Running out of Sparks (a chat `paywall` frame or an HTTP 402) opens the Sparks sheet: wait for the refill, Versa Plus (RevenueCat paywall), Exam Pass, or 50 Sparks. After a purchase the app calls `POST /api/learners/{id}/billing/sync` so the server applies it at once.
