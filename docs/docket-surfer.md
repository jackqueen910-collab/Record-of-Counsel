# Docket Surfer and My Files

Docket Surfer is the manual case-to-document workflow. It never calls a model,
requires a counsel match or decides which filings are relevant. The older AI
motion finder remains optional. Both attorney and litigant searches can retrieve
supported district-court dockets; litigant searches do not infer attorney clients.

Open Docket Surfer uses selected cases, or all cases in the current search when
none are selected. Case details also have a direct Docket Surfer button. Missing
dockets use the existing case-specific preview and fresh spending cap. Saved
full-case reports are reused across searches in this person's account. Reload
saved docket reads local data only; a paid refresh is not yet exposed. The
retrieval timestamp identifies the saved snapshot.

The viewer lists parties/counsel and full docket entries with dates, numbers,
text search and newest/oldest ordering. Unsupported rows produce warnings.
Source HTML never executes in the app. Text-only entries remain readable.

Select filings and review a maximum of $3 per new PDF, then enter a fresh cap.
Purchases use the existing court-web adapter with the official API session.
A recognized, case-bound Document Selection Menu with ordinary court links
is saved without a purchase POST for that entry. The batch stops for review;
reopen the docket to choose the main document or individual attachments with
a new cap. JavaScript menus, unfamiliar viewers, transcripts and prices above
$3 remain unsupported. Unknown responses retain their charge reservations and
never retry automatically. Menu support is narrower than full PACER coverage.

My Files automatically lists docket HTML and PDFs across searches, including
older Document Grabber purchases. Users can search, open saved PDFs, download
files or export a selected ZIP with a readable CSV index. Empty exports are
rejected. Missing files block repeat purchases. An unresolved document receipt,
including from the older AI flow, blocks new manual purchases for that person.

Purchases are kept by default and storage usage is displayed. Retention controls
are not enabled; docket sources also support rebuilding research exports.
Files live on the computer running ROC and do not synchronize across devices.

Offline tests cover account isolation with shared PACER access, manual purchases,
cache reuse, menus, attachments, source changes, caps, uncertain receipts and ZIP
contents. Live PDF/menu acceptance still requires explicit spending approval.
District docket validation does not establish PDF retrieval coverage.
