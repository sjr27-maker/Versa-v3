"""Rooms (EXPERIMENTAL, branch experiment/rooms): study a topic together in a
group chat where Versa is just another member.

One person creates a room with their name, a room code and a topic (a
search, a web link or a PDF -- outlined by the same nodes Learn a topic
uses). Others join with their name and the code, from any device that can
reach the server. The room is a WhatsApp-style group chat:

    people talk to each other, and to Versa ("@Versa ...")
    Versa watches everything and decides each time whether to speak, to
    whom (everyone or one person, optionally privately) and how: a chat
    line, a short teaching explanation (acted out on the stage), a question,
    clickable options, a task for someone, or marking someone's task done.

Modules
    store.py   the tables (migration rooms_001_rooms.sql), insert-only
    nodes.py   RoomDirector: the "should I step in, and how?" call
    hub.py     live connections, one Versa turn at a time per room, and
               applying Versa's actions
    router.py  the REST + WebSocket routes

Kept apart from the rest of Versa on purpose, so it can be lifted into its
own project (see scripts/export_rooms.py):
    * no core table is referenced -- a member is a name inside one room;
    * the personal memory layer (learner facts, claims, thinking style) is
      never read or written from here;
    * model calls are audited to its own `room_node_calls` table, the same
      payload `node_calls` carries (CLAUDE.md invariant 2);
    * everything is append-only (CLAUDE.md invariant 12).
"""

from versa.rooms.hub import RoomHub
from versa.rooms.router import build_rooms_router

__all__ = ["RoomHub", "build_rooms_router"]
