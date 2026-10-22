"""Live gate scenarios: end-to-end runs printed like a gate console.
ask(prompt) is input() in the menu and an auto-answer (defaults) in 'all' mode."""
import random
import zlib
from checks import T0, at
from sim import Lot, ManualEntryRequired, PaymentFailed, PlateMismatch, OfflineError

DEFAULT_PLATE = "29A-888.88"


def say(tag, msg):
    print(f"[{tag}] {msg}")


def auto(prompt):  # non-interactive answer: take the default
    print(prompt)
    return ""


def quality(plate):  # simulated camera image quality for this plate (deterministic per plate)
    return random.Random(plate).uniform(0.78, 0.995)


def entry(lot, ask, t, vtype="car"):
    plate = ask(f"Enter vehicle plate [Default: {DEFAULT_PLATE}]: ").strip() or DEFAULT_PLATE
    q = quality(plate)
    frame = {"plate": plate, "quality": q}
    try:
        plate = lot.identify(frame, t)
        say("ALPR CAM", f"Detected Plate: {plate} (Confidence {q:.1%})")
    except ManualEntryRequired as e:
        say("ALPR CAM", f"Cannot accept the reading: {e}")
        plate = lot.identify(frame, t, manual=ask(f"[ENTRY GATE] Attendant, type the plate [Default: {plate}]: ").strip() or plate)
        say("ENTRY GATE", f"Manual entry accepted: {plate}")
    try:
        tk = lot.enter(plate, vtype, t)
    except ValueError as e:
        say("ENTRY GATE", f"Rejected: {e}")
        return None
    if tk is None:
        say("ENTRY GATE", "Sorry, no free slot for this vehicle type.")
        return None
    say("ENTRY GATE", f"Welcome! Assigned Spot {tk.slot.name} ({lot.path_of(tk.slot).split(' > ')[1]}), {tk.slot.dist} m from the gate.")
    say("TICKET", f"Issued {tk.id} | Allocated Spot: {tk.slot.name}")
    lot.open_gate("entry", t)
    say("BARRIER", "Gate State: OPEN")
    lot.close_gate("entry", t)
    say("BARRIER", "Car transit complete. Barrier State: CLOSED")
    return tk


def leave(lot, ask, t, seen=None):
    default = next(iter(lot.tickets), None)
    if default is None:
        return say("EXIT GATE", "No vehicle inside.")
    key = ask(f"Scan ticket ID or plate [Default: {default}]: ").strip() or default
    tk = next((x for x in lot.tickets.values() if key in (x.id, x.plate)), None)
    if tk is None:
        return say("EXIT GATE", f"Ticket {key} not found: please see the attendant.")
    say("EXIT GATE", f"Ticket {tk.id} scanned. Plate {tk.plate}, spot {tk.slot.name}")
    say("EXIT CAM", f"Plate read: {seen or tk.plate}" + ("" if (seen or tk.plate) == tk.plate else f" - does NOT match ticket plate {tk.plate}"))
    say("PRICING", f"Entry {tk.since:%H:%M}, exit {t:%H:%M}")
    for line in lot.breakdown(tk, t):
        say("PRICING", "  " + line)
    fee = lot.fee(tk, t)
    method = ask("Payment method cash/card/online [Default: card]: ").strip() or "card"
    if method == "online" and ask("Payment gateway online? y/n [Default: y]: ").strip().lower() == "n":
        lot.gateway_up = False
    try:
        try:
            lot.exit(tk.plate, t, method=method, seen=seen or tk.plate)
        except PaymentFailed as e:
            say("PAYMENT", f"DECLINED: {e}")
            method = "cash"
            lot.exit(tk.plate, t, method=method, seen=seen or tk.plate)
    except PlateMismatch as e:
        return say("EXIT GATE", f"HOLD: {e}")
    except ValueError as e:
        return say("PAYMENT", str(e))
    say("PAYMENT", f"{method.upper()} APPROVED: {fee} VND")
    say("RECEIPT", f"RCT-{zlib.crc32((tk.id + 'R').encode()):08X} | {tk.plate} | {fee} VND | {t:%Y-%m-%d %H:%M}")
    lot.open_gate("exit", t)
    say("BARRIER", "Gate State: OPEN")
    lot.close_gate("exit", t)
    say("BARRIER", "Car transit complete. Barrier State: CLOSED")


def s_entry(ask):
    print("--- SCENARIO 1: Vehicle Entry with ALPR ---")
    entry(Lot(), ask, T0)


def s_exit(ask):
    print("--- SCENARIO 2: Vehicle Exit and Payment ---")
    lot = Lot()
    lot.enter(DEFAULT_PLATE, "car", T0.replace(hour=16, minute=20))  # parked earlier; the stay crosses the 17:00 peak
    leave(lot, ask, T0.replace(hour=19, minute=5))


def s_journey(ask):
    print("--- SCENARIO 3: Reservation to Exit Journey ---")
    lot, plate = Lot(), "29A-777.77"
    s = lot.reserve(plate, "car", at(minutes=30))
    say("APP", f"Driver reserves a car slot for {at(minutes=30):%H:%M}")
    say("RESERVE", f"Slot {s.name} held until {at(minutes=45):%H:%M}")
    t = at(minutes=32)
    say("GATE", f"{t:%H:%M} the vehicle arrives")
    entry(lot, lambda _: plate, t)
    r = {x.name: x.state == "Occupied" for x in lot.root.slots()}
    say("SENSOR", f"Scan of {len(r)} slots: {len(lot.scan(r, at(hours=1)))} alerts")
    say("PARKING", "The vehicle stays for 3 hours")
    leave(lot, auto, at(hours=3, minutes=32))


def s_drill(ask):
    print("--- SCENARIO 4: Emergency Evacuation Drill ---")
    lot = Lot()
    for p in ("29A-111", "29A-222", "29A-333"):
        lot.enter(p, "car", T0)
    lot.reserve("VIP-1", "car", at(minutes=30))
    say("FIRE ALARM", "Signal received")
    n = lot.evacuate(at(minutes=20), source="fire_alarm")
    say("SYSTEM", f"Evacuation started: {n} slots blocked, reservations cancelled, barriers {lot.barriers.upper()}")
    say("ENTRY GATE", "New vehicle: " + ("rejected, evacuation in progress" if lot.enter("29A-444", "car", at(minutes=21)) is None else "accepted?"))
    for p in ("29A-111", "29A-222", "29A-333"):
        say("EXIT GATE", f"{p} leaves, fee {lot.exit(p, at(minutes=22))} VND")
    lot.end_evacuation(at(minutes=40), "admin")
    say("SYSTEM", f"Evacuation ended by administrator: barriers {lot.barriers.upper()}, lot normal, {sum(s.state == 'Free' for s in lot.root.slots())} slots free")


def s_swap(ask):
    print("--- SCENARIO 5: Ticket Swap at the Exit ---")
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    lot.enter("29A-222", "car", T0)
    t = at(hours=2)
    say("EXIT GATE", "Driver of 29A-222 presents the ticket of 29A-111")
    leave(lot, lambda p: "29A-111" if p.startswith("Scan") else "cash", t, seen="29A-222")
    say("ATTENDANT", "Checks the vehicle by eye and approves the exit")
    say("EXIT GATE", f"Released with attendant approval, fee {lot.exit('29A-111', t, seen='29A-222', approver='attendant')} VND")


def s_offline(ask):
    print("--- SCENARIO 6: Network Outage at the Gates ---")
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    lot.set_network(False, at(minutes=30))
    say("SYSTEM", "Central server unreachable: gates switch to local mode")
    entry(lot, lambda _: "29A-222", at(minutes=31))
    try:
        lot.exit("29A-111", at(hours=1), method="online")
    except PaymentFailed as e:
        say("PAYMENT", f"DECLINED: {e}")
    leave(lot, lambda p: "29A-111" if p.startswith("Scan") else "cash", at(hours=1, minutes=31))
    try:
        lot.reserve("R-1", "car", at(hours=2))
    except OfflineError as e:
        say("APP", f"Reservation refused: {e}")
    say("SYSTEM", f"Connection back: {lot.set_network(True, at(hours=2))} records synchronized")


def s_ev(ask):
    print("--- SCENARIO 7: EV Charging and Monthly Pass ---")
    lot = Lot()
    lot.add_zone("F2", "E", "ev", 2, "admin", T0)
    lot.add_pass("29A-555", "monthly", at(days=30), "admin", T0)
    tk = entry(lot, lambda _: "51H-888.88", T0, "ev")
    say("CHARGER", f"{tk.slot.name}: {lot.charge('51H-888.88', 12.5, at(hours=1))} VND of energy so far")
    leave(lot, auto, at(hours=2))
    entry(lot, lambda _: "29A-555", at(hours=3))
    leave(lot, auto, at(hours=8))


LIVE = [("Vehicle entry with ALPR", s_entry), ("Vehicle exit and payment", s_exit),
        ("Reservation to exit journey", s_journey), ("Emergency evacuation drill", s_drill),
        ("Ticket swap at the exit", s_swap), ("Network outage at the gates", s_offline), ("EV charging and monthly pass", s_ev)]


def menu(ask=input):
    while True:
        print("\n=== Live gate scenarios ===")
        for i, (title, _) in enumerate(LIVE, 1):
            print(f"  {i}. {title}")
        print("  0. Back")
        c = ask("Choose: ").strip()
        if c == "0":
            return
        if c.isdigit() and 1 <= int(c) <= len(LIVE):
            print()
            LIVE[int(c) - 1][1](ask)
        else:
            print("Invalid choice.")
