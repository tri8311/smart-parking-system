"""Regenerate English tables. Add --tables-only to reuse recorded evidence.
Requires openpyxl; never writes to the original project workbook.
"""
from pathlib import Path
from collections import Counter
from datetime import datetime
import contextlib, hashlib, io, json, platform, re, sys, textwrap
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE/'source'))
import checks, live, rm

def esc(v):
    if v is None or v=='': return '---'
    tr={'\\':r'\textbackslash{}','&':r'\&','%':r'\%','$':r'\$','#':r'\#','_':r'\_','{':r'\{','}':r'\}','~':r'\textasciitilde{}','^':r'\textasciicircum{}','<':r'\textless{}','>':r'\textgreater{}'}
    return ''.join(tr.get(c,c) for c in re.sub(r';(?=\S)','; ',str(v))).replace('\n',' ')

def out(name,text):
    (BASE/'generated'/(name+'.tex')).write_text(text+'\n',encoding='utf-8')

LABELS={'Stakeholder register':'tab:stakeholders','Case-study elicitation':'tab:elicitation','Sample process':'tab:process','Functional specifications':'tab:functional',
  'Non-functional requirements':'tab:nfr-spec','Complete index of':'tab:uc-index','NFR results':'tab:nfr-results','Functional requirement link':'tab:coverage',
  'Functional and non-functional trace':'tab:trace','Complete results of':'tab:tests','Requirements data quality':'tab:quality','Impact summary':'tab:changes',
  'Hazard register':'tab:hazards','Risk matrix':'tab:risk-grid','Test-case source':'tab:tca-summary'}

def table(caption,headers,rows,widths=None,size='footnotesize',stretch=None):
    label=next((v for k,v in LABELS.items() if caption.startswith(k)),None)
    widths=widths or [1/len(headers)]*len(headers)
    spec=''.join(r'>{\raggedright\arraybackslash}p{\dimexpr '+f'{w:.5f}'+r'\linewidth-2\tabcolsep\relax}' for w in widths)
    head=' & '.join(r'\textbf{'+esc(h)+'}' for h in headers)+r' \\ \midrule'
    n=str(len(headers))
    lines=[r'\par'+(r'\needspace{'+str(len(rows)+5)+r'\baselineskip}' if len(rows)<=8 else '')+r'\begingroup'+'\\'+size+r'\setlength{\tabcolsep}{4pt}',r'\begin{longtable}{'+spec+'}',
        r'\caption{'+esc(caption)+r'}'+(r'\label{'+label+'}' if label else '')+r'\\',r'\toprule',head,r'\endfirsthead',
        r'\multicolumn{'+n+r'}{l}{\small\itshape '+esc(caption)+r' (continued)}\\',
        r'\toprule',head,r'\endhead',r'\midrule',
        r'\multicolumn{'+n+r'}{r}{\scriptsize Continued on the next page}\\',r'\endfoot',r'\bottomrule\endlastfoot']
    if stretch: lines[0]+=r'\renewcommand{\arraystretch}{'+str(stretch)+'}'
    lines+=[' & '.join(esc(c) for c in row)+r' \\' for row in rows]
    return '\n'.join(lines+[r'\end{longtable}\endgroup'])

REQ=[
('Vehicle entry','Issue a ticket with ID, entry time, and a matching slot when capacity exists.','Create the ticket; mark the slot Occupied; reject a plate already in the lot.'),
('Availability display','Show free-slot counts by floor and zone.','Displayed counts equal Free leaves in each branch.'),
('Exit and fee calculation','Calculate fees using started hours and the vehicle-type rate.','Outside peak hours, without surcharge or exemption, fee equals rounded-up hours times the rate.'),
('Payment','Confirm payment before opening the exit barrier in normal mode.','Failed payment retains the vehicle and ticket; allow another method. Evacuation takes priority.'),
('Reservation','Hold a matching slot until 15 minutes after expected arrival.','Do not assign it to another vehicle; release a no-show only after the hold expires.'),
('Locate a vehicle','Look up a parked vehicle by plate.','Return floor, zone, slot, and distance; report not found without an active ticket.'),
('Lost ticket','Look up the plate, verify ownership, charge the fee, and record the incident.','No acceptance criteria in the workbook; proposed criteria and fee discrepancies are discussed in Section 3.1.3.'),
('Zone lock/unlock','Allow administrators to lock or unlock a floor or zone.','Locked slots are not assigned to new vehicles; existing vehicles remain and exit through the appropriate workflow.'),
('Access control','Restrict administrative functions to authenticated roles.','Attendants cannot lock zones; only administrators change configuration.'),
('Transaction audit log','Record entry, exit, and payment transactions.','Events carry timestamps and can be retrieved for a reporting period.'),
('Plate recognition','Accept recognition confidence of at least 80%; otherwise require manual input.','Accept 80%; 79% or camera failure requires manual confirmation.'),
('Nearest-slot allocation','Select the nearest Free slot of the correct type.','Exclude locked/faulty slots; respect reservations and VIP eligibility; break distance ties by tree order.'),
('Sensor reconciliation','Alert when sensor readings differ from ticket data.','Report unticketed or missing vehicles; a silent sensor makes its slot Blocked.'),
('Overstay alert','Alert when parking exceeds 24 hours.','A sensor scan detects and records a stay longer than 24 hours.'),
('Emergency evacuation','Open every barrier, stop new tickets, and waive exit fees.','Triggered by fire alarm or administrator; cancel reservations; only an administrator may end evacuation.'),
('Peak-hour pricing','Apply a 1.5 multiplier to hours starting within 07-09 or 17-19.','Evaluate each started hour in UTC+7; the upper boundary is excluded.'),
('Occupancy surcharge','Apply 25% when same-type occupancy exceeds 80% at issuance.','Store the multiplier on the ticket; code checks after allocation and includes Reserved slots.'),
('Staff authentication','Authenticate with a password and lock after three consecutive failures.','Valid login returns a role; the third failure locks the account in the current Accounts object.'),
('Cancel reservation','Cancel a reservation and release its slot immediately.','The slot becomes available or Blocked if locked; distinguish expired reservations.'),
('Lot/rate configuration','Allow administrators to change rates and add valid zones.','New rates affect only new tickets; reject nonpositive rates and duplicate zone names.'),
('Revenue report','Aggregate revenue and audit records over a period.','Zone totals reconcile with floor and overall totals; access is restricted to manager/admin.'),
('Fee override','Record a reasoned request for manager/admin approval.','Amount is nonnegative; attendants cannot approve; an approved amount replaces the exit total.'),
('Monthly/VIP passes','Manage plate-based passes and waive parking for valid passes.','Expired passes at entry use normal pricing; VIP slots require VIP eligibility; energy remains chargeable.'),
('EV slots','Allocate dedicated slots to electric vehicles.','EVs do not receive car slots and vice versa; reject EV entry when EV capacity is exhausted.'),
('Charging fee','Add total kWh times 3,500 VND to the fee.','Accept positive kWh only for EV tickets; two normal hours and 10 kWh total 59,000 VND.'),
('Exit-plate verification','Compare the exit-camera plate with the ticket plate.','Mismatch or unreadable plates block exit and are logged; authorized confirmation may release the vehicle.'),
('Grace period','Waive parking fees for stays of at most 15 minutes.','15 minutes costs zero; 16 minutes costs one started hour; do not deduct the grace period from longer stays.'),
('Daily fee cap','Cap each 24-hour block at ten hourly rates before surcharge.','24 normal hours cost 100,000 VND for a car; 25 normal hours cost 110,000 VND.'),
('Offline operation','Continue local entry/exit when disconnected.','Simulated cash/card and evacuation work; refuse online payment, reservations, and pass changes.'),
('Recovery synchronization','Mark offline records and process them on reconnection.','Clear offline marks and log the count; the prototype does not transmit records to a real server.'),
('Barrier interlock','Refuse to close a barrier while a vehicle is detected or the presence sensor fails.','A close command with a vehicle present, a sensor fault, or power loss leaves the barrier open and is logged; a clear way allows closing.'),
]

UC=[
('Vehicle entry','Driver','A matching slot is available; the lot is not evacuating.','Read and confirm the plate; select a reserved or nearest matching slot; issue a ticket and guidance; open and close the entry gate.','Reject full lots, evacuation, and duplicate plates. Low confidence requires manual input.','Ticket Active; slot Occupied; entry logged.'),
('Exit and payment','Driver','An active ticket exists.','Scan the ticket; verify the plate; calculate and display the fee; take payment; close the ticket, release the slot, and operate the exit gate.','Missing ticket invokes lost-ticket handling. Failed payment retains the ticket; permit retry or another method. A plate mismatch needs confirmation.','Ticket Closed; slot Free or Blocked if locked; revenue recorded.'),
('Reserve a slot','Driver','A matching slot exists; the central server is reachable.','Enter type, plate, and arrival time; find a slot; mark Reserved and confirm the hold.','Reject if full. Release a no-show after the 15-minute hold when expiry is checked.','Reservation recorded; slot unavailable to other vehicles.'),
('Locate a vehicle','Driver','The vehicle has an active ticket in the lot.','Enter the plate; look up the ticket; return floor, zone, slot, and distance.','If absent from active tickets, display vehicle not found.','Location displayed without modifying data.'),
('Lock or unlock a zone','Administrator','An administrator account exists; the lot is in a suitable mode.','Log in; select a floor/zone; lock or unlock; update Free/Blocked states and log the action.','Deny insufficient privileges. Occupied slots remain until exit, then become Blocked if locked. Faulty slots remain blocked on unlock.','The locked area cannot take new visitors; existing reservations follow the stated policy.'),
('Handle a lost ticket','Gate Attendant','A driver reports a lost ticket and provides a plate.','Verify ownership; find the ticket; calculate the charge; close the ticket, release the slot, and log the incident.','No record: escalate to the manager. No ownership proof: refuse exit and record the denial.','Ticket Closed; charge and incident recorded; REQ-07 criteria still require completion.'),
('Recognize an entry plate','Driver / Entry Camera','The vehicle is stopped at entry; recognition or manual input is available.','The simulated camera returns plate and confidence; accept at least 80%; pass the confirmed plate to entry.','Below 80% or camera offline: request manual input and log the reason.','A confirmed plate is available before ticket issuance.'),
('Assign the nearest slot','Driver','A suitable slot is available.','Filter by type, state, and VIP eligibility; select minimum distance; break ties by tree order; display the route.','Skip locked/full nearest zones; return no slot when suitable capacity is exhausted.','A valid slot is selected for ticket issuance.'),
('Detect parking discrepancies','Gate Attendant / Slot Sensor','Sensor readings and ticket data are available.','Read each slot; reconcile occupancy with tickets; alert and log; relocate a ticket if staff confirm the vehicle is in another slot.','No heartbeat makes the slot Blocked. Stays over 24 hours trigger an overstay alert. An unticketed vehicle triggers an unauthorized-vehicle alert.','Discrepancies recorded; faulty slots cannot receive new vehicles.'),
('Emergency evacuation','Administrator / Fire Alarm','An administrator command or fire-alarm signal is received.','Open all barriers; stop tickets; cancel reservations and lock the lot; waive exit fees; let an administrator end and restore operation.','Deny attendant requests to trigger through administration or to end evacuation. A fire alarm bypasses login.','Barriers stay open during evacuation; completion restores Normal mode.'),
('Pay a dynamic fee','Driver','An active ticket exists.','Split the stay into started hours; apply peak multipliers; cap each 24-hour block; apply the ticket surcharge; display and collect the fee.','Grace-period or monthly/VIP stays waive parking. Without peak hours or surcharge, apply base pricing.','The exit workflow records a fee consistent with the rules.'),
('Log in','Attendant / Manager / Administrator','The account exists and is not locked.','Enter credentials; authenticate; return the role; establish the session and record the event.','Wrong credentials increment the count; three consecutive failures lock the account; locked accounts cannot log in.','A role is returned or access is denied.'),
('Cancel a reservation','Driver','A reservation exists for the plate.','Find the reservation; confirm cancellation; clear the hold; set Free or Blocked if locked; log the action.','Show distinct messages for missing and expired reservations.','The slot can be reallocated when eligible.'),
('Configure lot and rates','Administrator','An administrator account exists.','Log in; select rate change or new zone; validate; apply and log.','Reject nonpositive rates, invalid types, duplicates, or insufficient privileges. Existing tickets retain their rates.','New tickets use the new rate; new zones are usable in the current session.'),
('View availability','Driver','The lot hierarchy is available.','Traverse lot, floors, and zones; count Free leaves; display free and total counts.','Locked or evacuating branches cannot accept new allocations; compare counts with flags and states.','Query does not modify data.'),
('Revenue and audit report','Manager','Logged in as manager or administrator.','Select a period; read payments and lost-ticket charges; group by zone/floor; return totals and audit events.','No transactions gives zero revenue. Deny attendants access.','Report displayed without modifying source records.'),
('Override a fee','Attendant / Manager','An active ticket and an authorized requesting role exist.','Enter plate, amount, and reason; create Pending request; manager/admin approves; set the replacement amount and log the decision.','Reject negative amounts, missing reasons, or attendant approval. Rejection retains the calculated fee.','The override is effective only after approval.'),
('Use a monthly/VIP pass','Driver','A plate-based pass is valid at entry.','Recognize the plate; find the pass; prioritize VIP slots for VIP holders; record pass type on the ticket; waive parking at exit.','Expired passes use ordinary pricing. Monthly passes cannot use VIP slots. EV energy remains chargeable.','Exit closes the ticket; the accepted pass makes parking free.'),
('Register/remove a pass','Administrator','Administrator privileges and central-server connectivity exist.','Log in; enter plate, monthly/vip type, and expiry date; validate, save, and log; support pass removal.','Reject unknown types, past dates, disconnection, or insufficient privileges.','Pass registered or removed in the simulation data.'),
('Charge an EV','Driver / Charging Station','An EV slot and valid EV ticket are available.','Allocate the nearest EV slot; receive and accumulate kWh; charge parking plus energy on exit.','Reject when EV slots are full; do not allocate EV slots to cars; reject nonpositive kWh.','Energy is included in the EV ticket total.'),
('Verify an exit plate','Driver / Exit Camera','A ticket and camera plate or explicit unreadable result are available.','Read the exit plate; find the ticket; compare; proceed to payment on a match.','Mismatch/unreadable: hold the barrier, log, and call staff; authorized confirmation may release the vehicle.','Release or hold is recorded; omitted input remains an implementation limitation.'),
('Work offline','Gate Attendant / Central Server','Local data remains usable.','Detect disconnection; switch to local mode; allow entry/exit with simulated cash/card; mark logs; process marks and record their count on reconnection.','Refuse online payment, reservations, and pass updates. Evacuation remains available offline.','Local transactions remain in audit; actual network synchronization is not implemented.'),
]

def main():
    for folder in ['generated','evidence','data']: (BASE/folder).mkdir(exist_ok=True)
    d=rm.load(BASE/'data'/'parking_data.xlsx')
    ep=BASE/'evidence'/'verification.json'
    if '--tables-only' in sys.argv:
        runtime=json.loads(ep.read_text(encoding='utf-8'))
        tests,nfr,quality=runtime['tests'],runtime['nfr'],runtime['quality']
    else:
        tests,nfr,quality=checks.run_tests(d),checks.nfr_results(d),rm.quality(d)
        uc_runs='\n\n'.join(checks.run_uc(d,u['id']) for u in d['use_cases'])
        buf=io.StringIO()
        with contextlib.redirect_stdout(buf):
            for _,fn in live.LIVE: fn(live.auto); print()
        runtime={'generated_at':datetime.now().astimezone().isoformat(),'python':platform.python_version(),'os':platform.platform(),'processor':platform.processor(),
            'execution':'Sequential, single Python process','dataset':{k:len(v) for k,v in d.items()},'test_summary':dict(Counter(v[0] for v in tests.values())),
            'tests':tests,'nfr':nfr,'quality':quality,'hazard_demos':checks.hazard_demos()}
        ep.write_text(json.dumps(runtime,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        (BASE/'data'/'dataset.json').write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        for name,text in [('use_case_runs',uc_runs),('live_runs',buf.getvalue()),('day_sim',checks.day_sim()),('impact',rm.impact_text(d)),('quality',rm.quality_text(d)),('traceability',rm.trace_text(d))]:
            (BASE/'evidence'/(name+'.txt')).write_text(text,encoding='utf-8')
        (BASE/'data'/'diagrams.mmd').write_text('\n\n'.join(['%% Slot',*rm.state_mmd(),rm.usecase_mmd(d),rm.DFD_MMD,rm.dep_mmd(d)]),encoding='utf-8')
        manifest={str(p.relative_to(BASE)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((BASE/'source').glob('*.py'))}
        manifest['data/parking_data.xlsx']=hashlib.sha256((BASE/'data'/'parking_data.xlsx').read_bytes()).hexdigest()
        (BASE/'evidence'/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    out('environment',table('Verification environment',['Property','Value'],[('Recorded at',runtime['generated_at']),('Python',runtime['python']),('Operating system',runtime['os']),('Processor',runtime['processor'] or 'Not provided by the operating system'),('Execution','Sequential; single process; time.perf_counter')],[.22,.78]))
    phases=['Stakeholder interviews','Prioritization workshop','Actor and use-case modeling','SRS specification','Review and validation','Establish change control']
    out('process',table('Sample process activities',['ID','Activity','Duration (days)','Output'],[(p['id'],phases[i],p['duration_days'],p['outputs']) for i,p in enumerate(d['process_activities'])],[.12,.34,.14,.4]))
    roles=['Operations manager','IT services manager','Driver representative','Gate attendant','Fire safety inspector']
    needs=['Capacity, revenue, rates, and reports','Security, configuration, availability, and recovery','Availability, reservations, location, and transparent fees','Fast handling, camera faults, lost tickets, and alerts','Evacuation, open barriers, and event records']
    out('stakeholders',table('Stakeholder register',['ID','Role','Influence / interest','Main needs'],[(s['id'],roles[i],s['influence']+' / '+s['interest'],needs[i]) for i,s in enumerate(d['stakeholders'])],[.13,.24,.23,.4]))
    lv=['High','Medium','Low']
    out('power-interest',table('Power/interest grid from the source dataset',['Influence / interest']+lv,[[a]+[', '.join(s['id'] for s in d['stakeholders'] if s['influence']==a and s['interest']==b) or '---' for b in lv] for a in lv],[.28,.24,.24,.24]))
    tech=['Interview','Prioritization workshop','Questionnaire','Observation','Pricing and guidance workshop']
    findings=['Fast entry/exit, clear availability, and lost-ticket handling.','Security, availability, and audit are priorities.','Reservations and plate-based location are desired; survey sample size is absent.','Rain reduces recognition; wrong-slot parking; barriers should open within five seconds.','Peak and occupancy pricing; nearest-slot guidance.']
    out('elicitation',table('Case-study elicitation register',['ID / technique','Date / participants','Findings','Links'],[(e['id']+' '+tech[i],str(e['date'])+'; '+e['participants'],findings[i],e['requirements_derived']) for i,e in enumerate(d['elicitation_sessions'])],[.2,.23,.32,.25]))
    out('candidates',table('Candidate functional requirements backlog',['ID','Requirement','Priority / status','Source'],[(r['id'],REQ[i][0],r['priority']+' / '+r['status'],r['source']) for i,r in enumerate(d['requirements'])],[.13,.4,.3,.17],'scriptsize',1.0))
    out('functional',table('Functional specifications and verification criteria',['ID / name','Required behavior','Acceptance / verification interpretation'],[(r['id']+' '+REQ[i][0],REQ[i][1],REQ[i][2]) for i,r in enumerate(d['requirements'])],[.22,.32,.46],stretch=1.1))
    nfrtext=['Slot-assignment p95 at most one second.','Availability during operating hours at least 99.5%.','New attendants handle a lost ticket within two minutes after 20 minutes of training.','Encrypt 100% of PII fields at rest and in transit; target AES-256 / TLS 1.2+.','Support growth in floors and slots; metric and target are missing.','All barriers open within five seconds of an evacuation trigger.']
    out('nfr-spec',table('Non-functional requirements',['ID / category','Specification and target','Priority / verification'],[(n['id']+' / '+n['category'],nfrtext[i],n['priority']+' / '+n['verification_method']) for i,n in enumerate(d['nfrs'])],[.23,.52,.25]))
    out('uc-index',table(f"Complete index of {len(d['use_cases'])} use cases",['ID','Use case / actor','Related requirements'],[(u['id'],UC[i][0]+' / '+UC[i][1],u['related_requirements']) for i,u in enumerate(d['use_cases'])],[.11,.5,.39]))
    details=[]
    for i,u in enumerate(d['use_cases']):
        title,actor,pre,flow,alt,post=UC[i]
        details += [r'\subsection{'+esc(u['id']+' -- '+title)+'}',r'\noindent\textbf{Actor:} '+esc(actor)+r'\quad\textbf{Traceability:} '+esc(u['related_requirements'])+'.',r'\par\textbf{Preconditions:} '+esc(pre),r'\par\textbf{Main flow:} '+esc(flow),r'\par\textbf{Alternative flows and exceptions:} '+esc(alt),r'\par\textbf{Postconditions:} '+esc(post)]
    out('usecases','\n'.join(details))
    rows=[]
    for n in d['nfrs']:
        nid=n['id']
        if nid in nfr: kind='Sequential Python timing' if nid!='NFR-02' else 'Assumed outage model'; result=nfr[nid].replace('PASS:','PASS (simulated):',1).replace('FAIL:','FAIL (simulated):',1)
        else:
            kind='Not verified'
            result={'NFR-03':'Not evaluated with new users.','NFR-04':'Storage and transport encryption are not implemented.','NFR-05':'Metric/target missing; no acceptance conclusion.'}[nid]
        rows.append((nid,result,kind))
    out('nfr-results',table('NFR results and evidence scope',['ID','Recorded result','Evidence type'],rows,[.13,.62,.25]))
    tr=rm.trace(d); fr=[r for r in tr if r[0].startswith('REQ')]
    out('coverage',table('Functional requirement link coverage',['Criterion','Count','Coverage'],[('Has a use-case link',sum(bool(r[1]) for r in fr),'100%'),('Has a test-case link',sum(bool(r[2]) for r in fr),'100%'),('Has a direct elicitation-session link',sum(bool(r[5]) for r in fr),f'{round(100*sum(bool(r[5]) for r in fr)/len(fr))}%')],[.6,.2,.2],'scriptsize'))
    out('trace',table('Functional and non-functional traceability',['Requirement','Use case','Test case','HZ / CR / EL'],[(r[0],', '.join(r[1]),', '.join(r[2]),'; '.join(', '.join(c) for c in r[3:] if c)) for r in tr],[.13,.27,.34,.26],'scriptsize'))
    counts=Counter(v[0] for v in tests.values())
    out('test-summary',table('Recorded automated test summary',['Total','Passed','Failed','Not Run'],[[len(tests),counts['Passed'],counts['Failed'],counts['Not Run']]]))
    out('tests',table(f"Complete results of {len(d['test_cases'])} automated tests",['ID / requirements','Test objective','Actual result'],[(t['id']+' / '+t['related_requirements'],t['title'],tests[t['id']][0]+': '+tests[t['id']][1]) for t in d['test_cases']],[.23,.28,.49],'scriptsize'))
    out('quality',table('Requirements data quality findings',['Level','ID','Finding'],quality,[.12,.16,.72]))
    _,nodes=rm.dep_tree(d); rows=[]
    for cr in d['change_requests']:
        hit=rm.ids(cr['affected_requirements']); impacted=set(hit)
        for rid in hit: impacted |= rm.descendants(nodes[rid])
        nt=sum(bool(set(rm.ids(t['related_requirements'])) & impacted) for t in d['test_cases'])
        nu=sum(bool(set(rm.ids(u['related_requirements'])) & impacted) for u in d['use_cases'])
        rows.append((cr['id']+' / '+cr['status'],', '.join(hit),f'{len(impacted)} requirements ('+', '.join(sorted(impacted))+f'); {nu} use cases; {nt} tests',f"cost {cr['cost_impact_days']} days; schedule {cr['schedule_impact_days']} days"))
    out('changes',table('Impact summary for the two change requests',['CR','Direct requirements','Impact closure (requirements, use cases, tests)','Sample estimate'],rows,[.12,.2,.52,.16]))
    hz=d['hazards']
    out('risk-grid',table('Risk matrix S x L',['S / L','1','2','3'],[[s]+[', '.join(h['id'] for h in hz if h['severity']==s and h['likelihood']==l) for l in (1,2,3)] for s in (4,3,2,1)],[.1,.3,.3,.3]))
    mit=[('Owner data exposure','Encrypt backups and restrict access; no encryption evidence.'),('Incorrect fees','Test time/time-zone boundaries; overrides need manager approval.'),('Duplicate slot assignment','Reject repeated Occupied transitions; concurrent races are unverified.'),('Barrier strikes a vehicle','The gate refuses to close on a vehicle, a sensor fault, or power loss (REQ-31); no physical interlock is tested.'),('Unauthorized zone changes','Authenticate, lock after three failures, and restrict to admin.'),('Plate misrecognition','80% threshold; manual fallback; confidence is simulated.'),('Evacuation barriers fail to open','Fail-open and payment bypass; physical and power-loss tests are needed.'),('Missing/incorrect sensor data','Reconcile with tickets, alert, and block faulty slots.'),('Excessive dynamic fees','Fixed multipliers 1.5 x 1.25; show breakdown; clarify the cap.'),('Exit with another ticket','Compare exit and ticket plates; approval and logging on mismatch.'),('Network loss stops gates','Local entry/exit, marked logs; actual network synchronization is absent.'),('Override abuse','Separate request/approval roles; enforce distinct user identities in future.')]
    rows=[]
    for h in sorted(hz,key=lambda x:-x['severity']*x['likelihood']):
        title,action=mit[int(h['id'][-2:])-1]
        rows.append((h['id']+' '+title,f"{h['severity']} / {h['likelihood']} / {h['severity']*h['likelihood']}",h['sil'],action,h['related_requirements']))
    out('hazards',table('Hazard register and mitigation scope (ordered by R)',['ID / hazard','S / L / R','SIL','Mitigation and limitations','Traceability'],rows,[.22,.1,.08,.38,.22]))
    out('e2e',table('Seven recorded live scenarios',['Scenario','Expected observations'],[('1. Entry / ALPR','Plate, confidence, slot, ticket, and open/close gate states.'),('2. Exit / payment','A fee breakdown crossing peak hours; total and simulated receipt.'),('3. Reservation to exit','Held slot, arrival within hold, sensor scan, and fee.'),('4. Evacuation drill','Open gates; refused entry; zero-fee exit; administrator ends evacuation.'),('5. Ticket swap','Mismatch held; staff confirmation audited.'),('6. Network outage','Local entry, refused online payment, successful cash exit, and marked-log recovery.'),('7. EV / monthly pass','Energy plus parking; monthly pass waives parking.')],[.32,.68]))
    day=(BASE/'evidence'/'day_sim.txt').read_text(encoding='utf-8')
    out('day',r'\begin{Verbatim}[fontsize=\scriptsize,frame=single]'+'\n'+'\n'.join(textwrap.fill(line,width=100,subsequent_indent='  ',replace_whitespace=False) if len(line)>100 else line for line in day.splitlines())+'\n'+r'\end{Verbatim}')
    out('data-index',table('Complete sheet and schema index',['Sheet','Rows','Fields'],[(name,len(rows),', '.join(rows[0]) if rows else '') for name,rows in d.items()],[.24,.11,.65],'scriptsize'))
    tca=d['test_case_analysis']
    extra=[table('System actors',['ID','Name','Role'],[(a['id'],a['name'],a['description']) for a in d['actors']],[.13,.25,.62]),table('Tools listed in the source dataset',['ID / name','Purpose','Limitations'],[(t['id']+' '+t['name'],t['purpose'],t['limitations']) for t in d['tools']],[.24,.42,.34]),r'\subsection*{Test-case source analysis}\noindent The workbook holds '+str(len(tca))+r' links between tests and sources (requirements, use cases, flows, NFRs, and change requests); they are summarized here and listed row by row in \texttt{data/dataset.json}.\par',table('Test-case source analysis summary',['Source type','Links','Distinct sources','Positive / Negative / Boundary'],[(t,sum(1 for r in tca if list(r.values())[2]==t),len({list(r.values())[3] for r in tca if list(r.values())[2]==t}),' / '.join(str(sum(1 for r in tca if list(r.values())[2]==t and list(r.values())[4]==c)) for c in ('Positive','Negative','Boundary'))) for t in sorted({list(r.values())[2] for r in tca})],[.3,.15,.2,.35]),r'\noindent All fields from every sheet are available in \texttt{data/dataset.json}; the workbook snapshot is \texttt{data/parking\_data.xlsx}.']
    out('appendix-data','\n'.join(extra))
    out('source-index',table('Bundled source files',['File','Lines','Snapshot path'],[(p.name,len(p.read_text(encoding='utf-8').splitlines()),'source/'+p.name) for p in sorted((BASE/'source').glob('*.py'))],[.22,.15,.63]))
    excerpts=[]
    selections=[('Legal state transitions','sim.py','def move(','class PaymentFailed'),('Authentication and account lockout','sim.py','class Accounts:','class Lot:'),('Identifier-based test runner','checks.py','def run_tests(',None),('95th-percentile measurement','checks.py','def perf_p95(','def evac_seconds(')]
    for idx,(title,fn,start,end) in enumerate(selections,1):
        src=(BASE/'source'/fn).read_text(encoding='utf-8'); a=src.index(start); b=src.index(end,a) if end else len(src)
        name=f'snippet-{idx}.txt'; (BASE/'generated'/name).write_text(src[a:b].strip()+'\n',encoding='utf-8')
        excerpts += [r'\subsection*{'+esc(title)+'}',r'\noindent\textit{Source: '+esc(fn)+'}',r'\VerbatimInput[fontsize=\scriptsize,baselinestretch=1,frame=single,numbers=left,numbersep=4pt]{generated/'+name+'}']
    out('snippets','\n'.join(excerpts))
    print(json.dumps({'mode':'tables-only' if '--tables-only' in sys.argv else 'fresh verification','tests':dict(counts),'nfr':nfr},ensure_ascii=False))

if __name__=='__main__': main()
