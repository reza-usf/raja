from __future__ import annotations

import tempfile
from pathlib import Path

from app.storage import Storage
from app.status_logic import detect_important_events


def snap(available, trains):
    return {"available": available, "trains": trains}


def train(key, count):
    return {
        "key": key,
        "owner": "نورالرضا",
        "train_number": "524",
        "depart_time": "17:10",
        "capacity_text": f"{count} بلیت",
        "capacity_count": count,
        "capacity_plus": False,
        "price_text": "33,363,000",
    }


def main():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "bot.sqlite3"
        s = Storage(str(db))
        wid = s.add_watch(
            chat_id=123,
            origin="تهران",
            destination="شیراز",
            date_from="1405/06/27",
            date_to="1405/06/31",
            passengers=1,
            passenger_type="normal",
            interval_seconds=60,
        )

        s.set_dashboard_message_id(wid, 999)
        watch = s.list_active(123)[0]
        assert watch.dashboard_message_id == 999

        old = snap(False, [])
        new = snap(True, [train("A", 20)])
        s.set_date_snapshot(wid, "1405/06/27", old, "old")
        assert s.get_date_snapshot(wid, "1405/06/27") == old

        events = detect_important_events(
            "1405/06/27",
            s.get_date_snapshot(wid, "1405/06/27"),
            new,
        )
        assert [e.kind for e in events] == ["became_available"]

        s.set_date_snapshot(wid, "1405/06/27", new, "new")
        assert s.get_date_snapshot(wid, "1405/06/27")["available"] is True

    print("Production dashboard storage/event tests passed.")


if __name__ == "__main__":
    main()
