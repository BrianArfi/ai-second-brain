---
description: Setup - Connect Gmail, Calendar, Drive, Docs and Sheets with your own Google key, one sign-in
---

Walk the member through connecting Google with their own key. One setup, one sign-in, and
Gmail, Calendar, Drive, Docs and Sheets all work. The scripts and their commands are in
`google-connector/SKILL.md`.

This is the recommended route for full access to Google in this workspace, also when a Google
card in **Settings, Connected tools** shows as connected. The Gmail, Google Calendar and Google
Drive cards keep working for members who already use them, but they only read (Gmail also saves
drafts): they cannot send mail, create Docs or write Sheets. The app says the same thing on
those cards and in their connect dialog, which point here. Do not send the member to the pane
for Google, and never ask them to disconnect a card that works.

**Talk to the member in Indonesian** (unless they write in another language), one step at a
time, in plain words. Wait for "sudah" before the next step. Never paste a whole wall of steps.
The lines in quotes below are what to say; adjust the wording, keep the button names exact.

## 0. Check first

```bash
"${ASB_UV:-uv}" run google-connector/connect.py --status
```

- `CONNECTED as ...` and five `OK` lines: say Google is already connected, as which account,
  and stop.
- `MISSING` or `PROBLEM` lines: skip to the step that fixes them (section 7).
- `NOT CONNECTED`: if `credentials.json` already sits in the workspace root, go to step 5.
  Otherwise start at step 1.

The first run downloads its own Python pieces and can take a minute. Say so before it starts.

Open with:

> "Kita sambungkan Google sekali saja, sekitar 20 menit. Setelah ini aku bisa baca dan
> menyiapkan draft email, lihat dan bikin jadwal kalender, cari file di Drive, bikin Google Docs,
> dan isi Google Sheets. Semua pakai kunci milikmu sendiri, jadi datamu tidak lewat pihak lain."

## 1. Google Cloud project

> "Buka console.cloud.google.com dan login dengan akun Google yang mau disambungkan. Di kiri
> atas, klik pemilih proyek, lalu **New Project**. Kasih nama, misalnya `second-brain`, lalu
> **Create**. Setelah jadi, pastikan proyek itu yang terpilih di kiri atas."

## 2. Turn on the five APIs

Give this one link. It opens a page that enables all five at once:

https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com,calendar-json.googleapis.com,drive.googleapis.com,docs.googleapis.com,sheets.googleapis.com

> "Buka link ini, pastikan nama proyekmu yang tampil, lalu klik **Next** dan **Enable**."

If that page does not work for them, the manual route: **APIs & Services, Library**, search
each one and press **Enable**: Gmail API, Google Calendar API, Google Drive API, Google Docs API,
Google Sheets API.

## 3. Consent screen and Test user

> "Buka menu **APIs & Services, OAuth consent screen** (di tampilan baru namanya **Google Auth
> Platform**). Klik **Get started**. Isi **App name** bebas, misalnya `Second Brain`, pilih
> emailmu di **User support email**, klik **Next**. Di **Audience** pilih **External**, **Next**.
> Isi emailmu di **Contact information**, **Next**, centang persetujuan, lalu **Create**."

> "Sekarang buka **Audience** di menu kiri. Di bagian **Test users**, klik **Add users**, ketik
> alamat Gmail-mu sendiri, lalu **Save**."

Stress this one: without the Test user, Google blocks the sign-in later with `access_denied`.

## 4. The key file (OAuth client, Desktop app)

> "Buka **Clients** di menu kiri (atau **APIs & Services, Credentials, Create credentials,
> OAuth client ID**). **Application type** pilih **Desktop app**, bukan Web application. Kasih
> nama bebas, lalu **Create**. Di jendela **OAuth client created**, klik **Download JSON**."

## 5. Put the file in the workspace

The file must end up as `credentials.json` in the workspace root, next to `CLAUDE.md`.
Do this for the member instead of asking them to: look in their Downloads folder for the newest
`client_secret_*.json` (Windows: `$USERPROFILE/Downloads`, macOS: `~/Downloads`) and copy it to
`credentials.json` in the workspace root. Then say:

> "File kuncinya sudah aku pindahkan ke folder second brain-mu dengan nama `credentials.json`."

If you cannot find it, ask them to drag the downloaded file into the workspace folder. It may
keep its long `client_secret_...json` name: `connect.py` renames it.

## 6. Sign in

```bash
"${ASB_UV:-uv}" run google-connector/connect.py
```

It opens the browser and prints the sign-in link. Show the link to the member as a link too,
in case the browser did not open. Then say:

> "Browser akan terbuka. Ikuti ini:
> 1. Pilih akun Google-mu.
> 2. Muncul **Google hasn't verified this app**. Ini normal, karena aplikasinya buatanmu
>    sendiri. Klik **Advanced**, lalu **Go to Second Brain (unsafe)**.
> 3. Di halaman izin, klik **Select all**, lalu **Continue**.
> 4. Browser lalu menampilkan halaman error seperti **This site can't be reached** atau
>    **localhost refused to connect**. Itu memang begitu. Salin seluruh alamat di address bar
>    (diawali `http://localhost:8080/`) dan tempel di sini."

(The app name in step 2 is whatever they typed as App name in step 3.) The pasted address
holds a one-time code that expires within minutes and is useless without the key file. It is
the one thing this flow takes through the chat. Run step 7 with it straight away:

```bash
"${ASB_UV:-uv}" run google-connector/connect.py --url "<the address they pasted>"
```

## 7. Confirm

Success looks like:

```
STEP 2 of 2: sign-in saved.

CONNECTED as name@example.com
  Gmail     OK
  Calendar  OK
  Drive     OK
  Docs      OK
  Sheets    OK
```

Prove it with one real read, for example the three newest emails:

```bash
"${ASB_UV:-uv}" run google-connector/gmail.py search --query "in:inbox" --limit 3
```

> "Berhasil. Google sudah tersambung sebagai <email>. Contohnya, ini 3 email terbarumu: ...
> Mulai sekarang kamu bisa minta aku cari email, bikin draft, cek jadwal, atau bikin Google Docs.
> Email dan undangan kalender tidak akan terkirim tanpa persetujuanmu."

## When it goes wrong

| What they see or the script says | Cause | Fix |
| :--- | :--- | :--- |
| `Error 403: access_denied` in the browser, or `access_denied` from the script | Their email is not a Test user | Step 3, second part: add the email under **Audience, Test users**. Then step 6 again. |
| `Error 400: redirect_uri_mismatch` | The client is a Web application | Step 4 again with **Desktop app**, replace `credentials.json`, then step 6. The script also catches this before the browser opens. |
| `Error 401: invalid_client`, or `invalid_client` from the script | The key file is wrong, old, or the client was deleted | Download the JSON again from **Clients**, replace `credentials.json`, step 6. |
| `invalid_grant` from `--url` | The code was used already or is too old | Step 6 again, paste the new address straight away. |
| `That address belongs to an older sign-in link` | They used a link from an earlier run | Step 6 again, use only the newest link. |
| `MISSING: permission not given` | Boxes were left unticked on the permission page | Step 6 again, press **Select all**. |
| `PROBLEM: ... API is not enabled` | One of the five APIs is off | Step 2 for that API. No new sign-in needed; run `--status` again. |
| `credentials.json not found` | The file is not in the workspace root | Step 5. |
| Sign-in stops working after about a week (`expired or revoked`) | The app is in Testing mode, where Google ends sign-ins after 7 days | Step 6 again. To stop it: **Audience, Publish app**, confirm. The "unverified app" screen stays, which is fine for their own account. |

Anything else: read the error, fix what you can yourself, and only then ask the member for the
one thing you cannot do from here.
