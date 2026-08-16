import 'dart:async';

import 'package:riverpod_annotation/riverpod_annotation.dart';

import 'local_preferences_repository.dart';

part 'onboarding_provider.g.dart';

@riverpod
Future<bool> serverSetupCompleted(Ref ref) async {
  final repository = ref.watch(localPreferencesRepositoryProvider);
  try {
    // Native plugin initialization must never keep the app on the splash/loading
    // screen indefinitely. If preferences cannot be loaded, fall back to the
    // server setup page so the user can still recover/configure the app.
    return await repository
        .loadServerSetupCompleted()
        .timeout(const Duration(seconds: 5));
  } catch (_) {
    return false;
  }
}

@Riverpod(keepAlive: true)
OnboardingController onboardingController(Ref ref) {
  return OnboardingController(ref);
}

class OnboardingController {
  OnboardingController(this._ref);

  final Ref _ref;

  Future<void> markServerSetupCompleted() async {
    final repository = _ref.read(localPreferencesRepositoryProvider);
    await repository.saveServerSetupCompleted(true);
    if (_ref.mounted) {
      _ref.invalidate(serverSetupCompletedProvider);
    }
  }
}
