"""Kaveri's knowledge base, as it exists in the client system. Fictional, written for the pilot."""

KB_ARTICLES = [
    ("KB0010001", "Reset your SSO password",
     "If your SSO password has expired or you have forgotten it, the service desk can issue a temporary password. "
     "It is sent by SMS to your registered mobile number. Log in with it and you will be asked to set a new "
     "password (12+ characters, not one of your last 5). The temporary password is valid for 24 hours."),
    ("KB0010002", "Account locked after failed login attempts",
     "Accounts lock for 30 minutes after 5 failed attempts. The service desk can unlock immediately. If it keeps "
     "locking, check for an old password saved on your phone's mail app or a mapped drive."),
    ("KB0010003", "Export a report to Excel from KLMS",
     "Open the report, click the three-dot menu at the top right, choose Export, then Excel (.xlsx). Exports over "
     "50,000 rows are emailed to you as a link instead of downloading directly."),
    ("KB0010004", "Share your Outlook calendar or add a delegate",
     "In Outlook, open Calendar, right-click your calendar, choose Sharing Permissions and add the colleague. "
     "To add a delegate, go to File, Account Settings, Delegate Access."),
    ("KB0010005", "Set an automatic out-of-office reply",
     "In Outlook choose File, Automatic Replies, select Send automatic replies, set the time range and message "
     "for inside and outside the organisation."),
    ("KB0010006", "Where Teams meeting recordings are saved",
     "Recordings of meetings are saved to the organiser's OneDrive (Recordings folder) and linked in the meeting "
     "chat. Everyone invited can view them; only the organiser can share them outside the invite list."),
    ("KB0010007", "GlobalVPN keeps disconnecting or shows Gateway unreachable",
     "Update GlobalVPN to the latest version from the Company Portal, switch the gateway to the nearest region, "
     "and disable other VPN clients. If the certificate has expired, reinstall the client to renew it."),
    ("KB0010008", "Outlook not syncing or mailbox full",
     "Check the status bar for 'Disconnected'. Restart Outlook in safe mode. Mailbox quota is 50 GB; archive "
     "older mail via File, Tools, Clean Up Old Items."),
    ("KB0010009", "Request software installation",
     "Approved software from the catalogue can be installed through a software request. Non-catalogue software "
     "needs manager and security approval."),
    ("KB0010010", "Office printer shows offline",
     "Power-cycle the printer, check the network cable, and remove and re-add the printer from PrintHub."),
    ("KB0010011", "Bulk upload consignments into KLMS",
     "Use Consignments, Import, download the CSV template, fill one consignment per row and upload. Validation "
     "errors are listed per row before anything is saved."),
]
