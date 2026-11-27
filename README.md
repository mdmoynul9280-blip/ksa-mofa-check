# KSA MOFA — Applicant Check (local / online)

A local copy of the Saudi visa portal's **applicant application search**
(`visa.mofa.gov.sa/visaperson/getapplicantdata`) with English labels.

The portal has no public JSON API: it is a server-rendered form that needs a
session cookie, an anti-forgery token and a human-typed captcha. This folder
reproduces that flow on your own machine so the form is comfortable to use.

## Start

Double-click **`start.bat`** (or run `python server.py`).
A browser tab opens at `http://127.0.0.1:8777/`.

1. Passport number
2. Nationality — type to filter (`bangladesh`, `BGD`, `বাংলাদেশ`)
3. Visa type
4. Port of arrival / embassy (`dhaka`, `218`)
5. Type the 6-character code from the image
6. **Check application**

Each lookup needs a fresh captcha — that is by design and is never automated.

**Check application** renders only the official **Entered application** card
directly below the form — no new tab and no extra click. It uses the portal's own
CSS for that card (passport photo, barcodes, declaration and the `Visa platform`
message dialog), isolated in a shadow DOM so the search form keeps its own
styling. The site's header/footer/banner strips are intentionally omitted.
The response goes straight from the local helper to your browser; nothing is
written to disk.

## Online (mobile / another PC / anywhere)

Use `server-online.py` — the same helper, but it binds `0.0.0.0:$PORT` instead
of `127.0.0.1:8777`. Deploy these four files together:

- `index.html`
- `data.js`
- `server-online.py`
- `render.yaml` (or `Procfile` for Railway/Fly.io)

Render Blueprint example is already in `render.yaml`: start command is
`python server-online.py`, health check is `/api/health`. No pip packages are
needed.

After deploy, open the HTTPS URL on any device. The captcha is still typed by
hand, and the result still renders inline. Keep **one instance/worker**: the
token and captcha key live in memory, so a restart or scale-up resets the
session — reload the page to get a fresh captcha. Free-tier sleep does the same.

> Deploying moves the lookup through your hosting server instead of straight
> from your own computer. That removes the original "no third-party server"
> property, and the portal may block datacenter IPs. Use it only for
> applications you are allowed to look up.

## Files

| File | Purpose |
|---|---|
| `index.html` | the form + inline official-look result viewer (English UI, Arabic labels auto-translated) |
| `data.js` | dropdown lists + Arabic→English glossary, generated |
| `server.py` | tiny local helper: keeps the portal session, fetches the captcha/photo/assets, forwards the POST |
| `server-online.py` | same helper for hosting: binds `0.0.0.0:$PORT`, adds `/api/health` |
| `render.yaml` / `Procfile` | one-click Render / Railway start config |
| `update-data.py` | regenerate `data.js` from the live site |
| `start.bat` | Windows launcher |

## If the portal changes

```bat
python update-data.py
```

That re-reads the nationality / visa-type / embassy lists and rebuilds the
Arabic→English label glossary. If the result page stops being translated, the
glossary is where to look.

## Notes

- Requests go straight from your computer to `visa.mofa.gov.sa`. Nothing is
  sent anywhere else, and no data is stored on disk except the last values you
  typed (kept in your browser's localStorage).
- Use it only for applications you are allowed to look up (your own or your
  organisation's). Automated mass queries against a government portal are
  against its terms of use.
- This is an unofficial helper, not affiliated with the Saudi Ministry of
  Foreign Affairs.
