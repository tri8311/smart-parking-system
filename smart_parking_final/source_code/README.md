# Smart Parking System

Terminal requirements-management and simulation tool for the course CSE703095 Software Requirements (Phenikaa University). It loads a requirements workbook (`parking_data.xlsx`, 13 sheets), checks its quality, traces requirements to use cases and tests, and runs the use cases and tests on a simulated parking lot.

The parking lot is a tree: **Lot > Floor > Zone > Slot**. Cameras, sensors, payment, and the network are simulated; nothing talks to hardware.

## Run

Requires Python 3 and `openpyxl` (`python -m pip install openpyxl`).

```text
python parking.py        interactive menu
python parking.py all    run every feature once, no prompts, and export out/report_appendix.md
```

Set `PARKING_DATA=<path>` to read a different workbook (useful when the real one is open in Excel).

### Menu

| # | Item | What it does |
|---|------|--------------|
| 1 | Scenarios | Runs a use case (22 in the workbook) on the simulation and prints its flows |
| 2 | Quality check | Rule-based findings on requirements (ambiguity, atomicity, missing acceptance, links) |
| 3 | Traceability matrix | Requirement to use case to test case to hazard / change request / elicitation |
| 4 | Requirement tree and change impact | Dependency tree; closure of requirements, use cases, and tests hit by a change request |
| 5 | Automated tests | Runs 67 tests and writes status, actual result, executor, and date to the `test_cases` sheet |
| 6 | NFR verification | Classification (ISO/IEC 25010) and measured results for NFR-01, NFR-02, NFR-06 |
| 7 | Risk analysis | Hazard matrix, SIL level, mitigations, and software demonstrations |
| 8 | Diagrams | Mermaid state machines, use cases, data flow, dependencies |
| 9 | Parking tree | Prints the lot hierarchy and slot states |
| 10 | Live gate scenarios | Seven console-style gate scenarios (entry, exit, journey, evacuation drill, ticket swap, offline, EV and pass) |
| 11 | Export | Writes a Markdown report appendix to `out/` |
| 12 | Interactive mode | A command line on a persistent lot (state saved in `state.json`) |

Running tests (item 5) overwrites the result columns in the workbook. Close the workbook in Excel or WPS first.

### Interactive commands

Demo accounts: `admin1/admin123`, `att1/att123`, `mgr1/mgr123`. Type `help` in the mode for the full list.

```text
enter <plate> <car|moto|ev> [quality 0-1 [manual-plate]]     quote <plate>
exit <plate> [cash|card|online|fail] [seen=<plate>] [approve]   lost <plate> [noproof]
reserve <plate> <car|moto> [minutes]    cancel <plate>    find <plate>    avail
scan [ghost <slot> | silent <slot>]     relocate <plate> <slot>
lock|unlock <zone|floor>   evacuate [fire]   endevac
override <plate> <vnd> <reason>   approve|reject <OV-n>   charge <plate> <kWh>
pass <plate> <monthly|vip> <days>   unpass <plate>   rate <car|moto> <vnd>
addzone <floor> <name> <car|moto|ev> <slots> [vip]   report [hours]
gateway on|off   network on|off   tick <minutes>   tree   log   reset   quit
```

## Files

| File | Role |
|------|------|
| `sim.py` | The model: `Node` tree, `Lot`, `Ticket`, `Accounts`; state machines for slot, ticket, and lot; fee calculation; all business rules |
| `rm.py` | Requirements-management tools: workbook loader, quality rules, traceability, requirement tree, change impact, NFR and risk text, Mermaid generators |
| `checks.py` | Use-case runners (`UC`, 22), automated tests `tc01` to `tc67` (`TESTS`), NFR measurements, hazard demonstrations, simulated day |
| `live.py` | The seven live gate scenarios, interactive (`ask`) or automatic (`auto`) |
| `parking.py` | Menu, interactive command line, export |
| `parking_data.xlsx` | The dataset: requirements, NFRs, actors, use cases, test cases, hazards, change requests, and more |
| `out/` | Generated Markdown appendix |
| `docs/smart_parking_report/` | The English LaTeX report built from this project (see its own `README.md`) |

## Business rules (in `sim.py`)

- **States.** Slot: Free, Reserved, Occupied, Blocked. Ticket: Active, Paid, Lost, Closed. Lot: Normal, Evacuating. Moves go through `move(current, new, flow)` and invalid moves raise an error.
- **Rates** per started hour: car 10,000 VND, motorcycle 3,000, EV 12,000. EV energy costs 3,500 VND/kWh.
- **Fee.** Started hours; hours that start in 07:00-09:00 or 17:00-19:00 (UTC+7) cost x1.5; stays of at most 15 minutes are free; each started 24 hours costs at most 10 hourly rates; then x1.25 when occupancy of that vehicle type is above 80%. A lost ticket costs 24 hourly rates plus 50,000 VND. An approved override replaces the whole fee. Monthly and VIP pass holders pay no parking fee, only energy.
- **Entry.** A plate is read with a confidence threshold of 0.8; below it, or with the camera off, the plate is entered manually. Slots are assigned nearest first; VIP slots only for VIP pass holders; EV slots for EVs.
- **Exit.** Payment is confirmed before the barrier opens. The plate seen at exit must match the ticket, or a staff member (attendant, manager, admin) approves the mismatch. Online payment is refused when the gateway or network is down.
- **Reservation.** A slot is held until 15 minutes after the expected arrival.
- **Evacuation.** All barriers open, new entries are refused, exits are free. An administrator ends it.
- **Barrier safety (REQ-31).** `close_gate` refuses to close when a vehicle is present, the sensor fails, or power is lost, and logs `gate_close_refused`.
- **Offline mode.** With the network down, entry and cash exit work locally; log records are marked offline and counted again when the network returns.
- **Roles.** Staff-only commands check the logged-in role; override requests need a manager or admin to approve.

## Tests

`python parking.py` item 5, or `python parking.py all`, runs all 67 tests every time. Each test is a function `tcNN` in `checks.py`, mapped by id in `TESTS`; the workbook row for the same id supplies the title and requirement links. A test passes when its asserts hold; the returned text becomes the actual result. A test id with no function is recorded as Not Run.

## Known limits

- ANPR, sensors, the payment gateway, and the network are simulated, so behavior on a real site is unknown.
- Demo passwords are plaintext in memory; the workbook and `state.json` are not encrypted (NFR-04 is not implemented).
- Offline mode is a flag on log records; there is no real synchronization queue.
- A pass is checked only at entry; expiry during a stay is not rechecked.
- Requester and approver of an override are checked by role, not by identity.
- NFR timings come from sequential Python calls and vary by machine; uptime (NFR-02) comes from an assumed outage model.
- The workbook keeps three deliberate quality defects (REQ-07, REQ-10, NFR-05) so the quality checker has something to report.
