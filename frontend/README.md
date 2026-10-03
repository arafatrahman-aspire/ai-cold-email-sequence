# Outreach Console

A small React dashboard for the cold email service: live statistics, enrolling
leads, previewing sequences, editing settings and blocking addresses.

## Run it

The backend must be running first (from the project folder):

```bash
python -m uvicorn app.main:app --app-dir service --port 8080
```

Then, in a second terminal:

```bash
cd frontend
npm install        # first time only
npm run dev
```

Open http://localhost:5173.

The dev server forwards `/api/*` to the backend at `http://localhost:8080`, so
the backend needs no CORS setup. If the backend runs elsewhere, start with
`BACKEND_URL=http://host:port npm run dev`.

You sign in through CMS (admin or marketing role); **Sign out** in the top bar
signs you out of CMS as well. See "Login" in the main README.

## Pages

- **Overview**: headline numbers, the lead pipeline, email statuses, each
  inbox's usage against its daily cap, and buttons to run the background jobs
  now. Refreshes every 30 seconds.
- **Replies**: what prospects sent back (replies, unsubscribes, bounces),
  with an "Open in Gmail" link, and the address that gets an alert email for
  every new reply (with a "Send test alert" button). Needs
  `supabase/migrations/0005_replies.sql`.
- **Enroll**: add leads from the shared `leads` table by `lead_id`, with
  optional job title / company / timezone overrides; shows why any lead was
  refused.
- **Preview**: draft a sequence for a made-up lead. Nothing is saved or sent.
- **Settings**: an on/off switch for all sending, plus every setting as
  editable JSON.
- **Tools**: block an address; check which persona a job title gets.

## Production build

`npm run build` writes static files to `dist/`. `npm run preview` serves them
on port 4173 with the same `/api` forwarding. To host `dist/` elsewhere, put it
behind a web server that forwards `/api/` to the backend.
