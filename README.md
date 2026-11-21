# CivicResolve
**AI-assisted citizen grievance management and resolution**

## Chosen vertical
Citizen-to-government public service delivery. CivicResolve gives residents a structured way to report civic issues, routes reports to a suggested public department, and provides a tracking ID so citizens can follow progress.

## Problem and approach
Citizens often do not know which government office handles a local issue or how to follow up after reporting it. CivicResolve brings reporting, explainable triage, department routing, officer updates and status tracking into one responsive web application.

### How it works
1. A citizen creates an account and submits a title, description and location.
2. The triage engine checks issue-related keywords and suggests a category and department.
3. A transparent priority rule detects explicit emergency terms and potential multi-resident impact.
4. The platform creates a unique tracking ID.
5. An authorized officer reviews the report and updates its status and public-facing note.
6. The citizen signs in to view their reports or uses the tracking page with the tracking ID.

## Features
- Citizen registration and sign-in with password hashing.
- CSRF protection on forms, input length limits and server-side validation.
- Citizen-only access to their own grievance details.
- Officer-only dashboard and status update workflow.
- Explainable category, department and priority recommendations.
- Tracking IDs, status history fields, and public-facing officer notes.
- Responsive interface, semantic labels, keyboard-friendly forms and status text that does not rely only on color.
- Automated tests for core routing, access control and triage.

## AI / decision logic and assumptions
This submission uses a deterministic, explainable triage engine. It is described as AI-assisted decision support, not as a connected large language model. Category selection is based on keyword matches; priority is based on explicit urgency indicators and broad community-impact terms. An officer must verify every recommendation. The system does not make legal decisions, dispatch emergency services, or guarantee a government response.

Department names are illustrative defaults and must be mapped to the actual local government's jurisdiction and workflow before real deployment. The public home page shows aggregate metrics only. Anonymous tracking reveals a limited status summary and does not expose the report description or precise location. Tracking IDs use 128 bits of random entropy.

## Run locally
Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
flask --app app run --debug
```
Open http://127.0.0.1:5000

The database is created automatically. SQLite is used by default for local development.

## Create an officer account
After activating the virtual environment and setting `SECRET_KEY`:

```bash
flask --app app create-officer
```
Use a unique email and a strong password of at least 10 characters. Do not publish credentials or seed an officer password in source code.

## Run tests
```bash
pytest -q
```

## Deploy to Google Cloud Run
1. Push this repository to a **public GitHub repository** with one branch (`main`).
2. Create a Google Cloud project and enable Cloud Run and Cloud Build.
3. For persistent production data, provision PostgreSQL (for example, Cloud SQL) and set `DATABASE_URL` to its SQLAlchemy connection URL. Do not rely on the container's local SQLite filesystem for durable production data.
4. Set a strong random `SECRET_KEY` as a Cloud Run secret/environment variable. Set `COOKIE_SECURE=1` behind HTTPS.
5. Deploy the container from the repository using Cloud Build / Cloud Run. Configure the service to listen on port 8080.
6. Create the first officer account through a secure one-off administrative process connected to the production database. Do not expose the CLI creation command as a public web route.

Example container build:
```bash
gcloud builds submit --tag gcr.io/PROJECT_ID/civicresolve
gcloud run deploy civicresolve --image gcr.io/PROJECT_ID/civicresolve \
  --region asia-south1 --allow-unauthenticated \
  --set-env-vars COOKIE_SECURE=1
```
For production, also configure `DATABASE_URL` and `SECRET_KEY` securely. `--allow-unauthenticated` allows citizens to reach the sign-in and registration pages; protected dashboards still require login.

## Security and production limitations
- HTTPS and secure cookie mode should be enabled in production.
- Use a managed persistent database, backups, monitoring and least-privilege database credentials.
- Add email/SMS notifications, official government identity/department integrations and audit logs only after obtaining authorized access.
- Public tracking is a convenience feature. For sensitive reports, require authentication for every detail view.
- The prototype does not upload evidence files, geolocate users, or send reports to a real government department.
- Add rate limiting, account recovery, email verification, retention policy and an independent security review before handling real citizen data.

## Repository size
The project contains source code and lightweight assets only. Do not commit virtual environments, database files, secrets, build output or personal data.
