import 'package:flutter/material.dart';

import 'room_api.dart';
import 'room_screen.dart';
import 'rooms_home_screen.dart';

/// Study with others lives inside the Modes tab with its own navigation stack
/// (rooms list -> create / room), so switching tabs never leaves a room.
class RoomsRoot extends StatefulWidget {
  const RoomsRoot({super.key});

  @override
  State<RoomsRoot> createState() => _RoomsRootState();
}

class _RoomsRootState extends State<RoomsRoot> {
  final _navigator = GlobalKey<NavigatorState>();

  @override
  Widget build(BuildContext context) {
    return Navigator(
      key: _navigator,
      onGenerateRoute: (_) => roomRoute((_) => const RoomsHomeScreen()),
    );
  }
}

/// A calm fade-and-rise page transition (the same one the topic screens use).
PageRoute<T> roomRoute<T>(WidgetBuilder builder) => PageRouteBuilder<T>(
      pageBuilder: (context, _, _) => builder(context),
      transitionDuration: const Duration(milliseconds: 260),
      reverseTransitionDuration: const Duration(milliseconds: 200),
      transitionsBuilder: (context, animation, _, child) {
        final curved = CurvedAnimation(parent: animation, curve: Curves.easeOutCubic);
        return FadeTransition(
          opacity: curved,
          child: SlideTransition(
            position: Tween(begin: const Offset(0, 0.03), end: Offset.zero).animate(curved),
            child: child,
          ),
        );
      },
    );

Future<void> pushRoom(BuildContext context, RoomMembership membership) =>
    Navigator.of(context).push(roomRoute((_) => RoomScreen(membership: membership)));
