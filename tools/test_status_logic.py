from __future__ import annotations

from app.status_logic import detect_important_events, render_dashboard


def snap(available, trains):
    return {"available": available, "trains": trains}


def train(key, count, plus=False):
    return {
        "key": key,
        "owner": "نورالرضا",
        "train_number": "524",
        "depart_time": "17:10",
        "capacity_text": f"{count}{'+' if plus else ''} بلیت",
        "capacity_count": count,
        "capacity_plus": plus,
        "price_text": "33,363,000",
    }


def main():
    # 1) No ticket -> ticket appears: MUST notify.
    e = detect_important_events(
        "1405/06/27",
        snap(False, []),
        snap(True, [train("A", 20, True)]),
    )
    assert [x.kind for x in e] == ["became_available"]

    # 2) Same train 20+ -> 18: dashboard changes, NO notification.
    e = detect_important_events(
        "1405/06/27",
        snap(True, [train("A", 20, True)]),
        snap(True, [train("A", 18)]),
    )
    assert e == []

    # 3) 18 -> 9: MUST notify once for low capacity.
    e = detect_important_events(
        "1405/06/27",
        snap(True, [train("A", 18)]),
        snap(True, [train("A", 9)]),
    )
    assert [x.kind for x in e] == ["capacity_low"]

    # 4) 9 -> 8: still low, NO repeated low-capacity notification.
    e = detect_important_events(
        "1405/06/27",
        snap(True, [train("A", 9)]),
        snap(True, [train("A", 8)]),
    )
    assert e == []

    # 5) Existing date gets a genuinely new train: MUST notify.
    e = detect_important_events(
        "1405/06/27",
        snap(True, [train("A", 20, True)]),
        snap(True, [train("A", 20, True), train("B", 15)]),
    )
    assert [x.kind for x in e] == ["new_train"]

    dashboard = render_dashboard(
        watch_id=1,
        origin="تهران",
        destination="شیراز",
        passengers=1,
        passenger_type_label="مسافرین عادی",
        dates=["1405/06/27", "1405/06/28"],
        snapshots={
            "1405/06/27": snap(True, [train("A", 9)]),
            "1405/06/28": snap(False, []),
        },
    )

    print("همه تست‌های منطق اعلان با موفقیت پاس شدند.\n")
    print(dashboard)


if __name__ == "__main__":
    main()
