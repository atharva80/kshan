# User guide

For the person whose photos and videos these are. No technical knowledge needed. Whoever runs
the server will give you three things:

- an **invite link** for Tailscale
- your **Immich email and password**
- the **server address**, which looks like `https://something.tail1234.ts.net:8443`

## 1. Connect (once, about 5 minutes)

1. **Install Tailscale** (Play Store / App Store) and sign in with **your own** Google or Apple
   account.
2. **Open the invite link** and accept it. The server now appears in your Tailscale app.
3. Keep Tailscale **switched on** whenever you use your photos. On Android:
   Settings → Network → VPN → Tailscale → **Always-on VPN**.
4. **Install Immich**, then enter the server address. It must end in `:8443`. Log in with the
   email and password you were given.

## 2. Back up your phone and iPad

In the Immich app, tap the **cloud icon** → **Select albums**:

- Android: **Camera**, **WhatsApp Images**, **WhatsApp Video**, **Screenshots**, and anything else you care about
- iPhone/iPad: **Recents** covers everything

Then:
- In Backup settings, choose **Wi-Fi only** so it doesn't eat your mobile data.
- Do the **first big backup on Wi-Fi with the phone charging**, and keep the app open until it
  finishes. iPhones and iPads pause background uploads a lot.
- From then on, new photos and videos upload by themselves.

> **Don't delete anything from your phone yet**, even after it says everything is backed up.
> Wait until whoever runs the server tells you a second copy (a backup) exists.

## 3. Upload the pendrives

You don't need to sort anything first. Upload everything as it is. Immich:
- puts everything in date order, using the date inside each photo or video
- **skips exact duplicates automatically,** even across different pendrives
- keeps the originals untouched

Start with the **smallest pendrive**, to see how fast it goes.

### Best: from any laptop (Windows or Mac)

1. Install Tailscale on the laptop and sign in with **your** account.
2. In Immich (web or app): **Account Settings → API Keys → New API Key**. Copy the key.
3. Download **immich-go** from [github.com/simulot/immich-go/releases](https://github.com/simulot/immich-go/releases).
   It's one file, nothing to install.
4. Plug in the pendrive (say it shows up as `E:`), open a terminal next to `immich-go`, and run:

   ```
   immich-go upload from-folder --server=https://YOUR-SERVER:8443 --api-key=YOUR_KEY E:\
   ```

5. Leave it running. If it stops, run the same command again: files already uploaded are skipped.
   immich-go also reads dates from WhatsApp-style file names (`IMG-20190512-WA0001.jpg`).

### Phone only (Android)

Plug the pendrive into the phone with the OTG adapter. Open **Chrome** with Tailscale on, go to
the server address, log in, then tap **Upload** (top right) and pick files from the USB drive.
Go one folder at a time, and keep the screen on. It's slower than a laptop, but it works.

### iPad

Same as the phone, using **Safari** and the **Files** app. A 10th-gen iPad has USB-C. A
9th-gen iPad needs Apple's **Lightning to USB 3 Camera Adapter** (the one with a charging port),
because many pendrives need more power than the iPad gives.

## 4. Find things

### In the Immich app (the search bar)

Type what you remember, in plain words:
- **What's in it:** "cake with candles", "beach at sunset", "black dog", "fireworks"
  - Videos are now searched by **every scene**, not just their first frame.
- **Words written in it:** switch the search type to **OCR**, e.g. "diwali", "goa 2023", a shop name
- **What someone said:** switch the search type to **Description**, e.g. "birthday", "park"
  - Every video with talking gets the words written into its description (🎙). Hindi and
    Marathi also get an English translation (🌐), so English words find those too.

### The video search page: jump to the exact moment

Open `https://YOUR-SERVER:8443/vsearch/` in your phone's browser and log in with the same Immich
email and password. Tip: use **Add to Home screen** and it opens like an app.

- Type anything: "cat", "happy birthday", "going to Goa", "diwali".
- Every result shows **moments**:
  - **Seen 0:20**: it appears on screen at 0:20
  - **Said 1:12 "…"**: someone says it at 1:12 (`EN:` means it matched the English translation)
  - **Written 0:08 GOA 2023**: the text appears on screen at 0:08
- Tap a moment and the video **plays from that second**. Tap another moment to jump.
- Use the buttons along the top to search only **On screen**, **Said** or **Written**.
- **Open in Immich** opens the video in Immich. **Save these videos as an Immich album** turns
  the results into an album.

New uploads become searchable here within a few minutes. The page shows how many videos are
indexed so far.

## 5. If something doesn't work

| What you see | Try |
|---|---|
| Immich says it can't reach the server | Is Tailscale switched on? Is the server computer on and awake? |
| The search page asks you to log in again | Log in with your Immich email and password. Sessions last a long time, but phones clear them sometimes. |
| A brand-new video isn't found on the search page | Give it a few minutes; it's indexed after Immich finishes processing it |
| Uploads are very slow | Try another Wi-Fi, or upload the pendrives from a laptop with immich-go |
