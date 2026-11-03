# User guide

For the person whose photos and videos these are. **A phone is all you need**, plus the small
OTG adapter for pendrives. No computer required.

Whoever runs the server gives you:
- an **invite link** for Tailscale
- your **Immich email and password**
- the **server address**, which looks like `https://something.tail1234.ts.net:8443`

## 1. Connect (once, about 5 minutes)

1. **Install Tailscale** (Play Store / App Store) and sign in with **your own** Google or Apple account.
2. **Open the invite link** and accept it.
3. Keep Tailscale **on** whenever you use your photos. On Android:
   Settings → Network → VPN → Tailscale → **Always-on VPN**.
4. **Install Immich**, enter the server address (it ends in `:8443`), and log in.

## 2. Back up your phone and iPad (Immich app)

In the Immich app, tap the **cloud icon** → **Select albums**:
- Android: Camera, WhatsApp Images, WhatsApp Video, Screenshots…
- iPhone/iPad: Recents

Then:
- In backup settings, choose **Wi-Fi only**.
- Do the first big backup on Wi-Fi with the phone charging, keeping the app open.
- After that, new photos upload by themselves.

## 3. Upload pendrives (phone browser)

The Immich app backs up the phone's own gallery but can't open a pendrive, so pendrives go
through the **Pendrives** page.

1. Plug the pendrive into the phone with the OTG adapter.
2. In **Chrome**, with Tailscale on, open `https://YOUR-SERVER:8443/vsearch/#pendrives`.
   Log in with your Immich email and password.
   - Tip: **⋮ → Add to Home screen** makes it open like an app.
3. Type a **name** for this pendrive, e.g. *Blue 64GB*. That becomes the album
   **"Pendrive - Blue 64GB"** in Immich.
4. Tap **Choose photos & videos**. In the picker, open the **USB drive**, go into a folder,
   then **⋮ → Select all** → Select.
   - Folders with sub-folders: pick one folder, then tap the button again for the next.
     Everything goes into one list.
   - If your phone shows **Choose a whole folder**, that picks a folder with everything inside it.
5. Tap **Start upload**. You'll see a progress bar, *uploaded*, *already in Immich*, *failed*,
   *left*, the speed, and the time remaining.
6. **Keep the page open with the phone charging.** The screen stays on by itself. It's fine
   to leave it overnight.

**If it stops** (call, low battery, Wi-Fi drop, closed tab), open the page again and pick the
**same files**. Anything already uploaded is recognised and skipped in seconds, so nothing ever
uploads twice, even across different pendrives.

**Your pendrives** at the bottom of the page lists every pendrive: how many photos and videos
are in its album, and what the last run did. Tap the count to open the album.

Good to know:
- **Duplicates:** the same photo on three pendrives is stored **once**, and shows in all three
  pendrive albums.
- **Dates:** photos and videos keep their real date. WhatsApp-style names
  (`IMG-20190512-WA0001.jpg`, `VID-…`, `Screenshot_…`) are dated from the name when the file has
  no date inside it.
- **Failed files:** failures (unreadable files) are listed with a **Retry failed** button.

## 4. Find things

### Immich's search bar (in the app or on the web)

Just type. It now searches **inside videos** too:
- things you'd **see**: "cake with candles", "black dog", "fireworks", "beach"
- things someone **said**, even in Hindi or Marathi: "happy birthday", "park", "Goa"
- **writing** on screen: "diwali", a shop name, "goa 2023"

The best matches come first, photos and videos together.

### Jump to the exact second: the *Find in videos* page

`https://YOUR-SERVER:8443/vsearch/` (the other tab of the same page):
- Each result shows **moments** on a small timeline:
  - **Seen 0:20**: it's on screen at 0:20
  - **Said 1:12 "…"**: someone says it (`EN:` means it matched the English translation)
  - **Written 0:08**: the text appears on screen
- Tap a moment and the video plays **from that second**.
- **Save these videos as an Immich album** keeps the results.

## 5. Sort, and copy back to pendrives

Everything is in Immich now, so sorting happens there (app or web):
- **Make albums:** long-press to select photos/videos → **+** → **Add to album** / **New album**.
- **Find what to put in them:** use the search bar (people, places, things, words said).
- **People:** Immich groups faces automatically. Name them under **Explore → People**.
- **Copy an album onto a pendrive:** open the album → **⋮ → Download**. Big albums come as
  several ZIP parts (you can change the part size in Account Settings → Download). Then move
  the files to the pendrive with the phone's **Files** app. Do one album at a time if the
  phone is short on space.

> **Don't delete originals** from the phone or pendrives until whoever runs the server says
> there's a backup.

## 6. If something doesn't work

| What you see | Try |
|---|---|
| Can't reach the server | Is Tailscale on? Is the server computer on? |
| The page asks you to log in | Log in with your Immich email and password |
| Upload shows "Can't reach the server… retrying" | It waits and continues by itself. Check Tailscale/Wi-Fi |
| Upload stopped | Open the page, pick the same files again: done ones are skipped |
| A brand-new video isn't found by what's said in it | Give it a few minutes to be processed |
| The picker doesn't show the pendrive | Unplug and replug it. Some phones need *USB file transfer* turned on in the notification |
