# Personal ROC accounts

Each person signs into ROC with an email and password, independently of PACER.
Signup includes password confirmation. Passwords allow 5-128 characters without
composition rules and are stored as salted scrypt hashes. Email uniqueness is
case insensitive. No Alerter accounts or passwords are imported.

The first registration on this local installation is its owner; subsequent
registrations are members. The loopback launcher capability is required for
registration and login. This bootstrap is not an internet-hosted signup design.

Each identity has a server-generated ID and workspace under
`runs/workspace/users/<id>`. Searches, name rules, receipts and purchased files
belong to that person. PACER passwords, MFA values and sessions stay in memory.
Changing or disconnecting PACER never changes ROC identity. Two ROC users can
share a PACER username without sharing history. Two browsers of the same ROC
user share saved work, but operations capture their initiating PACER connection.

Random HttpOnly, SameSite=Strict cookies identify in-memory sessions, which
expire after 12 hours and end on restart. Password reset revokes sessions and
requests a pause after any current paid request settles. Login/signup/recovery
have persistent rate limits. Account changes clear UI state and invalidate
outstanding responses; scoped requests also check the account-view generation.

Recovery uses a 30-minute single-use token; only its digest is stored. Responses
do not confirm whether an email exists. A sender must be configured before
recovery mail can be sent. Until then the interface reports that limitation,
rather than claiming mail was sent. The PACER Alerter sender is not reused
without authorization. Local recovery links work on this computer while ROC
is running. Shared hosting and cross-device synchronization are not provided.

## Older workspaces

Folders under `accounts/<opaque-PACER-key>` are preserved and never automatically
assigned to new ROC users. The owner may connect the original PACER username
and explicitly choose Import older searches. This copies prior runs and files
into the owner's personal workspace, preserves originals and resumes no work.
Repeated imports skip existing run IDs. Name rules copy only when the target
has no existing rules file. Interrupted import folders remain for inspection.

Isolation is enforced by the application. It does not protect files from people
with filesystem access to the computer. Sessions and downloads never accept a
browser-provided account ID or path as a workspace selector.
