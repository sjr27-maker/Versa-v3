/// Who the person is, as far as an identity provider is concerned.
///
/// The app never trusts itself about this: it gets an ID token from the
/// provider (Firebase: Google, or email and password) and hands it to the
/// server (`POST /api/auth/firebase`), which checks it and answers with a
/// Versa session token. See src/versa/accounts.py.
abstract class IdentityService {
  /// Whether Google / email sign-in can be offered on this device at all
  /// (Firebase is set up in this build and supports this platform).
  bool get available;

  /// A Firebase ID token for the signed-in account, or null if none.
  Future<String?> currentIdToken();

  /// Google, in a popup (web) or the system browser (Android / iOS).
  Future<String> signInWithGoogle();

  Future<String> signInWithEmail(String email, String password);

  Future<String> createAccountWithEmail(String email, String password);

  Future<void> sendPasswordReset(String email);

  Future<void> signOut();
}

class IdentityException implements Exception {
  IdentityException(this.message, {this.cancelled = false});
  final String message;

  /// The person closed the Google window: not an error to show.
  final bool cancelled;

  @override
  String toString() => message;
}

/// No provider on this build or platform (e.g. the Windows desktop build):
/// only the testers' name sign-in is offered.
class NoIdentity implements IdentityService {
  const NoIdentity();

  @override
  bool get available => false;

  static Never _unavailable() => throw IdentityException('Google and email sign-in aren\'t available here.');

  @override
  Future<String?> currentIdToken() async => null;

  @override
  Future<String> signInWithGoogle() async => _unavailable();

  @override
  Future<String> signInWithEmail(String email, String password) async => _unavailable();

  @override
  Future<String> createAccountWithEmail(String email, String password) async => _unavailable();

  @override
  Future<void> sendPasswordReset(String email) async => _unavailable();

  @override
  Future<void> signOut() async {}
}
