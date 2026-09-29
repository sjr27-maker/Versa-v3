import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart';

import 'identity.dart';

/// The Firebase project this build signs in against, from build-time
/// defines -- nothing here is secret (Firebase client config is public by
/// design; the server is what checks tokens):
///
///   flutter run --dart-define-from-file=config/firebase.json
///
/// See app/config/firebase.example.json for the keys.
class FirebaseConfig {
  static const apiKey = String.fromEnvironment('FIREBASE_API_KEY');
  static const projectId = String.fromEnvironment('FIREBASE_PROJECT_ID');
  static const senderId = String.fromEnvironment('FIREBASE_MESSAGING_SENDER_ID');
  static const authDomain = String.fromEnvironment('FIREBASE_AUTH_DOMAIN');
  static const webAppId = String.fromEnvironment('FIREBASE_WEB_APP_ID');
  static const androidAppId = String.fromEnvironment('FIREBASE_ANDROID_APP_ID');
  static const androidApiKey = String.fromEnvironment('FIREBASE_ANDROID_API_KEY');
  static const iosAppId = String.fromEnvironment('FIREBASE_IOS_APP_ID');
  static const iosApiKey = String.fromEnvironment('FIREBASE_IOS_API_KEY');

  /// Options for this platform, or null when this build has none or the
  /// platform has no Firebase Auth (Windows / Linux / macOS desktop).
  static FirebaseOptions? forThisPlatform() {
    if (projectId.isEmpty || apiKey.isEmpty) return null;
    String appId;
    String key = apiKey;
    if (kIsWeb) {
      appId = webAppId;
    } else if (defaultTargetPlatform == TargetPlatform.android) {
      appId = androidAppId;
      if (androidApiKey.isNotEmpty) key = androidApiKey;
    } else if (defaultTargetPlatform == TargetPlatform.iOS) {
      appId = iosAppId;
      if (iosApiKey.isNotEmpty) key = iosApiKey;
    } else {
      return null;
    }
    if (appId.isEmpty) return null;
    return FirebaseOptions(
      apiKey: key,
      appId: appId,
      messagingSenderId: senderId,
      projectId: projectId,
      authDomain: authDomain.isEmpty ? '$projectId.firebaseapp.com' : authDomain,
    );
  }
}

class FirebaseIdentity implements IdentityService {
  FirebaseIdentity._(this._auth);

  final FirebaseAuth _auth;

  /// Firebase when this build and platform have it, otherwise [NoIdentity].
  static Future<IdentityService> create() async {
    final options = FirebaseConfig.forThisPlatform();
    if (options == null) return const NoIdentity();
    try {
      final app = Firebase.apps.isEmpty ? await Firebase.initializeApp(options: options) : Firebase.app();
      return FirebaseIdentity._(FirebaseAuth.instanceFor(app: app));
    } catch (e) {
      debugPrint('versa: Firebase could not start ($e) -- Google/email sign-in is off');
      return const NoIdentity();
    }
  }

  @override
  bool get available => true;

  @override
  Future<String?> currentIdToken() async => _auth.currentUser?.getIdToken();

  Future<String> _token(UserCredential credential) async {
    final token = await credential.user?.getIdToken();
    if (token == null) throw IdentityException('Signing in didn\'t finish. Try again.');
    return token;
  }

  @override
  Future<String> signInWithGoogle() async {
    final provider = GoogleAuthProvider()..setCustomParameters({'prompt': 'select_account'});
    try {
      final credential =
          kIsWeb ? await _auth.signInWithPopup(provider) : await _auth.signInWithProvider(provider);
      return await _token(credential);
    } on FirebaseAuthException catch (e) {
      throw _friendly(e);
    }
  }

  @override
  Future<String> signInWithEmail(String email, String password) async {
    try {
      return await _token(await _auth.signInWithEmailAndPassword(email: email.trim(), password: password));
    } on FirebaseAuthException catch (e) {
      throw _friendly(e);
    }
  }

  @override
  Future<String> createAccountWithEmail(String email, String password) async {
    try {
      final credential = await _auth.createUserWithEmailAndPassword(email: email.trim(), password: password);
      // Best effort: a verification mail. Sign-in doesn't wait for it.
      credential.user?.sendEmailVerification().ignore();
      return await _token(credential);
    } on FirebaseAuthException catch (e) {
      throw _friendly(e);
    }
  }

  @override
  Future<void> sendPasswordReset(String email) async {
    try {
      await _auth.sendPasswordResetEmail(email: email.trim());
    } on FirebaseAuthException catch (e) {
      throw _friendly(e);
    }
  }

  @override
  Future<void> signOut() => _auth.signOut();

  static IdentityException _friendly(FirebaseAuthException e) {
    switch (e.code) {
      case 'popup-closed-by-user':
      case 'cancelled-popup-request':
      case 'web-context-canceled':
      case 'canceled':
        return IdentityException('Sign-in was cancelled.', cancelled: true);
      case 'invalid-email':
        return IdentityException('That email address doesn\'t look right.');
      case 'user-disabled':
        return IdentityException('This account has been disabled.');
      case 'user-not-found':
      case 'wrong-password':
      case 'invalid-credential':
        return IdentityException('Wrong email or password.');
      case 'email-already-in-use':
        return IdentityException('There\'s already an account with that email. Sign in instead.');
      case 'weak-password':
        return IdentityException('Choose a longer password (at least 6 characters).');
      case 'account-exists-with-different-credential':
        return IdentityException('That email already signs in another way. Try Google, or email and password.');
      case 'network-request-failed':
        return IdentityException('No internet connection.');
      case 'too-many-requests':
        return IdentityException('Too many tries. Wait a minute and try again.');
      case 'operation-not-allowed':
        return IdentityException('This sign-in method isn\'t switched on for Versa yet.');
      default:
        return IdentityException(e.message ?? 'Sign-in failed (${e.code}).');
    }
  }
}
