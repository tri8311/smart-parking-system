"""Executable checks on the simulation: use-case scenarios (F4), test cases (F5), NFR simulation (F6), hazard mitigations (F7)."""
import random
import time
from datetime import datetime, timedelta, timezone
from sim import (Lot, Accounts, RATE, CHARGE_RATE, LOCAL, PaymentFailed, ManualEntryRequired, PlateMismatch, OfflineError, anpr, FREE, OCCUPIED, BLOCKED, RESERVED, show)
from rm import ids

T0 = datetime(2026, 10, 17, 10, 0, tzinfo=LOCAL)  # off-peak, so base-rate examples stay simple


def at(**kw):
    return T0 + timedelta(**kw)


def park(lot, plates, vtype="car"):
    return [lot.enter(p, vtype, T0) for p in plates]


def readings_of(lot):  # sensors that agree with the lot state
    return {s.name: s.state == OCCUPIED for s in lot.root.slots()}


def denied(fn):  # run fn, return the PermissionError text (fails if access was granted)
    try:
        fn()
    except PermissionError as e:
        return str(e)
    raise AssertionError("access was granted")


# ---- F4: one function per use case; returns (result per main-flow step, result per alternative flow) ----
def uc01():
    lot, p = Lot(), "29A-111"
    s = lot.pick(p, "car")
    tk = lot.enter(p, "car", T0)
    assert tk.slot is s and s.name == "A1"
    lot.open_gate("entry", T0)
    g1 = lot.gates["entry"]
    lot.close_gate("entry", T0)
    main = [f"{p} (car) at the entry gate", f"plate={p}, type=car", f"nearest free car slot: {s.name}",
            f"ticket {tk.id}, entry {T0:%H:%M}", f"show {s.name} ({lot.path_of(s)}); barrier {g1}, then {lot.gates['entry']} after transit"]
    lot2 = Lot()
    park(lot2, ["M-1", "M-2"], "moto")
    assert lot2.enter("M-3", "moto", T0) is None
    lot3 = Lot()
    lot3.reserve("VIP-1", "car", T0)
    assert lot3.enter("VIP-1", "car", T0).slot.name == "A1"
    try:
        lot.enter(p, "car", T0)
        raise AssertionError("duplicate plate accepted")
    except ValueError as e:
        a3 = f"rejected: {e}"
    return main, ["3rd moto: REJECTED 'zone full' (cars unaffected)", "VIP-1 gets its reserved slot A1", a3]


def uc02():
    lot, p = Lot(), "29A-111"
    tk = lot.enter(p, "car", T0)
    t = at(hours=2, minutes=30)
    f = lot.fee(tk, t)
    paid = lot.exit(p, t, method="card")
    lot.open_gate("exit", t)
    assert f == paid == 30000 and tk.slot.state == FREE and lot.gates["exit"] == "OPEN"
    main = [f"ticket {tk.id} presented", f"entry {T0:%H:%M}, exit {t:%H:%M} (2h30)", f"3 started hours x {RATE['car']} = {f} VND (off-peak)",
            f"paid {paid} VND by card", f"receipt printed; slot {tk.slot.name} is {tk.slot.state}; exit barrier {lot.gates['exit']}"]
    try:
        Lot().exit("NOPE", t)
    except LookupError as e:
        a1 = f"refused: {e}; driver sent to attendant (UC-06)"
    lot2 = Lot()
    tk2 = lot2.enter(p, "car", T0)
    try:
        lot2.exit(p, t, paid=False)
    except PaymentFailed:
        assert tk2.slot.state == OCCUPIED
    lot2.exit(p, t, method="card")
    lot3 = Lot()
    lot3.gateway_up = False
    tk3 = lot3.enter(p, "car", T0)
    try:
        lot3.exit(p, t, method="online")
        raise AssertionError("online payment accepted while the gateway is down")
    except PaymentFailed:
        assert tk3.slot.state == OCCUPIED
    lot3.exit(p, t, method="cash")
    return main, [a1, "payment failed: barrier stays closed, slot occupied; retry with card -> open",
                  "gateway down: online refused, slot occupied; driver pays cash at the booth -> open"]


def uc03():
    lot = Lot()
    s_free = lot.pick("VIP-1", "car")
    s = lot.reserve("VIP-1", "car", at(minutes=30))
    assert s is s_free and s.state == RESERVED and lot.enter("X-1", "car", T0).slot.name != s.name
    main = [f"car, arrival {at(minutes=30):%H:%M}", f"free slot found: {s.name}",
            f"{s.name} reserved for VIP-1 until {at(minutes=45):%H:%M}", "reservation confirmed"]
    lot2 = Lot()
    for i in range(4):
        assert lot2.reserve(f"R-{i}", "car", T0)
    assert lot2.reserve("R-5", "car", T0) is None
    lot3 = Lot()
    s3 = lot3.reserve("V", "car", at(minutes=30))
    assert lot3.expire(at(minutes=45)) == [] and lot3.expire(at(minutes=46)) == [s3.name] and s3.state == FREE
    return main, ["5th reservation: REJECTED (all 4 car slots reserved)", f"{s3.name} released at {at(minutes=46):%H:%M} (no-show over 15 min)"]


def uc04():
    lot = Lot()
    park(lot, ["29A-111", "29A-222", "29A-333"])
    s = lot.find("29A-333")
    assert s.name == "C1" and lot.find("NOPE") is None
    return ["plate 29A-333 entered", f"searched {len(lot.tickets)} active tickets", f"display: {lot.route(s)}"], ["'vehicle not found'"]


def uc05():
    lot, acc = Lot(), Accounts()
    role = acc.login("admin1", "admin123")
    n = lot.lock("A", role, T0)
    tk = lot.enter("29A-111", "car", T0)
    assert n == 2 and tk.slot.name == "C1"
    u = lot.lock("A", role, T0, lock=False)
    assert u == 2 and lot.root.get("A1").state == FREE
    main = [f"admin1 logged in, role {role}", "zone A selected", "action: lock, later unlock",
            f"lock: {n} free slots in A set to Blocked (new car went to {tk.slot.name}); unlock: {u} slots freed",
            f"changes logged ({', '.join(a['event'] for a in lot.audit if a['event'] in ('lock', 'unlock'))})"]
    lot2 = Lot()
    tk2 = lot2.enter("29A-111", "car", T0)
    lot2.lock("A", "admin", T0)
    assert tk2.slot.state == OCCUPIED
    lot2.exit("29A-111", at(hours=1))
    assert tk2.slot.state == BLOCKED
    lot3 = Lot()
    lot3.reserve("VIP-1", "car", at(minutes=30))
    lot3.lock("A", "admin", T0)
    assert lot3.root.get("A1").state == RESERVED and lot3.enter("VIP-1", "car", at(minutes=30)).slot.name == "A1"
    lot4 = Lot()
    lot4.scan({"B2": None}, T0)
    lot4.lock("B", "admin", T0)
    lot4.lock("B", "admin", T0, lock=False)
    assert lot4.root.get("B1").state == FREE and lot4.root.get("B2").state == BLOCKED
    a4 = f"attendant denied: {denied(lambda: Lot().lock('A', 'attendant', T0))}"
    return main, ["A1 stays Occupied while A locked; after exit it becomes Blocked", "A1 reserved for VIP-1 keeps its reservation; VIP-1 still gets A1",
                  "unlock: B1 freed, B2 stays Blocked because its sensor is faulty", a4]


def uc06():
    lot, p = Lot(), "29A-111"
    tk = lot.enter(p, "car", T0)
    fee = lot.lost_ticket(p, at(hours=5))
    assert fee == 24 * RATE["car"] + 50000 and tk.slot.state == FREE and lot.audit[-1]["event"] == "lost_ticket"
    main = [f"plate {p} entered", "identity and proof of ownership checked: accepted", f"entry record found, entry {T0:%H:%M}",
            f"fee = 24h x {RATE['car']} + 50000 penalty = {fee} VND", f"slot {tk.slot.name} freed; incident logged"]
    try:
        Lot().lost_ticket("NOPE", T0)
    except LookupError as e:
        a1 = f"{e}: escalate to the Parking Manager"
    lot2 = Lot()
    tk2 = lot2.enter(p, "car", T0)
    a2 = denied(lambda: lot2.lost_ticket(p, T0, proof=False))
    assert tk2.slot.state == OCCUPIED and lot2.audit[-1]["event"] == "lost_ticket_refused"
    return main, [a1, a2]


def uc07():  # ANPR
    lot = Lot()
    frame = {"plate": "29A-111", "quality": 0.95}
    plate = lot.identify(frame, T0)
    tk = lot.enter(plate, "car", T0)
    assert plate == "29A-111" and tk
    main = ["car stops at the entry gate", f"frame captured (image quality {frame['quality']:.0%})",
            f"plate read: {plate}, confidence {frame['quality']:.0%}", "confidence >= 80%: accepted, attendant not needed",
            f"entry continues: ticket {tk.id}, slot {tk.slot.name}"]
    bad = {"plate": "29A-222", "quality": 0.55}
    lot2 = Lot()
    try:
        lot2.identify(bad, T0)
        raise AssertionError("low confidence accepted")
    except ManualEntryRequired:
        pass
    p2 = lot2.identify(bad, T0, manual="29A-222")
    assert p2 == "29A-222"
    lot3 = Lot()
    try:
        lot3.identify(None, T0)
        raise AssertionError("offline camera accepted")
    except ManualEntryRequired:
        pass
    assert lot3.identify(None, T0, manual="29A-333") == "29A-333" and any(a["event"] == "camera_offline" for a in lot3.audit)
    return main, [f"confidence 55% (read '{anpr(bad)[0]}'): attendant asked; manual entry '{p2}' accepted",
                  "camera offline: manual entry used, fault logged"]


def uc08():  # nearest slot; zone B is closer to the gate than zone A
    spec = [[("A", "car", 2, 40), ("B", "car", 2, 10)]]
    lot = Lot(spec)
    free = [s for s in lot.root.slots() if s.vtype == "car" and s.state == FREE]
    tk = lot.enter("29A-111", "car", T0)
    assert tk.slot.name == "B1" and free[0].name == "A1"
    main = ["vehicle type: car", f"free car slots: {', '.join(s.name for s in free)}",
            f"distances: {', '.join(f'{s.name}={s.dist} m' for s in free)}",
            f"nearest: {tk.slot.name} ({tk.slot.dist} m); first in tree order would be {free[0].name} ({free[0].dist} m)",
            f"route: {lot.route(tk.slot)}"]
    lot2 = Lot(spec)
    lot2.lock("B", "admin", T0)
    s2 = lot2.enter("29A-111", "car", T0).slot
    lot3 = Lot([[("A", "car", 1, 10), ("B", "car", 1, 10)]])
    s3 = lot3.enter("29A-111", "car", T0).slot
    assert s2.name == "A1" and s3.name == "A1"
    return main, [f"zone B locked: next nearest is {s2.name} ({s2.dist} m)", f"A1 and B1 both {s3.dist} m: tree order gives {s3.name}"]


def uc09():  # sensors
    lot = Lot()
    tk = lot.enter("29A-111", "car", T0)
    r = readings_of(lot)
    r["A1"], r["A2"] = False, True  # driver parked in A2 instead of A1
    alerts = lot.scan(r, T0)
    assert {a[0] for a in alerts} == {"missing_vehicle", "unauthorized_vehicle"}
    lot.relocate("29A-111", "A2", T0)
    again = lot.scan(r, T0)
    assert again == [] and tk.slot.name == "A2" and lot.root.get("A1").state == FREE
    main = [f"{len(r)} sensors report occupancy", f"compared with {len(lot.tickets)} active ticket(s)",
            "mismatch: ticket holds A1 but vehicle sits in A2", f"{len(alerts)} alerts sent to attendant: {', '.join(a[0] for a in alerts)}",
            f"ticket moved A1 -> {tk.slot.name}; rescan: {len(again)} alerts"]
    lot2 = Lot()
    a_f = lot2.scan({"B2": None}, T0)
    s = lot2.root.get("B2")
    assert a_f[0][0] == "sensor_fault" and s.state == BLOCKED
    lot2.scan({"B2": False}, T0)
    assert s.state == FREE
    lot3 = Lot()
    lot3.enter("29A-111", "car", T0)
    a_o = lot3.scan({"A1": True}, at(hours=25))
    assert [a[0] for a in a_o] == ["overstay"]
    return main, ["B2 silent: slot Blocked + fault alert; sensor back: slot Free again", a_o[0][2]]


def uc10():  # evacuation
    lot = Lot()
    park(lot, ["29A-111", "29A-222"])
    lot.reserve("VIP-1", "car", at(hours=1))
    n = lot.evacuate(T0, role="admin")
    cancelled = sum(1 for a in lot.audit if a["event"] == "reserve_cancelled")
    rejected = lot.enter("29A-333", "car", T0) is None
    fee = lot.exit("29A-111", at(minutes=20))
    assert lot.barriers == "open" and rejected and fee == 0 and lot.root.get("A1").state == BLOCKED and cancelled == 1
    during = lot.barriers
    freed = lot.end_evacuation(at(minutes=30), role="admin")
    assert lot.barriers == "closed" and lot.enter("29A-333", "car", at(minutes=31)) is not None
    main = ["evacuation triggered by administrator", f"barriers: {during}; 29A-111 left, fee {fee} VND",
            f"{n} slots blocked, {cancelled} reservation cancelled; new entry rejected",
            f"message 'EVACUATE' shown on floors {', '.join(f.name for f in lot.root.children)}",
            f"logged: {next(a for a in lot.audit if a['event'] == 'evacuation_start')['detail']}",
            f"evacuation ended: {freed} slots unblocked, barriers {lot.barriers}, new entry accepted"]
    a1 = f"attendant denied: {denied(lambda: Lot().evacuate(T0, role='attendant'))}"
    lot2 = Lot()
    lot2.evacuate(T0, source="fire_alarm")
    assert lot2.barriers == "open"
    denied(lambda: lot2.end_evacuation(at(minutes=5), "attendant"))
    assert lot2.evac
    return main, [a1, "fire alarm started the evacuation without operator login; barriers open", "attendant cannot end the evacuation: lot stays evacuating"]


def uc11():  # dynamic fee
    lot, p = Lot(), "29A-444"
    t_in = T0.replace(hour=16)
    park_t = [lot.enter(x, "car", t_in) for x in ("29A-111", "29A-222", "29A-333")]
    tk = lot.enter(p, "car", t_in)  # 4th of 4 car slots -> occupancy 100%
    t_out = t_in + timedelta(hours=3, minutes=30)
    lines = lot.fee_lines(tk, t_out)
    fee = lot.fee(tk, t_out)
    assert tk.surcharge == 125 and [x[2] for x in lines] == [False, True, True, False] and fee == 62500 and park_t
    main = [f"ticket {tk.id} presented", f"{t_in:%H:%M}-{t_out:%H:%M} = {len(lines)} started hours",
            "; ".join(f"{h} {a}{' peak' if pk else ''}" for h, a, pk in lines) + f" = {sum(a for _, a, _ in lines)}",
            f"occupancy at entry {tk.slot.vtype} 100% > 80%: x1.25", f"fee {fee} VND shown and paid"]
    lot2 = Lot()
    t2 = lot2.enter("29A-111", "car", T0)
    base = lot2.fee(t2, at(hours=2))
    lot3 = Lot()
    t3 = lot3.enter("M-1", "moto", T0.replace(hour=17))
    mfee = lot3.fee(t3, T0.replace(hour=17, minute=30))
    assert base == 20000 and mfee == 4500
    lot4 = Lot()
    t4 = lot4.enter("29A-111", "car", T0)
    g15, g16 = lot4.fee(t4, at(minutes=15)), lot4.fee(t4, at(minutes=16))
    day, day2 = lot4.fee(t4, at(hours=24)), lot4.fee(t4, at(hours=25))
    assert (g15, g16, day, day2) == (0, 10000, 100000, 110000) and sum(a for _, a, _ in lot4.fee_lines(t4, at(hours=24))) == 260000
    return main, [f"off-peak, occupancy 25%: base rate, 2 h = {base} VND", f"moto 17:00-17:30: 3000 x 1.5 = {mfee} VND",
                  f"grace period: 15 min = {g15} VND, 16 min = {g16} VND", f"daily cap: 24 h would be 260000, charged {day} VND; 25 h = {day2} VND"]


def uc12():  # login
    acc = Accounts()
    role = acc.login("admin1", "admin123")
    assert role == "admin" and acc.events[-1] == ("login", "admin1")
    main = ["user enters admin1 and the password", "credentials match", f"role of admin1: {role}", f"access granted; event logged: {acc.events[-1][0]}"]
    acc2 = Accounts()
    a1 = f"{denied(lambda: acc2.login('admin1', 'wrong'))}; failed attempts: {acc2.fails['admin1']}"
    denied(lambda: acc2.login("admin1", "wrong"))
    last = denied(lambda: acc2.login("admin1", "wrong"))
    now = denied(lambda: acc2.login("admin1", "admin123"))
    assert "admin1" in acc2.locked
    return main, [a1, f"{last}; correct password now: {now}"]


def uc13():  # cancel reservation
    lot = Lot()
    s = lot.reserve("VIP-1", "car", at(minutes=30))
    cancelled = lot.cancel_reservation("VIP-1", T0)
    assert cancelled is s and s.state == FREE and lot.enter("X-1", "car", T0).slot is s
    main = ["plate VIP-1 entered", f"reservation found: {s.name}", "driver confirms the cancellation",
            f"{s.name} released (state Free) and assigned to the next car; cancellation logged"]
    try:
        Lot().cancel_reservation("NOPE", T0)
    except LookupError as e:
        a1 = str(e)
    lot2 = Lot()
    lot2.reserve("V", "car", at(minutes=30))
    lot2.expire(at(minutes=46))
    try:
        lot2.cancel_reservation("V", at(minutes=47))
    except LookupError as e:
        a2 = str(e)
    return main, [a1, a2]


def uc14():  # configure lot and rates
    lot, acc = Lot(), Accounts()
    role = acc.login("admin1", "admin123")
    old = lot.enter("29A-111", "car", T0)
    lot.set_rate("car", 12000, role, T0)
    park(lot, ["29A-222", "29A-333", "29A-444"])  # all 4 car slots are now taken
    assert lot.enter("29A-555", "car", T0) is None
    lot.add_zone("F2", "D", "car", 2, role, T0)
    new = lot.enter("29A-555", "car", T0)
    assert new.slot.name == "D1" and new.rate == 12000 and old.rate == 10000
    main = [f"admin1 authenticated, role {role}", "change: car rate 12000 and new zone D (2 car slots) on F2", "rate > 0 and zone name D unused: input valid",
            f"rate applied; zone D added; next car assigned {new.slot.name} ({lot.route(new.slot)})",
            f"logged: {', '.join(a['event'] for a in lot.audit if a['event'] in ('rate_change', 'zone_added'))}"]
    t_out = at(hours=1)
    assert lot.fee(old, t_out) == 10000 and lot.fee(new, t_out) == 12000 * 125 // 100  # new car entered at >80% occupancy
    bad = []
    for fn in (lambda: lot.set_rate("car", -5, role, T0), lambda: lot.add_zone("F2", "A", "car", 2, role, T0)):
        try:
            fn()
        except ValueError as e:
            bad.append(str(e))
    assert len(bad) == 2
    a3 = f"attendant denied: {denied(lambda: lot.set_rate('car', 9000, 'attendant', T0))}"
    return main, [f"parked 29A-111 keeps {old.rate}/h; new tickets pay {lot.rates['car']}/h", f"rejected: {bad[0]}", a3]


def uc15():  # availability
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    lot.reserve("VIP-1", "car", at(minutes=30))
    av = lot.availability()
    assert {x[1]: (x[2], x[3]) for x in av} == {"Smart Parking": (4, 6), "F1": (2, 4), "A": (0, 2), "B": (2, 2), "F2": (2, 2), "C": (2, 2)}
    lot.lock("A", "admin", T0)
    zone_a = next(x for x in lot.availability() if x[1] == "A")
    assert zone_a[2] == 0
    return ["driver looks at the display", f"counted free slots over {av[0][3]} slots in {len(av) - 1} floors and zones",
            ", ".join(f"{n} {f}/{t}" for _, n, f, t, _ in av)], [f"zone A locked: shows {zone_a[2]}/{zone_a[3]} free"]


def uc16():  # revenue and audit report
    lot, acc = Lot(), Accounts()
    role = acc.login("mgr1", "mgr123")
    park(lot, ["29A-111", "29A-222", "29A-333"])
    lot.exit("29A-111", at(hours=2))
    lot.exit("29A-222", at(hours=1))
    lot.exit("29A-333", at(hours=1))
    rep = lot.report(T0, at(hours=5), role)
    assert rep["total"] == 40000 and rep["by_zone"] == {"A": 30000, "C": 10000} and rep["by_floor"] == {"F1": 30000, "F2": 10000}
    main = [f"mgr1 authenticated, role {role}", f"period {T0:%H:%M}-{at(hours=5):%H:%M}", f"{rep['transactions']} payments collected",
            f"floors {rep['by_floor']}, zones {rep['by_zone']}", f"total {rep['total']} VND; {len(rep['audit'])} audit entries listed"]
    empty = lot.report(at(hours=6), at(hours=7), role)
    assert empty["total"] == 0 and empty["transactions"] == 0
    a2 = f"attendant denied: {denied(lambda: lot.report(T0, at(hours=5), 'attendant'))}"
    return main, [f"empty period: revenue {empty['total']}, {empty['transactions']} payments", a2]


def uc17():  # manual fee override with approval
    lot, acc = Lot(), Accounts()
    att, mgr = acc.login("att1", "att123"), acc.login("mgr1", "mgr123")
    p = "29A-111"
    tk = lot.enter(p, "car", T0)
    t = at(hours=2)
    calc = lot.fee(tk, t)
    oid = lot.request_override(p, 5000, "meter fault: driver overcharged", att, t)
    assert lot.overrides[oid]["state"] == "Pending" and lot.fee(tk, t) == calc
    lot.decide_override(oid, True, mgr, t)
    new = lot.fee(tk, t)
    assert new == 5000 and lot.exit(p, t) == 5000
    ev = [a["event"] for a in lot.audit if a["event"].startswith("override")]
    assert ev == ["override_requested", "override_approved"]
    main = [f"{att} authenticated (UC-12)", f"plate {p}, amount 5000, reason 'meter fault: driver overcharged'", f"{oid} recorded as Pending; fee still {calc} VND",
            f"{mgr} approves {oid}", f"ticket fee {new} VND (was {calc}); exit charged {new}; logged: {', '.join(ev)}"]
    lot2 = Lot()
    tk2 = lot2.enter(p, "car", T0)
    bad = []
    for amount, reason in ((5000, "  "), (-1, "typo")):
        try:
            lot2.request_override(p, amount, reason, "attendant", t)
        except ValueError as e:
            bad.append(str(e))
    assert len(bad) == 2
    oid2 = lot2.request_override(p, 0, "friend", "attendant", t)
    lot2.decide_override(oid2, False, "manager", t)
    assert lot2.fee(tk2, t) == 20000 and lot2.overrides[oid2]["state"] == "Rejected"
    oid3 = lot2.request_override(p, 0, "friend again", "attendant", t)
    a3 = denied(lambda: lot2.decide_override(oid3, True, "attendant", t))
    assert lot2.fee(tk2, t) == 20000
    return main, [f"rejected: {bad[0]}", f"manager rejects {oid2}: fee stays {lot2.fee(tk2, t)} VND", f"attendant approval denied: {a3}"]


def uc18():  # monthly / VIP pass
    lot = Lot()
    lot.add_zone("F1", "V", "car", 1, "admin", T0, base=1, vip=True)
    until = T0 + timedelta(days=30)
    lot.add_pass("VIP-1", "vip", until, "admin", T0)
    lot.add_pass("MON-1", "monthly", until, "admin", T0)
    p = lot.identify({"plate": "VIP-1", "quality": 0.95}, T0)
    tk = lot.enter(p, "car", T0)
    lot.open_gate("entry", T0)
    g = lot.gates["entry"]
    assert tk.pass_kind == "vip" and tk.slot.name == "V1"
    fee = lot.exit(p, at(hours=5))
    assert fee == 0
    main = [f"{p} (car) at the entry gate", f"plate {p} read (UC-07)", f"vip pass found, valid until {until:%Y-%m-%d}", f"VIP slot {tk.slot.name} ({tk.slot.dist} m)",
            f"ticket {tk.id} marked 'vip'; entry barrier {g}", f"5 h stay, parking fee {fee} VND"]
    lot2 = Lot()
    lot2.add_pass("OLD-1", "monthly", T0 + timedelta(days=1), "admin", T0)
    late = T0 + timedelta(days=2)
    t2 = lot2.enter("OLD-1", "car", late)
    f2 = lot2.exit("OLD-1", late + timedelta(hours=2))
    assert t2.pass_kind is None and f2 == 20000 and any(a["event"] == "pass_expired" for a in lot2.audit)
    m = lot.enter("MON-1", "car", T0)
    assert m.pass_kind == "monthly" and m.slot.name == "A1" and lot.exit("MON-1", at(hours=3)) == 0
    lot3 = Lot()
    lot3.add_zone("F2", "E", "ev", 2, "admin", T0)
    lot3.add_pass("EV-1", "monthly", until, "admin", T0)
    e = lot3.enter("EV-1", "ev", T0)
    lot3.charge("EV-1", 10, at(hours=1))
    f3 = lot3.exit("EV-1", at(hours=3))
    assert f3 == 10 * CHARGE_RATE
    return main, [f"pass ended {T0 + timedelta(days=1):%Y-%m-%d}: expiry logged, 2 h charged {f2} VND", f"monthly holder gets {m.slot.name}, not VIP slot V1; parking {0} VND",
                  f"EV with monthly pass: parking 0, energy 10 kWh x {CHARGE_RATE} = {f3} VND"]


def uc19():  # register / remove a pass
    lot, acc = Lot(), Accounts()
    role = acc.login("admin1", "admin123")
    until = T0 + timedelta(days=30)
    lot.add_pass("MON-1", "monthly", until, role, T0)
    assert lot.passes["MON-1"] == ("monthly", until)
    main = [f"admin1 authenticated, role {role}", "plate MON-1, type monthly, end date " + f"{until:%Y-%m-%d}", "type known and end date in the future: valid",
            f"pass stored; logged: {lot.audit[-1]['event']} ({lot.audit[-1]['detail']})"]
    bad = []
    for kind, end in (("gold", until), ("monthly", T0 - timedelta(days=1))):
        try:
            lot.add_pass("X-1", kind, end, role, T0)
        except ValueError as e:
            bad.append(str(e))
    assert len(bad) == 2 and "X-1" not in lot.passes
    a2 = f"attendant denied: {denied(lambda: lot.add_pass('X-1', 'monthly', until, 'attendant', T0))}"
    lot.set_network(False, at(minutes=1))
    try:
        lot.add_pass("X-1", "monthly", until, role, at(minutes=2))
        raise AssertionError("pass registered while offline")
    except OfflineError as e:
        a3 = f"refused: {e}"
    lot.set_network(True, at(minutes=3))
    lot.remove_pass("MON-1", role, at(minutes=4))
    assert "MON-1" not in lot.passes and lot.audit[-1]["event"] == "pass_removed"
    return main, [f"rejected: {bad[0]}; {bad[1]}", a2, a3, "MON-1 removed; removal logged"]


def uc20():  # EV charging
    lot = Lot()
    lot.add_zone("F2", "E", "ev", 2, "admin", T0)
    tk = lot.enter("EV-1", "ev", T0)
    assert tk.slot.name == "E1"
    cost = lot.charge("EV-1", 10, at(hours=1))
    t = at(hours=2)
    parking, fee = lot.parking_fee(tk, t), lot.fee(tk, t)
    assert (parking, cost, fee) == (24000, 35000, 59000) and lot.exit("EV-1", t) == 59000
    main = ["EV-1 (ev) at the entry gate", f"nearest free EV slot: {tk.slot.name} ({lot.route(tk.slot)})", f"charger reports 10 kWh; energy cost {cost} VND",
            f"2 h x {tk.rate} = {parking} VND parking + {cost} VND energy = {fee} VND", f"paid {fee} VND; slot {tk.slot.name} {tk.slot.state}"]
    lot2 = Lot()
    lot2.add_zone("F2", "E", "ev", 1, "admin", T0)
    assert lot2.enter("EV-1", "ev", T0) and lot2.enter("EV-2", "ev", T0) is None
    lot3 = Lot()
    lot3.add_zone("F2", "E", "ev", 1, "admin", T0)
    park(lot3, ["29A-1", "29A-2", "29A-3", "29A-4"])
    assert lot3.enter("29A-5", "car", T0) is None and lot3.root.get("E1").state == FREE
    bad = []
    for plate, kwh in (("EV-1", 0), ("EV-1", -3), ("29A-1", 5)):
        try:
            (lot2 if plate == "EV-1" else lot3).charge(plate, kwh, T0)
        except ValueError as e:
            bad.append(str(e))
    assert len(bad) == 3
    return main, ["2nd EV: REJECTED (EV zone full)", "5th car: REJECTED although E1 is free (cars never use EV slots)", f"rejected: 0 kWh, -3 kWh and a car reading ({bad[0]})"]


def uc21():  # exit plate verification
    lot = Lot()
    park(lot, ["29A-111", "29A-222"])
    t = at(hours=1)
    fee = lot.exit("29A-111", t, seen="29A-111")
    main = ["exit camera reads 29A-111", "ticket of 29A-111 found", "read 29A-111 == ticket 29A-111", f"match: payment {fee} VND, vehicle released"]
    try:
        lot.exit("29A-222", t, seen="29A-999")
        raise AssertionError("mismatch accepted")
    except PlateMismatch as e:
        a1 = f"{e}; slot {lot.find('29A-222').name} still occupied"
    assert lot.audit[-1]["event"] == "exit_plate_mismatch" and "29A-222" in lot.tickets
    ok = lot.exit("29A-222", t, seen="29A-999", approver="attendant")
    assert any(a["event"] == "exit_plate_override" for a in lot.audit) and "29A-222" not in lot.tickets
    lot2 = Lot()
    lot2.enter("29A-333", "car", T0)
    try:
        lot2.exit("29A-333", t, seen="")
        raise AssertionError("unreadable plate accepted")
    except PlateMismatch:
        pass
    try:
        lot2.exit("29A-333", t, seen="", approver="guest")
        raise AssertionError("guest approval accepted")
    except PlateMismatch:
        pass
    return main, [a1, f"attendant approves: vehicle leaves ({ok} VND), approval logged", "unreadable plate: barrier stays closed; a guest cannot approve"]


def uc22():  # offline mode
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    lot.set_network(False, at(minutes=30))
    lot.enter("29A-222", "car", at(minutes=31))
    fee = lot.exit("29A-111", at(hours=1, minutes=31), method="cash")
    marked = sum(1 for a in lot.audit if a.get("offline"))
    assert fee == 20000 and marked == 3
    n = lot.set_network(True, at(hours=2))
    assert n == 3 and not any(a.get("offline") for a in lot.audit) and lot.audit[-1]["event"] == "network_up"
    main = ["central server connection lost at 10:30", f"logged: {lot.audit[[a['event'] for a in lot.audit].index('network_down')]['event']}; gates in local mode",
            f"29A-222 enters, 29A-111 exits and pays {fee} VND cash", f"{marked} records marked offline", f"connection back: {n} records synchronized, marks cleared"]
    lot2 = Lot()
    tk = lot2.enter("29A-111", "car", T0)
    lot2.set_network(False, at(minutes=1))
    try:
        lot2.exit("29A-111", at(hours=1), method="online")
        raise AssertionError("online payment accepted offline")
    except PaymentFailed as e:
        a1 = f"online refused: {e}; slot {tk.slot.state}"
    lot2.exit("29A-111", at(hours=1), method="card")
    refused = []
    for fn in (lambda: lot2.reserve("R-1", "car", at(hours=2)), lambda: lot2.add_pass("P-1", "vip", at(days=3), "admin", at(hours=2))):
        try:
            fn()
        except OfflineError as e:
            refused.append(str(e))
    assert len(refused) == 2
    lot3 = Lot()
    lot3.enter("29A-111", "car", T0)
    lot3.set_network(False, at(minutes=1))
    lot3.evacuate(at(minutes=2), source="fire_alarm")
    assert lot3.barriers == "open" and lot3.exit("29A-111", at(minutes=3)) == 0
    return main, [a1, f"refused offline: {'; '.join(refused)}", "evacuation offline: barriers OPEN, exit free of charge"]


UC = {f"UC-{i:02d}": f for i, f in enumerate((uc01, uc02, uc03, uc04, uc05, uc06, uc07, uc08, uc09, uc10, uc11, uc12, uc13, uc14, uc15, uc16, uc17, uc18, uc19, uc20, uc21, uc22), 1)}


def run_uc(data, ucid):
    u = next(x for x in data["use_cases"] if x["id"] == ucid)
    steps, alts = ids(u["main_flow"]), ids(u["alternative_flows"])
    main, alt = UC[ucid]()
    assert (len(steps), len(alts)) == (len(main), len(alt)), f"{ucid}: xlsx flows and simulation are out of sync"
    L = [f"{ucid} {u['title']}   (actor: {u['primary_actor']}, frequency: {u['frequency']})",
         f"Goal         : {u['goal']}", f"Precondition : {u['preconditions']}", f"Trigger      : {u['trigger']}", "Main flow:"]
    L += [f"  {i}. {s}\n       => {r}" for i, (s, r) in enumerate(zip(steps, main), 1)]
    L += ["Alternative flows:"] + [f"  A{i}. {s}\n       => {r}" for i, (s, r) in enumerate(zip(alts, alt), 1)]
    L.append(f"Postcondition: {u['postconditions']}")
    return "\n".join(L)


# ---- F6: NFR simulation ----
def big_lot():
    return Lot([[(f"Z{f}{z}", "car", 50) for z in range(4)] for f in range(5)])  # 1000 car slots


def perf_p95(n=1000):  # NFR-01: n entry requests on a 1000-slot lot, time each
    lot = big_lot()
    times = []
    for i in range(n):
        t0 = time.perf_counter()
        assert lot.enter(f"P-{i}", "car", T0)
        times.append(time.perf_counter() - t0)
    return sorted(times)[int(0.95 * n) - 1]


def evac_seconds():  # NFR-06: trigger evacuation on a lot with 500 parked cars
    lot = big_lot()
    for i in range(500):
        lot.enter(f"P-{i}", "car", T0)
    t0 = time.perf_counter()
    lot.evacuate(T0, role="admin")
    assert lot.barriers == "open"
    return time.perf_counter() - t0


def uptime(seed=2026, days=30):  # NFR-02: simulated outages; model assumption: 0.4% chance per hour of a 5-20 min outage
    rng = random.Random(seed)
    down = sum(rng.randint(5, 20) for _ in range(days * 24) if rng.random() < 0.004)
    return 100 * (1 - down / (days * 24 * 60)), down


def nfr_results(data):
    tgt = {n["id"]: n["target_value"] for n in data["nfrs"]}
    p95, ev = perf_p95(), evac_seconds()
    up, down = uptime()
    return {"NFR-01": f"{'PASS' if p95 <= 1 else 'FAIL'}: p95 = {p95 * 1000:.3f} ms over 1000 requests (target {tgt['NFR-01']})",
            "NFR-02": f"{'PASS' if up >= 99.5 else 'FAIL'}: {up:.2f}% uptime, {down} min down in 30 days (target {tgt['NFR-02']}; modeled outages)",
            "NFR-06": f"{'PASS' if ev <= 5 else 'FAIL'}: barriers open in {ev * 1000:.3f} ms with 500 cars parked (target {tgt['NFR-06']})"}


# ---- F7: hazard mitigations exercised in the simulation ----
def hazard_demos():
    d = {}
    lot, p = Lot(), "29A-111"  # HZ-02: boundaries + entry/exit given in different UTC offsets
    tk = lot.enter(p, "car", T0)
    f = lambda **kw: lot.fee(tk, T0 + timedelta(**kw))
    assert (f(minutes=0), f(minutes=15), f(minutes=16), f(minutes=60), f(minutes=61)) == (0, 0, 10000, 10000, 20000)
    tk.since = datetime(2026, 10, 17, 3, 0, tzinfo=timezone.utc)  # = 10:00 local; exit 11:30 local = 04:30 UTC -> 1.5 h
    assert lot.fee(tk, datetime(2026, 10, 17, 11, 30, tzinfo=LOCAL)) == 20000
    oid = lot.request_override(p, 1000, "demo", "attendant", T0)  # the manual override of the mitigation needs a manager
    denied(lambda: lot.decide_override(oid, True, "attendant", T0))
    lot.decide_override(oid, True, "manager", T0)
    assert lot.fee(tk, T0) == 1000
    d["HZ-02"] = "OK: fee boundaries 0/15/16/60/61 min and mixed UTC offsets correct; manual override only with manager approval"
    lot = Lot()  # HZ-03: second assignment to the same slot is rejected by the state machine
    s = lot.pick("A", "car")
    lot.enter("A", "car", T0)
    try:
        s.to(OCCUPIED)
        raise AssertionError("double allocation allowed")
    except ValueError:
        pass
    assert lot.pick("B", "car").name == "A2"
    d["HZ-03"] = f"OK: {s.name} cannot be assigned twice; next request gets A2"
    assert Lot.close_barrier(sensor_ok=False) == "open" and Lot.close_barrier(vehicle_present=True) == "open" and Lot.close_barrier() == "closed"
    lot = Lot()
    lot.open_gate("exit", T0)
    assert not lot.close_gate("exit", T0, vehicle_present=True) and not lot.close_gate("exit", T0, sensor_ok=False) and lot.gates["exit"] == "OPEN"
    assert lot.close_gate("exit", T0) and lot.gates["exit"] == "CLOSED"
    d["HZ-04"] = "OK: close_gate keeps the barrier open on sensor fault or vehicle present, closes it otherwise"
    acc = Accounts()  # HZ-05: lockout after 3 failures + admin-only functions
    for _ in range(3):
        denied(lambda: acc.login("admin1", "x"))
    denied(lambda: acc.login("admin1", "admin123"))
    denied(lambda: Lot().lock("A", "attendant", T0))
    d["HZ-05"] = "OK: account locked after 3 failed logins; attendant cannot lock zones"
    lot = Lot()  # HZ-06: 79% is rejected, 80% accepted, garbled text is never ticketed
    for q, ok in ((0.79, False), (0.80, True)):
        try:
            lot.identify({"plate": "29A-111", "quality": q}, T0)
            assert ok
        except ManualEntryRequired:
            assert not ok
    d["HZ-06"] = "OK: read at 79% needs manual entry, read at 80% accepted"
    lot = Lot()  # HZ-07: fail-open on power loss; evacuation exits need no payment
    lot.enter("29A-111", "car", T0)
    lot.evacuate(T0, source="fire_alarm")
    assert Lot.close_barrier(power_ok=False) == "open" and lot.barriers == "open" and lot.exit("29A-111", T0, paid=False) == 0
    d["HZ-07"] = "OK: barriers open on evacuation or power loss; exit needs no payment"
    lot = Lot()  # HZ-08: silent sensor blocks its slot, mismatch is alerted
    assert lot.scan({"B2": None, "A1": True}, T0) and lot.root.get("B2").state == BLOCKED
    d["HZ-08"] = "OK: silent sensor blocks the slot; unticketed vehicle alerted"
    lot = Lot()  # HZ-09: stacked multipliers are capped by construction (1.5 x 1.25 = 1.875)
    for x in ("1", "2", "3"):
        lot.enter(x, "car", T0.replace(hour=17))
    tk = lot.enter("4", "car", T0.replace(hour=17))
    worst = lot.fee(tk, T0.replace(hour=18))
    assert worst == 18750 and worst < 2 * RATE["car"]
    d["HZ-09"] = f"OK: worst case 1 h peak + surcharge = {worst} VND (< 2 x base)"
    lot = Lot()  # HZ-10: a ticket cannot be used by another vehicle
    lot.enter("29A-111", "car", T0)
    try:
        lot.exit("29A-111", at(hours=1), seen="29A-222")
        raise AssertionError("ticket swap accepted")
    except PlateMismatch:
        pass
    assert "29A-111" in lot.tickets
    d["HZ-10"] = "OK: exit with a different plate stays closed until an attendant approves"
    lot = Lot()  # HZ-11: gates keep working without the network
    lot.set_network(False, T0)
    assert lot.enter("29A-111", "car", T0) and lot.exit("29A-111", at(hours=1), method="cash") == 10000
    assert lot.set_network(True, at(hours=2)) == 3
    d["HZ-11"] = "OK: entry and cash exit work offline; 3 records synchronized afterwards"
    lot = Lot()  # HZ-12: nobody approves their own request
    tk = lot.enter("29A-111", "car", T0)
    oid = lot.request_override("29A-111", 0, "friend", "attendant", T0)
    denied(lambda: lot.decide_override(oid, True, "attendant", T0))
    assert lot.fee(tk, at(hours=1)) == 10000 and any(a["event"] == "denied" for a in lot.audit)
    d["HZ-12"] = "OK: an attendant cannot approve; the denial is audited and the fee is unchanged"
    return d


# ---- 8.3 end-to-end simulated day ----
def day_sim():
    lot, L = Lot(), []

    def ev(t, msg):
        L.append(f"{t:%H:%M}  {msg}")
    ev(at(), f"29A-111 identified by camera, enters -> {lot.enter(lot.identify({'plate': '29A-111', 'quality': 0.93}, at()), 'car', at()).slot.name}")
    ev(at(minutes=10), f"29B-222 camera confidence 60% -> manual entry -> {lot.enter(lot.identify({'plate': '29B-222', 'quality': 0.6}, at(minutes=10), manual='29B-222'), 'car', at(minutes=10)).slot.name}")
    ev(at(minutes=15), f"M-1 enters -> {lot.enter('M-1', 'moto', at(minutes=15)).slot.name}")
    ev(at(minutes=20), f"VIP-1 reserves for {at(hours=1):%H:%M} -> {lot.reserve('VIP-1', 'car', at(hours=1)).name}")
    ev(at(minutes=30), f"admin locks zone B -> {lot.lock('B', 'admin', at(minutes=30))} slot(s) blocked")
    ev(at(minutes=40), f"M-2 enters -> {'rejected (zone B locked)' if lot.enter('M-2', 'moto', at(minutes=40)) is None else 'accepted?'}")
    r = readings_of(lot)
    r["A2"] = False  # 29B-222 actually sits elsewhere: sensor of its slot reports empty
    ev(at(minutes=50), f"sensor scan -> {[a[0] for a in lot.scan(r, at(minutes=50))]}")
    ev(at(hours=1, minutes=20), f"VIP-1 no-show -> released {lot.expire(at(hours=1, minutes=20))}")
    ev(at(hours=2), f"29A-111 exits, fee {lot.exit('29A-111', at(hours=2))} VND")
    ev(at(hours=2, minutes=30), f"29B-222 lost ticket, fee {lot.lost_ticket('29B-222', at(hours=2, minutes=30))} VND")
    ev(at(hours=3), f"M-1 exits, fee {lot.exit('M-1', at(hours=3))} VND")
    return "\n".join(L) + "\n\n" + "\n".join(show(lot.root)) + f"\n\nAudit log: {len(lot.audit)} records ({', '.join(sorted({a['event'] for a in lot.audit}))})."


# ---- F5: one check per test case; returns actual result text, raises AssertionError on failure, Skip if not runnable ----
class Skip(Exception):
    pass


def tc01(_):
    lot = Lot()
    tk = lot.enter("29A-111", "car", T0)
    assert tk and tk.slot.name == "A1"
    return "Ticket issued; slot A1 assigned."


def tc02(_):
    lot = Lot()
    park(lot, ["M-1", "M-2"], "moto")
    assert lot.enter("M-3", "moto", T0) is None and lot.audit[-1]["event"] == "entry_rejected"
    return "Entry rejected ('zone full'); rejection logged."


def tc03(_):
    lot = Lot()
    tk = lot.enter("29A-111", "car", T0)
    f = lot.fee(tk, at(hours=2, minutes=30))
    assert f == 30000
    return f"Fee = {f} VND."


def tc04(_):
    p95 = perf_p95()
    assert p95 <= 1
    return f"p95 = {p95 * 1000:.3f} ms over 1000 requests."


def tc05(_):
    lot = Lot()
    lot.reserve("VIP-1", "car", T0)
    other = lot.enter("29A-111", "car", T0)
    vip = lot.enter("VIP-1", "car", T0)
    assert other.slot.name == "A2" and vip.slot.name == "A1"
    return "Other car got A2; VIP-1 got A1."


def tc06(_):
    lot = Lot()
    park(lot, ["29A-111", "29A-222", "29A-333"])
    s = lot.find("29A-222")
    assert lot.path_of(s).endswith("F1 > A > A2")
    return f"Found: {lot.path_of(s)}."


def tc07(data):
    st = next(c["status"] for c in data["change_requests"] if c["id"] == "CR-01")
    if st not in ("Approved", "Implemented"):
        raise Skip(f"CR-01 not implemented (status: {st}).")
    lot = Lot()
    t = T0.replace(hour=17)
    tk = lot.enter("29A-111", "car", t)
    f = lot.fee(tk, t + timedelta(hours=1))
    assert f == 15000
    return f"CR-01 implemented: 17:00-18:00 charged {f} VND (1.5 x 10000)."


def tc08(_):
    denied(lambda: Lot().lock("A", "attendant", T0))
    return "PermissionError raised; denial logged."


def tc09(_):
    lot = Lot()
    lot.lock("A", "admin", T0)
    tk = lot.enter("29A-111", "car", T0)
    assert tk.slot.name == "C1"
    return "Car assigned C1 (zone A blocked)."


def tc10(_):
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    fee = lot.lost_ticket("29A-111", at(hours=5))
    assert fee == 290000
    return f"Charged {fee} VND (24h x 10000 + 50000)."


def tc11(_):
    assert Lot().identify({"plate": "29A-111", "quality": 0.95}, T0) == "29A-111"
    return "Plate 29A-111 accepted at 95% confidence."


def tc12(_):
    lot, bad = Lot(), {"plate": "29A-222", "quality": 0.55}
    try:
        lot.identify(bad, T0)
    except ManualEntryRequired:
        assert lot.identify(bad, T0, manual="29A-222") == "29A-222"
        return "55% confidence: manual entry requested and accepted."
    raise AssertionError("low confidence accepted")


def tc13(_):
    lot = Lot()
    try:
        lot.identify(None, T0)
    except ManualEntryRequired:
        assert lot.audit[-1]["event"] == "camera_offline"
        return "Manual entry required; camera_offline logged."
    raise AssertionError("offline camera accepted")


def tc14(_):
    lot = Lot([[("A", "car", 2, 40), ("B", "car", 2, 10)]])
    assert lot.enter("29A-111", "car", T0).slot.name == "B1"
    return "Assigned B1 (10 m), not A1 (40 m)."


def tc15(_):
    lot = Lot([[("A", "car", 2, 40), ("B", "car", 2, 10)]])
    lot.lock("B", "admin", T0)
    assert lot.enter("29A-111", "car", T0).slot.name == "A1"
    return "Zone B locked: assigned A1."


def tc16(_):
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    r = readings_of(lot)
    r["A1"], r["A2"] = False, True
    kinds = sorted(a[0] for a in lot.scan(r, T0))
    assert kinds == ["missing_vehicle", "unauthorized_vehicle"]
    return f"Alerts: {', '.join(kinds)}."


def tc17(_):
    lot = Lot()
    s = lot.root.get("B2")
    assert lot.scan({"B2": None}, T0)[0][0] == "sensor_fault" and s.state == BLOCKED
    lot.scan({"B2": False}, T0)
    assert s.state == FREE
    return "B2 blocked on silence, freed on recovery."


def tc18(_):
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    assert lot.scan({"A1": True}, at(hours=24)) == []  # exactly 24 h is not over the limit
    a = lot.scan({"A1": True}, at(hours=25))
    assert [x[0] for x in a] == ["overstay"]
    return a[0][2]


def tc19(_):
    lot = Lot()
    park(lot, ["29A-111", "29A-222"])
    lot.evacuate(T0, role="admin")
    assert lot.barriers == "open" and lot.enter("29A-333", "car", T0) is None and lot.exit("29A-111", at(minutes=5)) == 0
    return "Barriers open; entry rejected; exit charged 0 VND."


def tc20(_):
    denied(lambda: Lot().evacuate(T0, role="attendant"))
    return "PermissionError raised; denial logged."


def tc21(_):
    lot = Lot()
    lot.evacuate(T0, source="fire_alarm")
    assert lot.barriers == "open"
    return "Evacuation started by fire alarm; barriers open."


def tc22(_):
    s = evac_seconds()
    assert s <= 5
    return f"Barriers open in {s * 1000:.3f} ms (500 cars parked)."


def tc23(_):
    lot = Lot()
    t = T0.replace(hour=16, minute=30)
    tk = lot.enter("29A-111", "car", t)
    f = lot.fee(tk, t + timedelta(hours=2))
    assert f == 25000
    return f"Fee = {f} VND (10000 + 15000)."


def tc24(_):
    lot = Lot()
    park(lot, ["29A-111", "29A-222", "29A-333"])
    tk3 = lot.tickets["29A-333"]
    tk4 = lot.enter("29A-444", "car", T0)
    assert tk3.surcharge == 100 and tk4.surcharge == 125 and lot.fee(tk4, at(hours=1)) == 12500
    return "3rd car base fee; 4th car (100% occupancy) 12500 VND = 125% of 10000."


def tc25(_):
    lot = Lot()
    tk = lot.enter("29A-111", "car", T0)
    try:
        lot.exit("29A-111", at(hours=1), paid=False)
        raise AssertionError("exit without payment")
    except PaymentFailed:
        assert tk.slot.state == OCCUPIED
    lot.exit("29A-111", at(hours=1))
    assert tk.slot.state == FREE
    return "Failed payment: slot stays occupied; after payment the slot is free."


def tc26(_):
    try:
        Lot().exit("NOPE", T0)
    except LookupError as e:
        return f"Exit refused: {e}."
    raise AssertionError("unknown ticket accepted")


def tc27(_):
    lot = Lot()
    for i in range(4):
        assert lot.reserve(f"R-{i}", "car", T0)
    assert lot.reserve("R-5", "car", T0) is None
    return "5th reservation rejected (4 car slots reserved)."


def tc28(_):
    lot = Lot()
    s = lot.reserve("V", "car", at(minutes=30))
    assert lot.expire(at(minutes=46)) == [s.name] and s.state == FREE
    return f"{s.name} released at 10:46."


def tc29(_):
    assert Lot().find("NOPE") is None
    return "Plate NOPE: vehicle not found."


def tc30(_):
    lot = Lot()
    tk = lot.enter("29A-111", "car", T0)
    lot.lock("A", "admin", T0)
    assert tk.slot.state == OCCUPIED
    lot.exit("29A-111", at(hours=1))
    assert tk.slot.state == BLOCKED
    return "A1 occupied after lock; blocked after exit."


def tc31(_):
    try:
        Lot().lost_ticket("NOPE", T0)
    except LookupError as e:
        return f"{e}: escalate to the Parking Manager."
    raise AssertionError("lost ticket without record accepted")


def tc32(_):
    lot = Lot()
    tk = lot.enter("29A-111", "car", T0)
    denied(lambda: lot.lost_ticket("29A-111", T0, proof=False))
    assert tk.slot.state == OCCUPIED and lot.audit[-1]["event"] == "lost_ticket_refused"
    return "Exit refused; refusal logged."


def tc33(_):
    assert Accounts().login("admin1", "admin123") == "admin"
    return "admin1 logged in with role admin."


def tc34(_):
    acc = Accounts()
    denied(lambda: acc.login("admin1", "wrong"))
    assert acc.fails["admin1"] == 1
    return "Login rejected; failed attempts: 1."


def tc35(_):
    acc = Accounts()
    for _ in range(3):
        denied(lambda: acc.login("admin1", "wrong"))
    assert "admin1" in acc.locked
    return denied(lambda: acc.login("admin1", "admin123")) + " (correct password refused after 3 failures)."


def tc36(_):
    lot = Lot()
    s = lot.reserve("VIP-1", "car", at(minutes=30))
    lot.cancel_reservation("VIP-1", T0)
    assert lot.enter("29A-111", "car", T0).slot is s
    return f"{s.name} released and assigned to the next car."


def tc37(_):
    try:
        Lot().cancel_reservation("NOPE", T0)
    except LookupError as e:
        return f"Cancel NOPE: {e}."
    raise AssertionError("missing reservation cancelled")


def tc38(_):
    lot = Lot()
    a = lot.enter("29A-111", "car", T0)
    lot.set_rate("car", 12000, "admin", T0)
    b = lot.enter("29A-222", "car", T0)
    assert (lot.fee(a, at(hours=1)), lot.fee(b, at(hours=1))) == (10000, 12000)
    return "Car A pays 10000; car B pays 12000."


def tc39(_):
    lot = Lot()
    park(lot, ["1", "2", "3", "4"])
    assert lot.enter("5", "car", T0) is None
    lot.add_zone("F2", "D", "car", 2, "admin", T0)
    assert lot.enter("5", "car", T0).slot.name == "D1"
    return "Zone D added; the next car got D1."


def tc40(_):
    lot = Lot()
    denied(lambda: lot.set_rate("car", 9000, "attendant", T0))
    denied(lambda: lot.add_zone("F2", "D", "car", 2, "attendant", T0))
    return "Both attempts denied."


def tc41(_):
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    lot.reserve("VIP-1", "car", at(minutes=30))
    av = {x[1]: (x[2], x[3]) for x in lot.availability()}
    assert av["A"] == (0, 2) and av["F1"] == (2, 4) and av["Smart Parking"] == (4, 6)
    return "A 0/2, F1 2/4, lot 4/6 free."


def tc42(_):
    lot = Lot()
    park(lot, ["29A-111", "29A-222", "29A-333"])
    for p in ("29A-111", "29A-222", "29A-333"):
        lot.exit(p, at(hours=1))
    rep = lot.report(T0, at(hours=5), "manager")
    assert sum(rep["by_zone"].values()) == sum(rep["by_floor"].values()) == rep["total"] == 30000
    return f"Zones {rep['by_zone']} = floors {rep['by_floor']} = total {rep['total']}."


def tc43(_):
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    lot.exit("29A-111", at(hours=1))
    ev = [a["event"] for a in lot.audit]
    assert all(e in ev for e in ("entry", "payment", "exit")) and all(a["t"] for a in lot.audit)
    return f"Audit events: {', '.join(ev)}."


def tc44(_):
    lot = Lot()
    lot.evacuate(T0, role="admin")
    lot.end_evacuation(at(minutes=10), "admin")
    assert lot.barriers == "closed" and lot.root.get("A1").state == FREE and lot.enter("29A-111", "car", at(minutes=11))
    return "Barriers closed, slots unblocked, new entry accepted."


def tc45(_):
    lot = Lot()
    s = lot.reserve("VIP-1", "car", at(minutes=30))
    lot.evacuate(T0, role="admin")
    assert s.state == BLOCKED and s.reserved_for is None
    return f"Reservation cancelled; {s.name} blocked."


def tc46(_):
    lot = Lot()
    lot.lock("A", "admin", T0)
    assert next(x for x in lot.availability() if x[1] == "A")[2] == 0
    return "Zone A shows 0 free slots."


def tc47(_):
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    lot.exit("29A-111", at(hours=1))
    rep = lot.report(at(hours=6), at(hours=7), "manager")
    assert rep["total"] == 0 and rep["transactions"] == 0
    return "Revenue 0, 0 payments."


def _ev_lot(n=2):
    lot = Lot()
    lot.add_zone("F2", "E", "ev", n, "admin", T0)
    return lot


def tc48(_):
    lot = Lot()
    tk = lot.enter("29A-111", "car", T0)
    a, b = lot.fee(tk, at(minutes=15)), lot.fee(tk, at(minutes=16))
    assert (a, b) == (0, 10000)
    return f"15 min = {a} VND; 16 min = {b} VND."


def tc49(_):
    lot = Lot()
    tk = lot.enter("29A-111", "car", T0)
    a, b = lot.fee(tk, at(hours=24)), lot.fee(tk, at(hours=25))
    assert (a, b) == (100000, 110000)
    return f"24 h = {a} VND (cap); 25 h = {b} VND."


def tc50(_):
    return uc17()[0][-1]


def tc51(_):
    return "; ".join(uc17()[1])


def tc52(_):
    lot = Lot()
    lot.add_pass("29A-111", "monthly", at(days=30), "admin", T0)
    tk = lot.enter("29A-111", "car", T0)
    assert tk.pass_kind == "monthly" and lot.exit("29A-111", at(hours=5)) == 0
    return "Monthly pass: 5 h stay costs 0 VND."


def tc53(_):
    lot = Lot()
    lot.add_zone("F1", "V", "car", 1, "admin", T0, base=1, vip=True)
    lot.add_pass("VIP-1", "vip", at(days=30), "admin", T0)
    lot.add_pass("MON-1", "monthly", at(days=30), "admin", T0)
    m, v, o = lot.enter("MON-1", "car", T0), lot.enter("VIP-1", "car", T0), lot.enter("29A-111", "car", T0)
    assert (m.slot.name, v.slot.name) == ("A1", "V1") and o.slot.name == "A2"
    return f"VIP holder: {v.slot.name}; monthly holder: {m.slot.name}; ordinary car: {o.slot.name}."


def tc54(_):
    lot = Lot()
    lot.add_pass("OLD-1", "monthly", at(days=1), "admin", T0)
    tk = lot.enter("OLD-1", "car", at(days=2))
    f = lot.exit("OLD-1", at(days=2, hours=2))
    assert tk.pass_kind is None and f == 20000 and any(a["event"] == "pass_expired" for a in lot.audit)
    return f"Expired pass: expiry logged, fee {f} VND."


def tc55(_):
    return "; ".join([uc19()[0][-1], uc19()[1][-1]])


def tc56(_):
    return "; ".join(uc19()[1][:3])


def tc57(_):
    lot = _ev_lot(2)
    a, b, c = lot.enter("EV-1", "ev", T0), lot.enter("EV-2", "ev", T0), lot.enter("EV-3", "ev", T0)
    car = lot.enter("29A-111", "car", T0)
    assert (a.slot.name, b.slot.name, c) == ("E1", "E2", None) and car.slot.name == "A1"
    return "EVs got E1 and E2; third EV rejected; the car got A1."


def tc58(_):
    lot = _ev_lot(2)
    tk = lot.enter("EV-1", "ev", T0)
    lot.charge("EV-1", 10, at(hours=1))
    f = lot.exit("EV-1", at(hours=2))
    assert f == 2 * 12000 + 10 * CHARGE_RATE == 59000
    return f"Fee {f} VND (24000 parking + 35000 energy)."


def tc59(_):
    lot = _ev_lot(2)
    park(lot, ["EV-1"], "ev")
    lot.enter("29A-111", "car", T0)
    n = 0
    for plate, kwh in (("EV-1", 0), ("29A-111", 5)):
        try:
            lot.charge(plate, kwh, T0)
        except ValueError:
            n += 1
    assert n == 2
    return "0 kWh and a car reading were rejected."


def tc60(_):
    lot = Lot()
    lot.enter("29A-111", "car", T0)
    assert lot.exit("29A-111", at(hours=1), seen="29A-111") == 10000 and "29A-111" not in lot.tickets
    return "Matching plate: vehicle left."


def tc61(_):
    lot = Lot()
    park(lot, ["29A-111", "29A-222"])
    try:
        lot.exit("29A-111", at(hours=1), seen="29A-222")
        raise AssertionError("mismatch accepted")
    except PlateMismatch:
        pass
    assert "29A-111" in lot.tickets and lot.audit[-1]["event"] == "exit_plate_mismatch"
    return "Mismatch: exit refused, slot still occupied, mismatch logged."


def tc62(_):
    return uc21()[1][1] + "; " + uc21()[1][2]


def tc63(_):
    lot = Lot()
    lot.set_network(False, T0)
    lot.enter("29A-111", "car", T0)
    lot.exit("29A-111", at(hours=1), method="cash")
    assert sum(1 for a in lot.audit if a.get("offline")) == 3
    return "Offline: entry and cash exit worked; 3 records marked."


def tc64(_):
    return "; ".join(uc22()[1][:2])


def tc65(_):
    lot = Lot()
    lot.set_network(False, T0)
    lot.enter("29A-111", "car", T0)
    n = lot.set_network(True, at(hours=1))
    assert n == 1 and not any(a.get("offline") for a in lot.audit) and "1 records" in lot.audit[-1]["detail"]
    return f"{n} record synchronized; marks cleared; count logged."


def tc66(_):
    return uc22()[1][2]


def tc67(_):
    lot = Lot()
    lot.open_gate("entry", T0)
    assert not lot.close_gate("entry", T0, vehicle_present=True) and lot.gates["entry"] == "OPEN"
    assert not lot.close_gate("entry", T0, sensor_ok=False) and lot.audit[-1]["event"] == "gate_close_refused"
    assert not lot.close_gate("entry", T0, power_ok=False) and lot.gates["entry"] == "OPEN"
    assert sum(a["event"] == "gate_close_refused" for a in lot.audit) == 3
    assert lot.close_gate("entry", T0) and lot.gates["entry"] == "CLOSED"
    return "Barrier stayed open with a vehicle present, a sensor fault, and power loss (3 refusals logged); closed when clear."


TESTS = {f"TC-{i:02d}": globals()[f"tc{i:02d}"] for i in range(1, 68)}


def run_tests(data):
    out, today, by = {}, datetime.now().date().isoformat(), "SPS terminal tool"
    for t in data["test_cases"]:
        fn = TESTS.get(t["id"])
        try:
            if fn is None:
                raise Skip("no automated check")
            out[t["id"]] = ("Passed", fn(data), by, today)
        except Skip as e:
            out[t["id"]] = ("Not Run", str(e), by, today)
        except AssertionError as e:
            out[t["id"]] = ("Failed", f"assertion failed: {e}", by, today)
    return out
