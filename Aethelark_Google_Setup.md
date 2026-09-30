# Connect Google (one-time, ~5–8 minutes)

Google sign-in is fully built and the code is ready — it's **off only because
Google requires an OAuth client registered under _your_ account**. No app can ship
that credential; you create it once, paste it in, and log in. After that the
eagle's Google tools connect: **Gmail brief, Calendar, Contacts, Tasks**.

Until then, Settings → Google shows **SETUP NEEDED** and everything else (voice,
WhatsApp, swarm, browser) works normally.

---

## Step 1 — Create the OAuth client (Google Cloud Console)

1. Open the console → https://console.cloud.google.com/ and **create a project**
   (top bar → New Project), or select one.

2. **Enable the APIs** the scopes need — APIs & Services → **Library**, search each
   and click **Enable**:
   - **Gmail API**
   - **Google Calendar API**
   - **People API**  (contacts)
   - **Tasks API**

3. **OAuth consent screen** (APIs & Services → OAuth consent screen):
   - User type: **External** → Create.
   - App name / user support email / developer email = yours.
   - **Add scopes** → add:
     `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`,
     `.../auth/gmail.readonly`, `.../auth/calendar.events`,
     `.../auth/calendar.readonly`, `.../auth/contacts.readonly`, `.../auth/tasks`.
   - **Test users** → add your own Google address
     (`you@example.com`). Unverified apps work for you + up to 100 test
     users — enough for personal use; no Google verification needed.

4. **Credentials** → Create credentials → **OAuth client ID**:
   - Application type: **Desktop app** (important — Desktop allows the loopback
     login with no client secret).
   - Create → **copy the Client ID**
     (`1234567890-abc123.apps.googleusercontent.com`). There is **no secret** to
     copy for this flow.

---

## Step 2 — Connect it (one command)

From the project folder, in this session type:

```
! .venv/bin/python setup_google.py PASTE_YOUR_CLIENT_ID
```

It saves the ID, opens Google's consent page in your browser — **approve it** —
and stores the login. On success it prints `🦅 Connected as <you>`.

(Equivalent manual path: put `"google_client_id": "…"` into
`config/api_keys.json`, then Settings → Google → **Connect**.)

---

## After connecting

- Try **“what did I miss?”** → the Gmail + WhatsApp unread brief now includes real
  mail.
- Settings → Google flips to **LINKED** with your email. **Disconnect** there
  revokes + deletes the token.
- Tokens live in `config/google_token.json`; the eagle refreshes them
  automatically (no re-login).

### Trimming the grant
Want a smaller consent? Edit `SCOPES` in `actions/google_auth.py` — the unread
brief only needs `gmail.readonly`. Fewer scopes = fewer APIs to enable + a shorter
consent screen.

### Notes / reality checks
- `gmail.readonly` is a Google **restricted** scope; Calendar/Contacts/Tasks are
  **sensitive**. All work **unverified** for you + test users. Public distribution
  later needs Google's OAuth verification.
- The redirect is a loopback (`http://127.0.0.1:<random-port>/`) — nothing to
  pre-register, no secret to protect (PKCE).
