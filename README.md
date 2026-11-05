# Wiener Staatsoper: two automatically updated calendar subscriptions

Two separate subscriptions, both for **opera only** (not ballet/concerts):

1. `vienna-opera.ics` — all opera performances, including Saturdays. Performance entries contain date, public start time when provided, work, composer, available conductor/cast, and the sale-start information in the description. **No sale reminder alarms are attached to performance events.**
2. `vienna-ticket-sales.ics` — starts of regular seated-ticket sales. Each unique sale-opening date and time is a **single 30-minute event** listing all the opera performances covered by that opening. Each sale event includes four embedded `VALARM` notifications: **3 months, 1 month, 1 week, and 1 day beforehand**. Reminders do **not** generate additional calendar events.

The standard seated-ticket policy is the first day of the month two months before the performance month, 10:00 Europe/Vienna; website-published individual sale dates take precedence. Calculated dates are labeled and may have exceptions. Standing-room sales are **not** included in the presale calendar.

## Set up in GitHub

1. Create a **public** repository called `vienna-opera-calendar`.
2. Upload the three files `operabook_calendar.py`, `requirements.txt`, and `.github/workflows/update.yml` preserving the folder path.
3. In **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**.
4. Open **Actions → Update Vienna State Opera calendars → Run workflow** and check that it finishes successfully.
5. In macOS Apple Calendar, use **File → New Calendar Subscription** and subscribe to both feeds below (replacing `YOUR_USERNAME` with your own GitHub name):

   - `https://raw.githubusercontent.com/YOUR_USERNAME/vienna-opera-calendar/main/vienna-opera.ics`
   - `https://raw.githubusercontent.com/YOUR_USERNAME/vienna-opera-calendar/main/vienna-ticket-sales.ics`

   Select automatic refresh (daily or more frequent where supported).

## Important limitation

Apple Calendar may not reliably execute embedded `VALARM` notifications from **subscribed/read-only calendars**. The ICS events contain the four requested reminder alarms, but notifications must be tested on your Mac/iPhone; subscribing alone does not guarantee that alerts will fire. No additional reminder events are created.

## Verification

The scraper relies on the public English website's HTML structure and must be tested in GitHub Actions. It fails rather than replace existing feeds with empty files when insufficient entries are extracted. Operas not yet announced cannot appear. Changes to the website may require parser updates.
