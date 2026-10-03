# PACER accounts and saved searches

ROC uses successful authentication with the official PACER API to open an account's saved searches. There is no separate registration, password database or browser-login fallback. Account checks do not submit a PCL search, retrieve a docket, buy a document or call Claude.

## Behavior

- Signed out: the saved-search area prompts for PACER sign-in and returns no real account histories.
- Signed in: searches, name rules, downloaded reports, model analyses, documents and receipts belong to that PACER username. Sharing PACER credentials means sharing this history.
- Expired/rejected PACER session: saved work stays readable in the already verified ROC browser session. A new PACER request requires reconnection. A failed reconnect to the same username retains the saved view and never retries automatically.
- Switch account: the previous identity is removed as soon as a valid new sign-in attempt is accepted locally. If PACER rejects the new credentials, neither account is exposed. Successful sign-in opens only the new account. A pending action from the previous account is canceled.
- Sign out: clears browser identity, saved views, selected cases, previews and that browser's PACER session. Other independently signed-in browsers retain their own sessions. Tabs within one browser profile share the account cookie; their next refresh clears stale views, and stale actions are rejected immediately.
- Stop/restart: discards all browser identities and PACER sessions. Account files remain; sign in again to access them. Stop ROC controls the whole local process, including all its accounts.

An active operation must reach an idle boundary before its account signs out or switches. Pause it or let it finish first. This avoids discarding a connection while it is settling a paid request. Different accounts have independent queues; browsers sharing an account share its one-operation-at-a-time queue. Each operation captures the initiating browser's connection and client billing code, so another browser reading the same history cannot replace them.

## Identity and storage

The authentication API supplies a session token and result, not a stable account ID or canonical username. ROC uses the exact trimmed username from a successful response and deliberately does not merge case variants or other aliases. Use the same spelling/case each time. Changing a PACER username does not automatically migrate its earlier ROC history.

`runs/workspace/account-identity.key` contains a random local HMAC key. Persistent account folder names are HMAC-SHA256 digests of verified usernames: `runs/workspace/accounts/<digest>/`. Usernames are kept in memory for display; passwords, MFA codes, PACER tokens and browser cookies are never written to these files. Preserve the identity key with the entire workspace in backups or moves; losing/changing it disconnects account logins from their old folders. It is distinct from the shared ROC Anthropic API key, which remains in its existing owner-managed secret store.

The loopback launcher token opens ROC but cannot list or read an account's work. A separate random HttpOnly, SameSite=Strict session cookie binds each browser to a verified identity. Cookies rotate on sign-in attempts and sign-out, and expire when the backend process ends. Account-specific HTTP requests also carry a view version, preventing an old tab's action from targeting a newly selected account. Requests resolve files within the bound account, including exports, document ZIPs, name rules and quotes; the UI filter is not the access-control boundary.

The normal interface disables demo creation and hides its entry point. Development browser tests explicitly enable it with `make_server(..., enable_demo=True)`. In that test interface, an anonymous demo has its own `guests/<random>/` folder. It makes no PACER/AI requests, exposes no real history and is not migrated into an account. Guest files are retained locally but cannot be reopened after their browser identity is discarded. Signed-in development demos are saved to the current account. Older flat interface run folders and CLI runs are never silently assigned to the first signer.

This remains a local application. Its account separation governs HTTP access, not access by someone who can read the underlying Windows files or inspect this process. It is not a public hosting/login service. Production `serve()` and the desktop launcher construct `Accounts`; passing a bare `Workspace` to `make_server` is an internal offline test harness only.

## Verification

Offline tests cover distinct/shared account histories, failed login and reconnect, expiry, restart, cookie rotation, stale views, every run/download/action route, guest isolation, credential-free persisted JSON, concurrent browser billing-session selection and shutdown during authentication. Browser tests cover first-login search continuation, sign-out, switching, separate browser contexts, stale tabs, dialog/data clearing and reconnect. Synthetic authentication and search responses are used; no live acceptance login or paid request is required by these checks.
