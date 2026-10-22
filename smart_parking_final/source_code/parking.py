"""Smart Parking System - terminal requirements-management and simulation tool.

python parking.py        interactive menu
python parking.py all    run every feature once (non-interactive) and export the report
"""
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timedelta

import checks
import live
import rm
from sim import Lot, Accounts, show

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("PARKING_DATA") or os.path.join(HERE, "parking_data.xlsx")
STATE = os.path.join(HERE, "state.json")
OUT = os.path.join(HERE, "out")
SOURCES = ("sim.py", "rm.py", "checks.py", "live.py", "parking.py")


def head(s):
    print(f"\n=== {s} ===")


# ---- F4 scenarios ----
def scenarios(data, all_=False):
    ucs = data["use_cases"]
    if all_:
        for u in ucs:
            print("\n" + checks.run_uc(data, u["id"]))
        return
    while True:
        head("Scenarios (use cases)")
        for i, u in enumerate(ucs, 1):
            print(f"  {i}. {u['id']} {u['title']}  [{u['primary_actor']}]")
        print("  0. Back")
        c = input("Choose: ").strip()
        if c == "0":
            return
        if c.isdigit() and 1 <= int(c) <= len(ucs):
            print("\n" + checks.run_uc(data, ucs[int(c) - 1]["id"]))
        else:
            print("Invalid choice.")


# ---- F5 tests: run, write back to xlsx, reload ----
def run_tests(data, quiet=False):
    res = checks.run_tests(data)
    try:
        rm.save_results(DATA, res)
    except PermissionError:
        print(f"! cannot write {DATA} (open in Excel?) - results shown but not saved")
    data.update(rm.load(DATA))
    if not quiet:
        head("Test results (saved to test_cases sheet)")
        print(rm.tests_text(data))


def risk(data):
    head("Risk matrix and hazards")
    print(rm.risk_text(data, checks.hazard_demos()))


def nfr(data):
    head("NFR classification (ISO/IEC 25010) and simulation verification")
    print(rm.nfr_text(data, checks.nfr_results(data)))


def tree_and_impact(data):
    head("Requirement dependency tree")
    print(rm.tree_text(data))
    head("Change impact analysis")
    print(rm.impact_text(data))


def diagrams(data):
    slot, ticket, lot = rm.state_mmd()
    for title, body in (("Slot state machine", slot), ("Ticket state machine", ticket), ("Lot state machine", lot), ("Use case diagram", rm.usecase_mmd(data)),
                        ("Data flow diagram", rm.DFD_MMD)):
        head(f"{title} (Mermaid)")
        print(body)


def lot_tree():
    lot = Lot()
    lot.enter("29A-111", "car", checks.T0)
    lot.enter("M-1", "moto", checks.T0)
    head("Parking tree (Lot > Floor > Zone > Slot)")
    print("\n".join(show(lot.root)))


# ---- F9 report export ----
def export(data):
    run_tests(data, quiet=True)
    os.makedirs(OUT, exist_ok=True)
    slot, ticket, lot_state = rm.state_mmd()
    fence = lambda body, lang="": f"```{lang}\n{body}\n```"
    uc_runs = "\n\n".join(checks.run_uc(data, u["id"]) for u in data["use_cases"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for _, fn in live.LIVE:
            fn(live.auto)
            print()
    live_runs = buf.getvalue().rstrip()
    tb = rm.table
    sec = [
        ("1.2 Stakeholder analysis", tb(["ID", "Name", "Role", "Influence", "Interest"], [[s[k] for k in ("id", "name", "role", "influence", "interest")] for s in data["stakeholders"]])
         + "\n\nPower/interest grid:\n\n" + rm.grid_text(data)),
        ("2.1 Elicitation techniques applied", tb(["ID", "Technique", "Date", "Participants", "Findings", "Derived"], [[e[k] for k in ("id", "technique", "date", "participants", "findings", "requirements_derived")] for e in data["elicitation_sessions"]])),
        ("2.2 Candidate requirements list", tb(["ID", "Title", "Priority", "Status", "Source"], [[r[k] for k in ("id", "title", "priority", "status", "source")] for r in data["requirements"]])),
        ("3.1 Functional requirements", tb(["ID", "Description", "Priority", "Acceptance criteria", "Verification"], [[r[k] for k in ("id", "description", "priority", "acceptance_criteria", "verification_method")] for r in data["requirements"]])),
        ("3.2 Non-functional requirements", tb(["ID", "Category", "Description", "Metric", "Target", "Priority"], [[n[k] for k in ("id", "category", "description", "metric", "target_value", "priority")] for n in data["nfrs"]])),
        ("4.1 Use case diagram", fence(rm.usecase_mmd(data), "mermaid")),
        ("4.2 Detailed scenarios (executed on the simulation)", fence(uc_runs)),
        ("5.1 State machine diagrams", "Slot:\n\n" + fence(slot, "mermaid") + "\n\nTicket:\n\n" + fence(ticket, "mermaid") + "\n\nLot:\n\n" + fence(lot_state, "mermaid")),
        ("5.2 Data flow diagram", fence(rm.DFD_MMD, "mermaid")),
        ("5.3 Parking structure tree", fence(rm.lot_mmd(Lot().root), "mermaid")),
        ("6.1 ISO/IEC 25010 classification", rm.nfr_text(data)),
        ("6.2 Simulation verification results", rm.nfr_text(data, checks.nfr_results(data))),
        ("7.1 Requirements traceability matrix", rm.trace_text(data) + "\n\nDependency tree:\n\n" + fence(rm.tree_text(data)) + "\n\n" + fence(rm.dep_mmd(data), "mermaid")),
        ("7.2 Automated test results", rm.tests_text(data)),
        ("7.3 Requirements change management", fence(rm.impact_text(data))),
        ("8.1 Safety risk analysis", rm.risk_text(data, checks.hazard_demos())),
        ("8.2 Integrated requirements management tool - quality report", rm.quality_text(data)),
        ("8.3 Simulation and end-to-end testing", "One simulated day:\n\n" + fence(checks.day_sim()) + "\n\nLive gate scenarios (default answers):\n\n" + fence(live_runs)),
        ("Appendix A: Full dataset", rm.dataset_text(data)),
        ("Appendix B: Source code", "\n\n".join(f"### {f}\n\n" + fence(open(os.path.join(HERE, f), encoding="utf-8").read(), "python") for f in SOURCES)),
    ]
    path = os.path.join(OUT, "report_appendix.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Smart Parking System - report appendix (generated by parking.py)\n\n" + "\n\n".join(f"## {t}\n\n{b}" for t, b in sec) + "\n")
    print(f"Report written: {path}  ({len(sec)} sections)")


# ---- F10 interactive mode with persistence ----
HELP = """commands (accounts: admin1/admin123, att1/att123, mgr1/mgr123):
  login <account> <password>   logout
  enter <plate> <car|moto|ev> [camera-quality 0-1 [manual-plate]]   quote <plate> (fee breakdown now)
  exit <plate> [cash|card|online|fail] [seen=<plate-read-by-camera>] [approve]   lost <plate> [noproof]   gateway on|off   network on|off
  override <plate> <vnd> <reason...> (staff)   approve|reject <OV-n> (manager, admin)   charge <plate> <kWh> (EV)
  pass <plate> <monthly|vip> <days> (admin)   unpass <plate> (admin)
  reserve <plate> <car|moto> [minutes until arrival, default 30]   cancel <plate>
  find <plate>   avail   scan [ghost <slot> | silent <slot>]   relocate <plate> <slot>
  lock|unlock <zone|floor> (admin)   evacuate [fire]   endevac (admin)
  rate <car|moto> <vnd> (admin)   addzone <floor> <name> <car|moto|ev> <slots> [vip] (admin)   report [hours back] (admin, manager)
  tick <minutes>   tree   log   reset   help   quit"""


def interactive():
    st = json.load(open(STATE)) if os.path.exists(STATE) else None
    lot = Lot.load(st["lot"]) if st else Lot()
    clock = datetime.fromisoformat(st["clock"]) if st else checks.T0
    role, accounts = "guest", Accounts()
    head("Interactive parking (state saved in state.json)")
    print(HELP)
    while True:
        lot.expire(clock)
        json.dump({"lot": lot.dump(), "clock": clock.isoformat()}, open(STATE, "w"))
        cmd = input(f"\n[{clock:%H:%M} {role}] > ").split()
        if not cmd:
            continue
        c, a = cmd[0].lower(), cmd[1:]
        try:
            if c in ("quit", "q"):
                return
            elif c == "help":
                print(HELP)
            elif c == "enter":
                plate = lot.identify({"plate": a[0], "quality": float(a[2])}, clock, a[3] if len(a) > 3 else None) if len(a) > 2 else a[0]
                tk = lot.enter(plate, a[1], clock)
                print(f"-> plate {plate}: {lot.route(tk.slot)}" if tk else "-> REJECTED: no free slot of that type (or evacuation)")
            elif c == "quote":
                tk = lot.tickets[a[0]]
                print("\n".join("  " + x for x in lot.breakdown(tk, clock)))
            elif c == "login":
                role = accounts.login(a[0], a[1])
                print(f"-> logged in as {a[0]}, role {role}")
            elif c == "logout":
                role = "guest"
            elif c == "network":
                n = lot.set_network(a[0] == "on", clock)
                print("-> network UP, " + f"{n} offline records synchronized" if a[0] == "on" else "-> network DOWN: local mode (online payment, reservations and pass changes refused)")
            elif c == "override":
                print(f"-> {lot.request_override(a[0], int(a[1]), ' '.join(a[2:]), role, clock)} pending: a manager or administrator must approve it")
            elif c in ("approve", "reject"):
                lot.decide_override(a[0], c == "approve", role, clock)
                print(f"-> {a[0]} {lot.overrides[a[0]]['state'].lower()}")
            elif c == "charge":
                print(f"-> energy cost so far {lot.charge(a[0], float(a[1]), clock)} VND")
            elif c == "pass":
                lot.add_pass(a[0], a[1], clock + timedelta(days=int(a[2])), role, clock)
                print(f"-> {a[1]} pass registered for {a[0]}")
            elif c == "unpass":
                lot.remove_pass(a[0], role, clock)
                print(f"-> pass of {a[0]} removed")
            elif c == "gateway":
                lot.gateway_up = a[0] == "on"
                print(f"-> payment gateway {'up' if lot.gateway_up else 'DOWN'}")
            elif c == "exit":
                opt = next((x for x in a[1:] if "=" not in x and x != "approve"), "cash")
                seen = next((x[5:] for x in a[1:] if x.startswith("seen=")), None)
                fee = lot.exit(a[0], clock, paid=opt != "fail", method="cash" if opt == "fail" else opt, seen=seen, approver=role if "approve" in a else None)
                lot.open_gate("exit", clock)
                lot.close_gate("exit", clock)
                print(f"-> paid {fee} VND ({opt}), exit barrier opened and closed")
            elif c == "lost":
                print(f"-> charged {lot.lost_ticket(a[0], clock, proof=not (len(a) > 1 and a[1] == 'noproof'))} VND")
            elif c == "cancel":
                print(f"-> reservation of {a[0]} cancelled, {lot.cancel_reservation(a[0], clock).name} released")
            elif c == "avail":
                print("\n".join(f"{'  ' * d}{k} {n}: {f}/{t} free" for k, n, f, t, d in lot.availability()))
            elif c == "relocate":
                print(f"-> ticket of {a[0]} moved to {lot.relocate(a[0], a[1], clock).name}")
            elif c == "rate":
                lot.set_rate(a[0], int(a[1]), role, clock)
                print(f"-> {a[0]} rate {a[1]} VND/h for new tickets")
            elif c == "addzone":
                print(f"-> zone {a[1]} added to {a[0]} with {lot.add_zone(a[0], a[1], a[2], int(a[3]), role, clock, 1 if "vip" in a[4:] else None, "vip" in a[4:])} slots (not saved across restarts)")
            elif c == "report":
                rep = lot.report(clock - timedelta(hours=float(a[0]) if a else 24), clock, role)
                print(f"-> {rep['transactions']} payments, total {rep['total']} VND, floors {rep['by_floor']}, zones {rep['by_zone']}, {len(rep['audit'])} audit entries")
            elif c == "reserve":
                s = lot.reserve(a[0], a[1], clock + timedelta(minutes=int(a[2]) if len(a) > 2 else 30))
                print(f"-> reserved {s.name}" if s else "-> REJECTED: no free slot of that type")
            elif c == "find":
                s = lot.find(a[0])
                print(f"-> {lot.route(s)}" if s else "-> vehicle not found")
            elif c == "scan":
                r = {s.name: s.state == "Occupied" for s in lot.root.slots()}
                if len(a) > 1:
                    r[a[1]] = True if a[0] == "ghost" else None
                print("\n".join(f"  ALERT {k}: {m}" for k, _, m in lot.scan(r, clock)) or "-> no alerts")
            elif c == "evacuate":
                print(f"-> evacuation started, {lot.evacuate(clock, role, 'fire_alarm' if a else 'operator')} slots blocked, barriers {lot.barriers}")
            elif c == "endevac":
                print(f"-> evacuation ended, {lot.end_evacuation(clock, role)} slots unblocked, barriers {lot.barriers}")
            elif c in ("lock", "unlock"):
                print(f"-> {lot.lock(a[0], role, clock, c == 'lock')} slot(s) changed")
            elif c == "tick":
                clock += timedelta(minutes=int(a[0]))
            elif c == "tree":
                print(f"lot mode: {'EVACUATING' if lot.evac else 'normal'}, barriers {lot.barriers}")
                print("\n".join(show(lot.root)))
            elif c == "log":
                for r in lot.audit[-15:]:
                    print(f"  {r['t'][11:16]} {r['event']:<16} {r['plate']:<10} {r['detail']}")
            elif c == "reset":
                lot, clock = Lot(), checks.T0
            else:
                print("unknown command, type help")
        except IndexError:
            print("missing argument, type help")
        except (ValueError, LookupError, PermissionError, checks.PaymentFailed, checks.ManualEntryRequired, checks.PlateMismatch, checks.OfflineError) as e:
            print(f"-> ERROR: {e}")


MENU = [
    ("Scenarios (use cases from xlsx, run on the simulation)", lambda d: scenarios(d)),
    ("Quality check of requirements data", lambda d: (head("Quality report"), print(rm.quality_text(d)))),
    ("Traceability matrix", lambda d: (head("Traceability matrix"), print(rm.trace_text(d)))),
    ("Requirement tree and change impact", tree_and_impact),
    ("Run automated tests (writes results to xlsx)", run_tests),
    ("NFR classification and verification", nfr),
    ("Risk analysis and hazard mitigations", risk),
    ("State machines and diagrams (Mermaid)", diagrams),
    ("Show parking tree", lambda d: lot_tree()),
    ("Live gate scenarios (console style, asks for input)", lambda d: live.menu()),
    ("Export report appendix (Markdown)", export),
    ("Interactive parking mode", lambda d: interactive()),
]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    data = rm.load(DATA)
    if sys.argv[1:] == ["all"]:
        head("Scenarios")
        scenarios(data, all_=True)
        for _, fn in MENU[1:8]:
            fn(data)
        lot_tree()
        head("Simulated day")
        print(checks.day_sim())
        head("Live gate scenarios")
        for _, fn in live.LIVE:
            fn(live.auto)
            print()
        export(data)
        return print("\nAll features ran.")
    while True:
        head("Smart Parking System")
        for i, (title, _) in enumerate(MENU, 1):
            print(f"  {i:>2}. {title}")
        print("   0. Quit")
        c = input("Choose: ").strip()
        if c == "0":
            return
        if c.isdigit() and 1 <= int(c) <= len(MENU):
            MENU[int(c) - 1][1](data)
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()
