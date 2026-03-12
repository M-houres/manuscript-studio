# Manual Smoke Checklist

Prerequisites
1. Configure `.env` for the target environment.
2. Start the app with `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
3. Open the site in a desktop browser.

Core pages
1. Visit `/` and confirm layout, nav, and cards render.
2. Visit `/login` and `/register` and confirm forms render with CSRF hidden inputs.
3. Visit `/privacy` and `/terms` and confirm content renders.
4. Visit `/contact` and confirm contact form renders.

Auth flow
1. Register a new account.
2. Log out and log in with the new account.
3. Confirm the header shows the user state and wallet.

User features
1. Visit `/assets` and confirm the empty state is shown for a new user.
2. Visit `/history` and confirm the empty state plus filters render.

Admin access
1. Visit `/admin` while logged in as a non-admin and confirm a 403 page appears.
2. Log in as an admin and confirm dashboard cards and model config section render.

Error handling
1. Visit a non-existent route like `/missing` and confirm 404 error page.
2. Trigger a form error (for example, submit login with a wrong password) and confirm error toast or inline message appears.
