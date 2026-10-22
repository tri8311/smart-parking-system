"""Parking simulation. Tree: Lot > Floor > Zone > Slot. State machines, pricing, plate recognition, sensors, evacuation, accounts."""
import zlib
from datetime import datetime, timedelta, timezone
from math import ceil

RATE = {"car": 10000, "moto": 3000, "ev": 12000}  # default VND per started hour (REQ-03); each Lot keeps its own editable copy
CHARGE_RATE = 3500                    # VND per kWh charged by an EV (REQ-25)
GRACE = timedelta(minutes=15)         # stays up to this long are free (REQ-27)
DAILY_CAP_HOURS = 10                  # each started 24 h costs at most this many hourly rates (REQ-28)
PENALTY = 50000                       # lost-ticket penalty (REQ-07)
HOLD = timedelta(minutes=15)          # reservation hold after arrival time (REQ-05)
LOCAL = timezone(timedelta(hours=7))  # lot time zone; peak hours are local time
PEAK = ((7, 9), (17, 19))             # peak periods [start, end) in local hours (REQ-16)
PEAK_PCT = 150                        # peak multiplier in percent
FULL_PCT, SURCHARGE_PCT = 80, 125     # occupancy above 80% at entry -> 125% of fee (REQ-17)
MIN_CONF = 0.8                        # plate recognition threshold (REQ-11)
MAX_STAY = timedelta(hours=24)        # overstay limit (REQ-14)
PAY_METHODS = ("cash", "card", "online")

FREE, RESERVED, OCCUPIED, BLOCKED = "Free", "Reserved", "Occupied", "Blocked"
SLOT_FLOW = {FREE: {RESERVED, OCCUPIED, BLOCKED}, RESERVED: {OCCUPIED, FREE},
             OCCUPIED: {FREE, BLOCKED}, BLOCKED: {FREE}}
ACTIVE, PAID, LOST, CLOSED = "Active", "Paid", "Lost", "Closed"
TICKET_FLOW = {ACTIVE: {PAID, LOST}, PAID: {CLOSED}, LOST: {CLOSED}, CLOSED: set()}
NORMAL, EVACUATING = "Normal", "Evacuating"
LOT_FLOW = {NORMAL: {EVACUATING}, EVACUATING: {NORMAL}}

# floors -> zones (name, vehicle type, slots[, distance of the zone's first slot to the gate in metres])
STANDARD = [[("A", "car", 2), ("B", "moto", 2)], [("C", "car", 2)]]


def move(cur, new, flow):
    if new not in flow[cur]:
        raise ValueError(f"illegal transition {cur} -> {new}")
    return new


class PaymentFailed(Exception):
    pass


class CameraOffline(Exception):
    pass


class ManualEntryRequired(Exception):
    pass


class PlateMismatch(Exception):
    pass


class OfflineError(Exception):
    pass


class Node:
    def __init__(self, name, kind, vtype=None):
        self.name, self.kind, self.vtype = name, kind, vtype  # kind: lot|floor|zone|slot (or any, for other trees)
        self.children = []
        self.state, self.plate, self.since = FREE, None, None  # slot-only fields below
        self.reserved_for = self.reserved_until = None
        self.locked, self.fault, self.revenue, self.dist, self.vip = False, False, 0, 0, False

    def add(self, *kids):
        self.children += kids
        return self

    def slots(self):  # DFS over leaves
        if self.kind == "slot":
            yield self
        for c in self.children:
            yield from c.slots()

    def get(self, name):
        if self.name == name:
            return self
        for c in self.children:
            r = c.get(name)
            if r:
                return r

    def to(self, new):
        self.state = move(self.state, new, SLOT_FLOW)


def make_zone(floor_no, zi, name, vtype, n, base=None, vip=False):  # base: distance of the first slot to the gate (default: by zone order)
    zone = Node(name, "zone", vtype)
    for i in range(1, n + 1):
        s = Node(f"{name}{i}", "slot", vtype)
        s.vip = vip
        s.dist = (floor_no - 1) * 40 + (10 + 15 * zi if base is None else base) + 3 * (i - 1)
        zone.add(s)
    return zone


def build_tree(spec=STANDARD):
    root = Node("Smart Parking", "lot")
    for f, zones in enumerate(spec, 1):
        root.add(Node(f"F{f}", "floor").add(*[make_zone(f, zi, z, vtype, n, *base) for zi, (z, vtype, n, *base) in enumerate(zones)]))
    return root


def anpr(frame):  # simulated recogniser: returns (plate text, confidence); low quality garbles the text
    if frame is None:
        raise CameraOffline("entry camera offline")
    q = frame["quality"]
    p = frame["plate"]
    return (p if q >= MIN_CONF else "".join(c if i % 2 == 0 else "?" for i, c in enumerate(p))), q


def ticket_id(plate, t):
    return "TCK-" + format(zlib.crc32(f"{plate}|{t.isoformat()}".encode()), "08X")


class Ticket:
    def __init__(self, plate, vtype, slot, since, surcharge=100, rate=None, id=None):
        self.plate, self.vtype, self.slot, self.since, self.surcharge, self.state = plate, vtype, slot, since, surcharge, ACTIVE
        self.rate = RATE[vtype] if rate is None else rate  # rate at the time of entry
        self.id = id or ticket_id(plate, since)
        self.kwh, self.pass_kind, self.override = 0.0, None, None  # energy charged (EV, REQ-25); pass seen at entry (REQ-23); approved fee override (REQ-22)

    def to(self, new):
        self.state = move(self.state, new, TICKET_FLOW)


class Accounts:  # REQ-18. ponytail: plaintext passwords in memory, demo only; hash + persist for real use
    MAX_FAILS = 3

    def __init__(self):
        self.users = {"admin1": ("admin123", "admin"), "att1": ("att123", "attendant"), "mgr1": ("mgr123", "manager")}
        self.fails, self.locked, self.events = {}, set(), []

    def login(self, user, password):
        if user in self.locked:
            self.events.append(("login_refused_locked", user))
            raise PermissionError("account locked")
        known = self.users.get(user)
        if known is None or known[0] != password:
            if known is not None:
                self.fails[user] = self.fails.get(user, 0) + 1
                self.events.append(("login_failed", user))
                if self.fails[user] >= self.MAX_FAILS:
                    self.locked.add(user)
                    self.events.append(("account_locked", user))
                    raise PermissionError(f"account locked after {self.MAX_FAILS} failed attempts")
            raise PermissionError("invalid credentials")
        self.fails[user] = 0
        self.events.append(("login", user))
        return known[1]


class Lot:
    def __init__(self, spec=STANDARD):
        self.root = build_tree(spec)
        self.tickets = {}  # plate -> active Ticket
        self.audit = []    # REQ-10
        self.evac = False
        self.rates = dict(RATE)
        self.gates = {"entry": "CLOSED", "exit": "CLOSED"}
        self.gateway_up = True
        self.network_up = True  # REQ-29: False = offline mode
        self.expired = set()  # plates whose reservation lapsed (for a clear message on cancel)
        self.passes = {}      # plate -> (kind, valid_until)   REQ-23
        self.overrides = {}   # id -> request dict            REQ-22

    @property
    def barriers(self):
        return "open" if all(g == "OPEN" for g in self.gates.values()) else "closed"

    def open_gate(self, which, t):
        self.gates[which] = "OPEN"
        self.log(t, "gate_open", "", which)

    def close_gate(self, which, t, sensor_ok=True, vehicle_present=False, power_ok=True):  # REQ-31: never close on a vehicle or a faulty presence sensor
        if self.close_barrier(sensor_ok, vehicle_present, power_ok) == "open":
            self.log(t, "gate_close_refused", "", f"{which}: vehicle present or sensor/power fault; barrier stays open")
            return False
        self.gates[which] = "CLOSED"
        self.log(t, "gate_close", "", which)
        return True

    def log(self, t, event, plate="", detail="", **extra):
        if not self.network_up:
            extra["offline"] = True  # kept locally until synchronized
        self.audit.append({"t": t.isoformat(), "event": event, "plate": plate, "detail": detail, **extra})

    def _central(self, what, t):  # functions that need the central server
        if not self.network_up:
            self.log(t, "central_refused", "", what)
            raise OfflineError(f"{what} needs the central server (offline mode)")

    # ---- plate recognition (REQ-11) ----
    def identify(self, frame, t, manual=None):
        reason = None
        try:
            plate, conf = anpr(frame)
            if conf >= MIN_CONF:
                self.log(t, "anpr_read", plate, f"confidence {conf:.0%}")
                return plate
            reason = f"confidence {conf:.0%} below {MIN_CONF:.0%} (read '{plate}')"
            self.log(t, "anpr_low_confidence", "", reason)
        except CameraOffline:
            reason = "camera offline"
            self.log(t, "camera_offline", "", "switched to manual entry")
        if manual is None:
            raise ManualEntryRequired(reason)
        self.log(t, "manual_entry", manual, reason)
        return manual

    # ---- slot choice (REQ-12) ----
    def nearest(self, vtype, vip=False):  # free slot with the smallest distance; ties keep DFS (tree) order. VIP slots: VIP holders first, nobody else
        free = [s for s in self.root.slots() if s.vtype == vtype and s.state == FREE and (vip or not s.vip)]
        return min(free, key=lambda s: (not (vip and s.vip), s.dist), default=None)

    def pick(self, plate, vtype, vip=False):  # reserved-for-me first, else nearest free
        for s in self.root.slots():
            if s.vtype == vtype and s.state == RESERVED and s.reserved_for == plate:
                return s
        return self.nearest(vtype, vip)

    def occupancy(self, vtype):  # percent of slots of this type that are occupied or reserved
        ss = [s for s in self.root.slots() if s.vtype == vtype]
        return 100 * sum(s.state in (OCCUPIED, RESERVED) for s in ss) // len(ss)

    def route(self, slot):
        return f"{slot.dist} m from the gate: {self.path_of(slot)}"

    def availability(self):  # (kind, name, free, total, depth) for lot, floors and zones (REQ-02)
        out = []

        def walk(n, depth):
            if n.kind in ("lot", "floor", "zone"):
                ss = list(n.slots())
                out.append((n.kind, n.name, sum(s.state == FREE for s in ss), len(ss), depth))
            for c in n.children:
                walk(c, depth + 1)
        walk(self.root, 0)
        return out

    # ---- entry / exit / pricing ----
    def enter(self, plate, vtype, t):
        if plate in self.tickets:
            raise ValueError(f"{plate} is already parked")
        if self.evac:
            self.log(t, "entry_rejected", plate, "evacuation in progress")
            return None
        kind, until = self.passes.get(plate, (None, None))
        if kind and t > until:
            self.log(t, "pass_expired", plate, f"{kind} pass ended {until:%Y-%m-%d}")
            kind = None
        s = self.pick(plate, vtype, kind == "vip")
        if s is None:
            self.log(t, "entry_rejected", plate, f"no free {vtype} slot")
            return None
        s.to(OCCUPIED)  # atomic: an occupied slot can never be assigned twice (HZ-03)
        s.plate, s.since, s.reserved_for, s.reserved_until = plate, t, None, None
        tk = self.tickets[plate] = Ticket(plate, vtype, s, t, SURCHARGE_PCT if self.occupancy(vtype) > FULL_PCT else 100, self.rates[vtype])
        tk.pass_kind = kind
        self.log(t, "entry", plate, f"{s.name} {tk.id}" + (f" {kind} pass" if kind else ""))
        return tk

    @staticmethod
    def fee_lines(ticket, t):  # one line per started hour: (local start time, amount, is_peak). Aware datetimes: timezone-safe (HZ-02)
        secs = (t - ticket.since).total_seconds()
        if secs < 0:
            raise ValueError("exit time before entry time")
        lines = []
        for k in range(max(1, ceil(secs / 3600))):
            start = (ticket.since + timedelta(hours=k)).astimezone(LOCAL)
            peak = any(a <= start.hour < b for a, b in PEAK)
            lines.append((f"{start:%H:%M}", ticket.rate * PEAK_PCT // 100 if peak else ticket.rate, peak))
        return lines

    @classmethod
    def parking_fee(cls, ticket, t):  # hourly part: free inside the grace period (REQ-27), capped per started day (REQ-28), free for pass holders (REQ-23)
        if ticket.pass_kind or t - ticket.since <= GRACE:
            return 0
        a = [x for _, x, _ in cls.fee_lines(ticket, t)]
        cap = DAILY_CAP_HOURS * ticket.rate
        return sum(min(cap, sum(a[d:d + 24])) for d in range(0, len(a), 24)) * ticket.surcharge // 100

    @classmethod
    def breakdown(cls, ticket, t):  # text lines for the gate display, the quote command and the live scenarios
        L = [f"{h}  {a} VND{' (peak x1.5)' if pk else ''}" for h, a, pk in cls.fee_lines(ticket, t)]
        if ticket.surcharge != 100:
            L.append(f"Occupancy surcharge {ticket.surcharge}% applied")
        pf = cls.parking_fee(ticket, t)
        why = (f"free: {ticket.pass_kind} pass" if ticket.pass_kind else "free: grace period" if t - ticket.since <= GRACE
               else "daily cap applied" if pf < sum(a for _, a, _ in cls.fee_lines(ticket, t)) * ticket.surcharge // 100 else "")
        L.append(f"Parking fee: {pf} VND" + (f" ({why})" if why else ""))
        if ticket.kwh:
            L.append(f"Energy: {ticket.kwh:g} kWh x {CHARGE_RATE} = {round(ticket.kwh * CHARGE_RATE)} VND")
        if ticket.override is not None:
            L.append(f"Approved manual override: {ticket.override} VND replaces the calculated fee")
        L.append(f"Total due: {cls.fee(ticket, t)} VND")
        return L

    @classmethod
    def fee(cls, ticket, t):  # an approved manual override replaces the whole amount (REQ-22)
        if ticket.override is not None:
            return ticket.override
        return cls.parking_fee(ticket, t) + round(ticket.kwh * CHARGE_RATE)

    def _release(self, ticket, fee):
        s = ticket.slot
        s.plate = s.since = None
        s.to(BLOCKED if s.locked else FREE)
        s.revenue += fee
        del self.tickets[ticket.plate]

    def exit(self, plate, t, paid=True, method="cash", seen=None, approver=None):  # seen: plate read by the exit camera (REQ-26)
        if method not in PAY_METHODS:
            raise ValueError(f"unknown payment method {method}")
        tk = self.tickets.get(plate)
        if tk is None:
            raise LookupError("ticket not found")
        fee = 0 if self.evac else self.fee(tk, t)  # evacuation: exits are free
        if not self.evac:
            if seen is not None and seen != plate:
                if approver not in ("attendant", "manager", "admin"):
                    self.log(t, "exit_plate_mismatch", plate, f"camera read {seen or "no plate"}, ticket belongs to {plate}; barrier stays closed")
                    raise PlateMismatch(f"camera read {seen or "no plate"} but the ticket belongs to {plate}: barrier closed, attendant alerted")
                self.log(t, "exit_plate_override", plate, f"camera read {seen or "no plate"}, approved by {approver}")
            if method == "online" and not (self.gateway_up and self.network_up):
                self.log(t, "payment_failed", plate, "payment gateway unavailable" if self.network_up else "network down")
                raise PaymentFailed("payment gateway unavailable: pay cash or card at the booth")
            if not paid:
                self.log(t, "payment_failed", plate, str(fee))
                raise PaymentFailed(f"payment of {fee} VND failed, barrier stays closed")
        tk.to(PAID)
        self.log(t, "payment", plate, f"{method} {fee}", amount=fee, slot=tk.slot.name)
        tk.to(CLOSED)
        self._release(tk, fee)
        self.log(t, "exit", plate, tk.slot.name)
        return fee

    def lost_ticket(self, plate, t, proof=True):  # proof: attendant checked identity and ownership
        tk = self.tickets.get(plate)
        if tk is None:
            raise LookupError("no entry record")
        if not proof:
            self.log(t, "lost_ticket_refused", plate, "ownership not proven")
            raise PermissionError("ownership not proven: exit refused, manager notified")
        fee = 24 * tk.rate + PENALTY  # maximum daily fee + penalty
        tk.to(LOST)
        tk.to(CLOSED)
        self.log(t, "lost_ticket", plate, str(fee), amount=fee, slot=tk.slot.name)
        self._release(tk, fee)
        return fee

    # ---- reservations ----
    def reserve(self, plate, vtype, arrive):
        self._central("reservation", arrive)
        s = self.nearest(vtype)
        if s is None:
            self.log(arrive, "reserve_rejected", plate, vtype)
            return None
        s.to(RESERVED)
        s.reserved_for, s.reserved_until = plate, arrive + HOLD
        self.expired.discard(plate)
        self.log(arrive, "reserve", plate, s.name)
        return s

    def _unreserve(self, s):
        s.reserved_for = s.reserved_until = None
        s.to(FREE)
        if s.locked:
            s.to(BLOCKED)

    def expire(self, t):  # release no-show reservations
        out = []
        for s in self.root.slots():
            if s.state == RESERVED and t > s.reserved_until:
                self.expired.add(s.reserved_for)
                self._unreserve(s)
                self.log(t, "reserve_expired", "", s.name)
                out.append(s.name)
        return out

    def cancel_reservation(self, plate, t):  # REQ-19
        s = next((s for s in self.root.slots() if s.state == RESERVED and s.reserved_for == plate), None)
        if s is None:
            raise LookupError("reservation expired" if plate in self.expired else "reservation not found")
        self._unreserve(s)
        self.log(t, "reserve_cancelled", plate, s.name)
        return s

    # ---- zone lock (REQ-08, REQ-09) and evacuation (REQ-15) ----
    def _set_lock(self, node, lock):
        n = 0
        for s in node.slots():
            s.locked = lock
            if lock and s.state == FREE:
                s.to(BLOCKED)
                n += 1
            elif not lock and s.state == BLOCKED and not s.fault:
                s.to(FREE)
                n += 1
        return n

    def _admin(self, role, t, what):
        if role != "admin":
            self.log(t, "denied", "", f"{role} tried to {what}")
            raise PermissionError("administrator only")

    def lock(self, name, role, t, lock=True):
        self._admin(role, t, f"{'lock' if lock else 'unlock'} {name}")
        node = self.root.get(name)
        if node is None:
            raise KeyError(name)
        n = self._set_lock(node, lock)
        self.log(t, "lock" if lock else "unlock", "", f"{name}: {n} slots")
        return n

    def evacuate(self, t, role=None, source="operator"):  # fire alarm needs no login; operator must be admin
        if source == "operator":
            self._admin(role, t, "start evacuation")
        move(EVACUATING if self.evac else NORMAL, EVACUATING, LOT_FLOW)  # raises if already evacuating
        self.evac = True
        for s in self.root.slots():  # reservations are cancelled
            if s.state == RESERVED:
                self.log(t, "reserve_cancelled", s.reserved_for, f"{s.name}: evacuation")
                s.reserved_for = s.reserved_until = None
                s.to(FREE)
        n = self._set_lock(self.root, True)
        self.gates = {"entry": "OPEN", "exit": "OPEN"}
        self.log(t, "evacuation_start", "", f"source={source}, role={role}")
        return n

    def end_evacuation(self, t, role):
        self._admin(role, t, "end evacuation")
        move(EVACUATING, NORMAL, LOT_FLOW)
        self.evac = False
        n = self._set_lock(self.root, False)
        self.gates = {"entry": "CLOSED", "exit": "CLOSED"}
        self.log(t, "evacuation_end", "", f"role={role}")
        return n

    @staticmethod
    def close_barrier(sensor_ok=True, vehicle_present=False, power_ok=True):  # fail-safe: stay open on any fault (HZ-04, HZ-07)
        return "open" if (not sensor_ok or vehicle_present or not power_ok) else "closed"

    # ---- configuration (REQ-20) ----
    def set_rate(self, vtype, rate, role, t):
        self._admin(role, t, f"set the {vtype} rate")
        if vtype not in self.rates or not isinstance(rate, int) or rate <= 0:
            raise ValueError("invalid rate input")
        self.rates[vtype] = rate
        self.log(t, "rate_change", "", f"{vtype} {rate}")

    def add_zone(self, floor, name, vtype, n, role, t, base=None, vip=False):
        self._admin(role, t, f"add zone {name}")
        fl = self.root.get(floor)
        if fl is None or fl.kind != "floor":
            raise KeyError(floor)
        if vtype not in self.rates or n < 1 or self.root.get(name) or any(self.root.get(f"{name}{i}") for i in range(1, n + 1)):
            raise ValueError("invalid zone input (type, size or duplicate name)")
        fl.add(make_zone(self.root.children.index(fl) + 1, len(fl.children), name, vtype, n, base, vip))
        self.log(t, "zone_added", "", f"{floor}/{name}: {n} {vtype}{' VIP' if vip else ''} slots")
        return n

    # ---- passes (REQ-23), EV charging (REQ-25), fee override (REQ-22), offline mode (REQ-29) ----
    def add_pass(self, plate, kind, until, role, t):
        self._admin(role, t, f"register a pass for {plate}")
        self._central("pass registration", t)
        if kind not in ("monthly", "vip") or until < t:
            raise ValueError("invalid pass (kind monthly|vip, end date in the future)")
        self.passes[plate] = (kind, until)
        self.log(t, "pass_added", plate, f"{kind} until {until:%Y-%m-%d}")

    def remove_pass(self, plate, role, t):
        self._admin(role, t, f"remove the pass of {plate}")
        self._central("pass removal", t)
        if self.passes.pop(plate, None) is None:
            raise LookupError("no pass for this plate")
        self.log(t, "pass_removed", plate)

    def charge(self, plate, kwh, t):  # simulated charger meter reading
        tk = self.tickets.get(plate)
        if tk is None:
            raise LookupError("ticket not found")
        if tk.vtype != "ev" or not kwh > 0:
            raise ValueError("charging needs an EV in an EV slot and a positive energy reading")
        tk.kwh += kwh
        self.log(t, "charge", plate, f"{kwh} kWh in {tk.slot.name}")
        return round(tk.kwh * CHARGE_RATE)

    def _need(self, roles, role, t, what):
        if role not in roles:
            self.log(t, "denied", "", f"{role} tried to {what}")
            raise PermissionError(" or ".join(roles) + " only")

    def request_override(self, plate, amount, reason, role, t):  # attendant asks, manager/admin approves
        self._need(("attendant", "manager", "admin"), role, t, "request a fee override")
        tk = self.tickets.get(plate)
        if tk is None:
            raise LookupError("ticket not found")
        if not isinstance(amount, int) or amount < 0 or not reason.strip():
            raise ValueError("override needs a non-negative amount and a reason")
        oid = f"OV-{len(self.overrides) + 1}"
        self.overrides[oid] = {"plate": plate, "amount": amount, "reason": reason, "by": role, "state": "Pending"}
        self.log(t, "override_requested", plate, f"{oid} {self.fee(tk, t)} -> {amount} VND: {reason}")
        return oid

    def decide_override(self, oid, approve, role, t):
        self._need(("manager", "admin"), role, t, "decide a fee override")
        o = self.overrides.get(oid)
        if o is None or o["state"] != "Pending":
            raise LookupError("no pending override " + oid)
        o["state"] = "Approved" if approve else "Rejected"
        if approve:
            self.tickets[o["plate"]].override = o["amount"]
        self.log(t, "override_" + o["state"].lower(), o["plate"], f"{oid} by {role}")

    def set_network(self, up, t):
        if up == self.network_up:
            raise ValueError("network already " + ("up" if up else "down"))
        if not up:
            self.log(t, "network_down", "", "local mode: entry, exit, cash and card payment, sensors and evacuation continue")
            self.network_up = False
            return 0
        self.network_up = True
        n = 0
        for a in self.audit:
            if a.pop("offline", None):
                n += 1
        self.log(t, "network_up", "", f"synchronized {n} records")
        return n

    # ---- sensors (REQ-13, REQ-14) ----
    def scan(self, readings, t):  # readings: slot name -> True (vehicle) / False (empty) / None (no heartbeat)
        alerts = []
        for s in self.root.slots():
            if s.name not in readings:
                continue
            r = readings[s.name]
            if r is None:
                if not s.fault:
                    s.fault = True
                    if s.state == FREE:
                        s.to(BLOCKED)
                    alerts.append(("sensor_fault", s.name, f"no heartbeat from the sensor of {s.name}; slot blocked"))
                continue
            if s.fault:  # sensor recovered
                s.fault = False
                if s.state == BLOCKED and not s.locked:
                    s.to(FREE)
            if r and s.state != OCCUPIED:
                alerts.append(("unauthorized_vehicle", s.name, f"vehicle detected in {s.name} ({s.state}) without a ticket"))
            elif not r and s.state == OCCUPIED:
                alerts.append(("missing_vehicle", s.name, f"ticket {s.plate} holds {s.name} but the sensor shows it empty"))
        for tk in self.tickets.values():
            if t - tk.since > MAX_STAY:
                alerts.append(("overstay", tk.slot.name, f"{tk.plate} parked {(t - tk.since).total_seconds() / 3600:.0f} h in {tk.slot.name} (limit 24 h)"))
        for a in alerts:
            self.log(t, "alert_" + a[0], "", a[2])
        return alerts

    def relocate(self, plate, slot_name, t):  # attendant moves the ticket to the slot where the vehicle really is
        tk = self.tickets.get(plate)
        if tk is None:
            raise LookupError("ticket not found")
        new = self.root.get(slot_name)
        if new is None or new.kind != "slot" or new.vtype != tk.vtype or new.state != FREE:
            raise ValueError(f"{slot_name} is not a free {tk.vtype} slot")
        old = tk.slot
        old.plate = old.since = None
        old.to(BLOCKED if old.locked else FREE)
        new.to(OCCUPIED)
        new.plate, new.since, tk.slot = plate, tk.since, new
        self.log(t, "relocate", plate, f"{old.name} -> {new.name}")
        return new

    # ---- reports (REQ-21, REQ-10) ----
    def report(self, t0, t1, role):
        if role not in ("admin", "manager"):
            self.log(t1, "denied", "", f"{role} tried to read the report")
            raise PermissionError("administrator or manager only")
        rows = [a for a in self.audit if t0 <= datetime.fromisoformat(a["t"]) <= t1]
        pays = [a for a in rows if a["event"] in ("payment", "lost_ticket") and a.get("amount", 0) > 0]
        by_floor, by_zone = {}, {}
        for a in pays:
            _, floor, zone, _ = self.path_of(self.root.get(a["slot"])).split(" > ")
            by_floor[floor] = by_floor.get(floor, 0) + a["amount"]
            by_zone[zone] = by_zone.get(zone, 0) + a["amount"]
        return {"total": sum(a["amount"] for a in pays), "transactions": len(pays), "by_floor": by_floor, "by_zone": by_zone, "audit": rows}

    def find(self, plate):
        tk = self.tickets.get(plate)
        return tk.slot if tk else None

    def path_of(self, slot):
        def walk(n, trail):
            if n is slot:
                return trail + [n.name]
            for c in n.children:
                r = walk(c, trail + [n.name])
                if r:
                    return r
        return " > ".join(walk(self.root, []))

    # ---- persistence (interactive mode). Only the standard tree plus slot state is stored; added zones are not. ----
    def dump(self):
        keys = ("state", "plate", "reserved_for", "locked", "fault", "revenue")
        return {"slots": {s.name: {**{k: getattr(s, k) for k in keys},
                                   "since": s.since and s.since.isoformat(),
                                   "reserved_until": s.reserved_until and s.reserved_until.isoformat(),
                                   "surcharge": self.tickets[s.plate].surcharge if s.plate in self.tickets else 100,
                                   "rate": self.tickets[s.plate].rate if s.plate in self.tickets else None,
                                   "ticket_id": self.tickets[s.plate].id if s.plate in self.tickets else None,
                                   "kwh": self.tickets[s.plate].kwh if s.plate in self.tickets else 0.0,
                                   "pass_kind": self.tickets[s.plate].pass_kind if s.plate in self.tickets else None,
                                   "override": self.tickets[s.plate].override if s.plate in self.tickets else None}
                          for s in self.root.slots()},
                "audit": self.audit, "evac": self.evac, "rates": self.rates, "network_up": self.network_up, "overrides": self.overrides,
                "passes": {p: [k, u.isoformat()] for p, (k, u) in self.passes.items()}}

    @classmethod
    def load(cls, d):
        lot = cls()
        lot.audit, lot.evac, lot.rates = d["audit"], d.get("evac", False), {**RATE, **d.get("rates", {})}
        lot.network_up, lot.overrides = d.get("network_up", True), d.get("overrides", {})
        lot.passes = {p: (k, datetime.fromisoformat(u)) for p, (k, u) in d.get("passes", {}).items()}
        if lot.evac:
            lot.gates = {"entry": "OPEN", "exit": "OPEN"}
        for s in lot.root.slots():
            v = d["slots"][s.name]
            s.state, s.plate, s.reserved_for, s.locked, s.revenue = (v[k] for k in ("state", "plate", "reserved_for", "locked", "revenue"))
            s.fault = v.get("fault", False)
            s.since = v["since"] and datetime.fromisoformat(v["since"])
            s.reserved_until = v["reserved_until"] and datetime.fromisoformat(v["reserved_until"])
            if s.plate:
                tk = lot.tickets[s.plate] = Ticket(s.plate, s.vtype, s, s.since, v.get("surcharge", 100), v.get("rate"), v.get("ticket_id"))
                tk.kwh, tk.pass_kind, tk.override = v.get("kwh", 0.0), v.get("pass_kind"), v.get("override")
        return lot


def show(n, depth=0):
    pad = "  " * depth
    if n.kind == "slot":
        who = n.plate or n.reserved_for or ""
        tag = " [sensor fault]" if n.fault else ""
        return [f"{pad}[{n.name}] {n.state} {n.dist}m" + (f" ({n.vtype} {who})" if who else "") + tag]
    ss = list(n.slots())
    free = sum(s.state == FREE for s in ss)
    lines = [f"{pad}{n.kind.upper()} {n.name}  ({free}/{len(ss)} free, revenue {sum(s.revenue for s in ss)})"]
    for c in n.children:
        lines += show(c, depth + 1)
    return lines
