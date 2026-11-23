"""Ticket catalogue: the ground truth the synthetic generator draws from.

Phrasings mirror what we observed on the Kaveri service desk floor (see docs/01-discovery-notes.md):
formal emails, terse one-liners, Tanglish, Hinglish and angry escalations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STYLES = ("formal", "terse", "tanglish", "hinglish", "angry")
STYLE_WEIGHTS = (0.38, 0.24, 0.14, 0.12, 0.12)

# ServiceNow-style user-facing categories that callers pick (often wrongly) on the portal.
USER_CATEGORIES = ("Inquiry / Help", "Software", "Hardware", "Network", "Database", "Other")

CITIES = ("Chennai", "Mumbai", "Bengaluru", "Kolkata", "Hyderabad", "Singapore", "Jakarta", "Ho Chi Minh City")

SOFTWARE = ("Power BI Desktop", "Python 3.12", "Zoom", "Notepad++", "Tableau Reader", "AutoCAD LT", "Postman", "7-Zip")

APP_ERRORS = {
    "KLMS": ("Error 500 - Internal Server Error on Consignment screen", "ORA-01017: invalid username/password"),
    "WMS-Pro": ("Scanner sync failed: code WMS-4021", "Putaway task stuck in PENDING"),
    "FleetTrack": ("GPS feed not updating since morning", "NullReferenceException at VehicleController.GetRoute"),
    "RouteOptima": ("Route plan job timed out after 300s", "Map tiles not loading"),
    "CustomsLink": ("Bill of entry upload failed: XML schema validation error", "Session expired while filing"),
}


@dataclass(frozen=True)
class CategorySpec:
    name: str
    weight: float  # share of overall volume
    risk_tier: str
    assignment_group: str
    applications: tuple[str, ...]
    app_weights: tuple[float, ...]
    user_category: str  # what a well-informed caller would pick on the portal
    priority_weights: dict[str, float]  # ServiceNow priority "1".."5"
    templates: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # style -> [(short, body)]
    month_end_multiplier: float = 1.0


P_STANDARD = {"2": 0.05, "3": 0.55, "4": 0.40}
P_LOW = {"3": 0.35, "4": 0.50, "5": 0.15}
P_URGENT = {"1": 0.55, "2": 0.45}

CATALOG: list[CategorySpec] = [
    CategorySpec(
        "password_reset", 0.18, "low", "SD-L1",
        ("SSO", "Outlook", "KLMS", "PeopleDesk"), (0.55, 0.2, 0.15, 0.10),
        "Inquiry / Help", P_STANDARD,
        {
            "formal": [
                ("Password reset request for {app}",
                 "Hello team,\n\nI am unable to log in to {app} as my password has expired. "
                 "Kindly reset it at the earliest.\n\nThanks and regards,"),
                ("Unable to login - password expired",
                 "Hi,\nMy {app} password expired over the weekend and the self-service portal is not "
                 "sending the OTP. Please help reset."),
            ],
            "terse": [("pwd reset", "{app} password reset pls"), ("forgot password {app}", "cant login. reset")],
            "tanglish": [
                ("password reset pannunga", "Hi team, {app} password marandhuten, login aagala. "
                 "Konjam reset pannunga pls"),
                ("{app} login aagala", "password expire aagiduchu nu solludhu, reset panna mudiyuma?"),
            ],
            "hinglish": [
                ("password reset karna hai", "Hi, {app} ka password bhool gaya hoon, login nahi ho raha. "
                 "Please reset kar do"),
            ],
            "angry": [
                ("STILL CANT LOGIN!!!", "This is the THIRD time I am raising this. {app} password reset "
                 "is not working and I have a client call in 20 mins. Fix it NOW."),
            ],
        },
    ),
    CategorySpec(
        "account_unlock", 0.08, "low", "SD-L1",
        ("SSO", "KLMS", "WMS-Pro", "FinCore"), (0.5, 0.25, 0.17, 0.08),
        "Inquiry / Help", P_STANDARD,
        {
            "formal": [("Account locked - {app}", "Dear support,\nMy {app} account got locked after multiple "
                        "login attempts. Request you to unlock it.\nRegards,")],
            "terse": [("account locked", "{app} locked out. unlock pls")],
            "tanglish": [("account lock aagiduchu", "{app} la 3 times wrong password pottuten, ippo account "
                          "lock aagiduchu. unlock pannunga")],
            "hinglish": [("account lock ho gaya", "{app} account lock ho gaya hai, unlock kar dijiye")],
            "angry": [("Locked out AGAIN", "Your system locked me out of {app} again. Unlock immediately, "
                       "warehouse is waiting on me.")],
        },
    ),
    CategorySpec(
        "how_to_question", 0.06, "low", "SD-L1",
        ("Teams", "Outlook", "KLMS", "RouteOptima"), (0.3, 0.3, 0.25, 0.15),
        "Inquiry / Help", P_LOW,
        {
            "formal": [("How to export report from {app}", "Hi team,\nCould you please guide me on how to export "
                        "the weekly report from {app} to Excel?\nThanks,"),
                       ("Query on {app} shared calendar", "Hello,\nHow do I share my calendar in {app} "
                        "with my team?\nRegards,")],
            "terse": [("how to?", "how to add delegate in {app}"), ("{app} question", "where is export option")],
            "tanglish": [("{app} la eppadi", "{app} la report eppadi download panradhu? option kaanom")],
            "hinglish": [("{app} mein kaise", "{app} mein report export kaise karte hain?")],
        },
    ),
    CategorySpec(
        "vpn_access", 0.09, "low", "NET-Connectivity",
        ("GlobalVPN",), (1.0,),
        "Network", P_STANDARD,
        {
            "formal": [("VPN not connecting from home", "Hi team,\nI am working from {city} today and GlobalVPN "
                        "keeps disconnecting every 5 minutes. Error: 'Gateway unreachable'.\nRegards,"),
                       ("Need VPN access", "Hello,\nI have joined the {city} branch recently and need VPN "
                        "access for remote work. Manager approval attached.\nThanks,")],
            "terse": [("vpn down", "vpn not connecting"), ("VPN issue", "gateway unreachable error")],
            "tanglish": [("VPN connect aagala", "veetla irundhu VPN connect aagave maatengudhu, "
                          "gateway unreachable nu varudhu")],
            "hinglish": [("VPN nahi chal raha", "ghar se VPN connect nahi ho raha, bar bar disconnect ho raha hai")],
            "angry": [("VPN broken for 2 days", "VPN has been broken for TWO DAYS. Nobody has called me back. "
                       "I cannot work.")],
        },
    ),
    CategorySpec(
        "email_issue", 0.07, "low", "EUC-Messaging",
        ("Outlook",), (1.0,),
        "Software", P_STANDARD,
        {
            "formal": [("Outlook not syncing", "Hi,\nMy Outlook has stopped receiving emails since morning. "
                        "Sent items are stuck in Outbox.\nRegards,"),
                       ("Mailbox full", "Hello team,\nI am getting a 'mailbox full' warning and cannot send "
                        "mails. Please increase quota.\nThanks,")],
            "terse": [("mail not working", "outlook not syncing"), ("mailbox full", "cant send mail")],
            "tanglish": [("mail varala", "morning la irundhu Outlook la mail edhuvum varala")],
            "hinglish": [("mail nahi aa raha", "subah se Outlook mein mail nahi aa rahe")],
        },
    ),
    CategorySpec(
        "software_install", 0.07, "medium", "EUC-Desktop",
        ("Endpoint",), (1.0,),
        "Software", P_LOW,
        {
            "formal": [("Software installation request - {software}", "Hi team,\nPlease install {software} on my "
                        "laptop for the quarterly analysis work. Manager approval in thread.\nThanks,")],
            "terse": [("install {software}", "need {software} installed")],
            "tanglish": [("{software} install pannanum", "en laptop la {software} install pannanum, admin "
                          "rights illa")],
            "hinglish": [("{software} install karna hai", "mere laptop pe {software} install kar do please")],
        },
    ),
    CategorySpec(
        "printer_issue", 0.04, "low", "EUC-Desktop",
        ("PrintHub",), (1.0,),
        "Hardware", P_LOW,
        {
            "formal": [("Printer not working - {city} office 3rd floor", "Hi,\nThe printer on the 3rd floor "
                        "in {city} office is showing 'offline'. Jobs are stuck in queue.\nRegards,")],
            "terse": [("printer offline", "3rd floor printer not printing")],
            "tanglish": [("printer work aagala", "3rd floor printer offline nu kaatudhu")],
        },
    ),
    CategorySpec(
        "laptop_hardware", 0.08, "medium", "EUC-Hardware",
        ("Endpoint",), (1.0,),
        "Hardware", P_STANDARD,
        {
            "formal": [("Laptop battery draining fast", "Hello team,\nMy laptop battery drains from 100% to 20% "
                        "within an hour. Requesting a replacement.\nThanks,"),
                       ("Laptop not booting", "Hi,\nMy laptop is stuck on the boot screen since yesterday. "
                        "I have a demo tomorrow.\nRegards,")],
            "terse": [("laptop slow", "laptop very slow, hangs"), ("keyboard not working", "few keys not working")],
            "tanglish": [("laptop on aagala", "laptop boot aagama logo la ye nikkudhu")],
            "hinglish": [("laptop hang ho raha", "laptop bahut slow hai, bar bar hang ho raha")],
            "angry": [("Third laptop issue this month", "This is ridiculous. Third time this month my laptop "
                       "has died. Replace it.")],
        },
    ),
    CategorySpec(
        "app_access_request", 0.09, "medium", "APP-Access",
        ("KLMS", "WMS-Pro", "FleetTrack", "CustomsLink", "RouteOptima"), (0.3, 0.25, 0.2, 0.15, 0.1),
        "Inquiry / Help", P_STANDARD,
        {
            "formal": [("Access request - {app}", "Hi team,\nI have moved to the {city} operations team and "
                        "require access to {app} (role: Planner). My manager has approved.\nRegards,")],
            "terse": [("need {app} access", "pls give {app} access, role planner")],
            "tanglish": [("{app} access venum", "naan pudhu team la join panniruken, {app} access kudunga")],
            "hinglish": [("{app} access chahiye", "mujhe {app} ka access chahiye, manager ne approve kiya hai")],
        },
    ),
    CategorySpec(
        "app_error", 0.13, "medium", "APP-Support-L2",
        ("KLMS", "WMS-Pro", "FleetTrack", "RouteOptima", "CustomsLink"), (0.3, 0.25, 0.2, 0.15, 0.1),
        "Software", {"2": 0.15, "3": 0.6, "4": 0.25},
        {
            "formal": [("{app} error: {error}", "Hello team,\nWhile working on {app} in {city} we are getting "
                        "the following error:\n\n{error}\n\nThis is blocking dispatch. Please check.\nRegards,")],
            "terse": [("{app} not working", "{error}"), ("{app} issue", "getting error. see attached")],
            "tanglish": [("{app} la error", "{app} open panna '{error}' nu error varudhu, dispatch nikkudhu")],
            "hinglish": [("{app} mein error", "{app} mein '{error}' aa raha hai, kaam ruk gaya hai")],
            "angry": [("{app} DOWN - trucks waiting", "{app} is throwing '{error}' and 14 trucks are waiting "
                       "at the {city} hub. Escalating to my manager.")],
        },
    ),
    CategorySpec(
        "payroll_access", 0.03, "high", "HR-Systems",
        ("PeopleDesk",), (1.0,),
        "Inquiry / Help", P_STANDARD,
        {
            "formal": [("Access to payroll module", "Hi team,\nI have taken over as HR coordinator for {city} "
                        "and need access to the PeopleDesk payroll module.\nRegards,")],
            "terse": [("peopledesk payroll access", "need payroll module access")],
            "tanglish": [("payroll access venum", "PeopleDesk la payroll module access venum, pudhu role")],
        },
        month_end_multiplier=2.5,
    ),
    CategorySpec(
        "payroll_discrepancy", 0.02, "high", "HR-Systems",
        ("PeopleDesk",), (1.0,),
        "Other", P_STANDARD,
        {
            "formal": [("Salary credited is incorrect", "Dear team,\nMy salary for this month seems lower than "
                        "expected. Overtime hours are not reflected in the payslip on PeopleDesk.\nRegards,")],
            "terse": [("salary wrong", "payslip amount wrong, OT missing")],
            "tanglish": [("salary kammiya vandhurukku", "indha maasam salary kammiya vandhurukku, OT add aagala")],
            "hinglish": [("salary kam aayi", "is mahine salary kam aayi hai, overtime nahi juda")],
            "angry": [("WHERE IS MY OVERTIME", "Overtime missing from salary AGAIN. Fix this today.")],
        },
        month_end_multiplier=3.0,
    ),
    CategorySpec(
        "expense_claim_issue", 0.03, "high", "FIN-Systems",
        ("FinCore",), (1.0,),
        "Software", P_STANDARD,
        {
            "formal": [("Expense claim stuck in approval", "Hi team,\nMy travel expense claim from {city} has "
                        "been stuck in 'Pending Approval' in FinCore for 3 weeks.\nRegards,")],
            "terse": [("expense claim stuck", "fincore claim not moving")],
            "hinglish": [("claim approve nahi hua", "FinCore mein expense claim 3 hafte se pending hai")],
        },
        month_end_multiplier=2.0,
    ),
    CategorySpec(
        "security_phishing", 0.02, "critical", "SEC-SOC",
        ("Outlook",), (1.0,),
        "Other", {"1": 0.2, "2": 0.6, "3": 0.2},
        {
            "formal": [("Suspicious email received", "Hi team,\nI received an email asking me to verify my "
                        "payroll details via a link. The sender looks suspicious. I have not clicked it.\n"
                        "Regards,"),
                       ("Clicked a suspicious link", "Hello,\nI accidentally clicked a link in an email "
                        "claiming to be from HR. It asked for my password. Please advise urgently.")],
            "terse": [("phishing?", "got weird mail asking password")],
            "angry": [("Why are we getting scam mails", "Third phishing mail this week. What is security doing?")],
        },
    ),
    CategorySpec(
        "network_outage", 0.01, "critical", "NET-Major-Incident",
        ("CorpNetwork",), (1.0,),
        "Network", P_URGENT,
        {
            "formal": [("Network down - {city} warehouse", "Team,\nComplete network outage at the {city} "
                        "warehouse since 10:15. Scanners, KLMS and phones are all down.\nRegards,")],
            "terse": [("NETWORK DOWN {city}", "entire site offline")],
            "angry": [("{city} SITE DOWN - URGENT", "Whole {city} site is offline. Operations stopped. "
                       "Call me immediately.")],
        },
    ),
]

CATALOG_BY_NAME = {c.name: c for c in CATALOG}
