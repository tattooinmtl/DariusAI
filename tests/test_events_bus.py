import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.events.bus import ActivityBus


def test_publish_stamps_time_and_keeps_history():
    bus = ActivityBus()
    bus.publish({"kind": "skill_learned", "id": "skill-1"})
    bus.publish({"kind": "skill_learned", "id": "skill-2"})
    recent = bus.recent()
    assert len(recent) == 2
    assert all("time" in e for e in recent)
    assert recent[0]["id"] == "skill-1"


def test_history_limit_drops_oldest():
    bus = ActivityBus(history_limit=3)
    for i in range(5):
        bus.publish({"kind": "x", "id": str(i)})
    ids = [e["id"] for e in bus.recent()]
    assert ids == ["2", "3", "4"]


async def _subscriber_gets_events():
    bus = ActivityBus()
    q = bus.subscribe()
    bus.publish({"kind": "node_activated", "id": "n1"})
    event = await asyncio.wait_for(q.get(), timeout=1)
    assert event["id"] == "n1"
    bus.unsubscribe(q)
    return True


def test_subscriber_receives_live_events():
    assert asyncio.run(_subscriber_gets_events())


async def _unsubscribed_queue_gets_nothing():
    bus = ActivityBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish({"kind": "node_activated", "id": "n2"})
    assert q.empty()
    return True


def test_unsubscribe_stops_delivery():
    assert asyncio.run(_unsubscribed_queue_gets_nothing())
