# Library Contents Claim Agent

**What it does.** You walk around your home library once with your phone while talking to an AI insurance agent. The agent tells you where to point the camera ("pan slowly along the top shelf", "step closer, the spines are blurry"). While you walk, the screen fills in a list of your books and other items. At the end you get a **claim report** with:

- every book it could read from the spine, with its size and price in your currency;
- every other item in the room (lamps, furniture, art…), with a price range;
- the room's floor and wall area;
- a **review list** of everything the system was not sure about, so a person can check it.

Every price links to the shop page it came from, and every book links to the photo it was seen in. Nothing is guessed: if a spine can't be read, it says "unidentified" instead of inventing a title.

You don't need to know any programming. Follow the steps in order.

---

## What you need

| Item | Why |
|---|---|
| A Windows computer (Windows 10 or 11) | Runs the app. Mac and Linux also work, see the end. |
| Internet | To install the app the first time and to talk to Google's AI. |
| A Google (Gmail) account | To get a free AI key. |
| An Android phone with Chrome (optional) | To walk around the room. A laptop webcam also works for trying it out. |
| Plain A4 paper, a few sheets | The app measures sizes by comparing them with an A4 sheet. No printing needed. |
| About 15 minutes | First-time setup. After that it starts in seconds. |

## Quick start (the short version)

1. Get a free key at **https://aistudio.google.com/apikey** and copy it.
2. Unzip `library-claim-agent.zip` (right-click → **Extract All**).
3. Double-click **`START_HERE.bat`** and paste the key when asked.
4. Wait for **READY**. Your browser opens the app.
5. Press **Start sweep**, allow the camera and microphone, and talk to the agent.

The rest of this page explains each step in detail.

---

## Part 1: First-time setup (about 10 minutes)

### Step 1: Get a free Google API key

The app uses Google's AI. Google needs a free **API key**, a long code that works like a password for the app.

1. Open **https://aistudio.google.com/apikey** in your web browser.
2. Sign in with any Google account.
3. Click **Create API key**. If it asks for a project, accept the default.
4. A long code appears (it may start with `AIza` or `AQ.`). Click the **copy** button next to it.
5. Keep that tab open: you'll paste the key in Step 4.

> Treat the key like a password. Don't post it online or share it.

### Step 2: Unzip the project

1. Find `library-claim-agent.zip`, for example in your Downloads folder.
2. **Right-click** it, choose **Extract All…**, then click **Extract**.
3. Open the new `library-claim-agent` folder.

> Always extract the ZIP first. Starting the app from inside the ZIP does not work.

### Step 3: Double-click `START_HERE.bat`

A black window opens and does everything for you:

```
[1/3] Looking for Python...
[2/3] Preparing a private Python environment for this app...
[3/3] Installing the app's components (the first time takes 2-5 minutes)...
```

- **"Windows protected your PC":** click **More info**, then **Run anyway**.
- **Python missing:** the script installs it for you (Python is the language the app is written in). If Windows asks for permission, click **Yes**. If it then says *"Close this window and double-click the start script again"*, do exactly that.
- **Download size:** the first run downloads about 150 MB, so keep the internet on.

### Step 4: Paste your key when asked

The window shows:

```
Paste the key here and press Enter:
```

Click inside the black window and press **Ctrl + V** (or right-click) to paste. Then press **Enter**. The key is saved in a file called `.env`, so you only do this once.

### Step 5: Wait for "READY"

The app checks that everything works. The first time, it also tries a few Google AI models and picks ones your key can use (about a minute):

```
[ OK ] Python 3.12.x
[ OK ] All Python packages installed
[ OK ] Internet connection to Google works
[ OK ] API key accepted (…)
[ OK ] Price source reachable: Crossword (new books, INR)
...
READY: everything needed for a sweep works.
```

Your web browser then opens the app at **http://127.0.0.1:8000**.

- **[FAIL] lines:** read the line starting with `->` under each one. It says exactly what to do. Then double-click `START_HERE.bat` again.
- **[WARN] lines:** these are only warnings. The app still works.
- **Keep the black window open** while you use the app. Closing it stops the app.
- **Windows Firewall:** the first time, Windows may ask *"Do you want to allow Python to access the network?"*. Tick **Private networks** and click **Allow**, so your phone can reach the app later.

**Next time:** just double-click `START_HERE.bat`. It skips the setup and opens in a few seconds.

---

## Part 2: A tour of the screen

```
┌────────────┬────────────────────────────────────────────────────────────────┐
│ Library    │  [ Search books and items… ]                    🔔  (PH) ▾      │  ← top bar
│ Claim      ├────────────────────────────────────────────────────────────────┤
│            │  Library sweep                  [Use phone] [Start sweep]      │
│ ▸ Sweep    │  Ready when you are                                            │
│   Inventory│                                                                │
│   Packets  │  ┌ Replacement value ┐ ┌ Books found ┐ ┌ Identification rate ┐ │  ← key figures
│   Correct… │  └───────────────────┘ └─────────────┘ └─────────────────────┘ │
│   Settings │  ┌ Camera ─────────────────────────┐ ┌ Conversation ─────────┐ │
│            │  │  live video + tips on top       │ │  what you and the     │ │
│            │  │  [Wall][Shelf][Unit][Front]…    │ │  agent said           │ │
│ ● Live call│  └─────────────────────────────────┘ └───────────────────────┘ │
│            │  Value by shelf (chart) · Other contents · Books · Activity …  │
└────────────┴────────────────────────────────────────────────────────────────┘
```

- **Left menu:** jumps to each part of the page: **Sweep** (camera), **Inventory** (book list), **Packets** (your reports), **Corrections** and **Settings**. On a phone it's hidden behind the **☰** button. The dot at the bottom turns green during a live call.
- **Search box:** type part of a title or item name to filter the book and item lists.
- **Bell 🔔:** collects the agent's tips ("step closer"), warnings and "your packet is ready". A red number shows how many are new.
- **(PH) menu:** your valuation country and currency, the AI models in use, **Use phone camera**, **Light/Dark mode** and **New sweep** (starts over).
- **Key figures:**
  - **Replacement value:** what the priced books would cost new. The green badge shows how much it grew in the last minute.
  - **Books found:** the red badge counts spines that couldn't be read.
  - **Identification rate:** the share of books recognised. "Frames queued" means photos still waiting to be read.
- **Camera:** the live picture. On top of it you'll see:
  - the current step (e.g. *Shelf A2*);
  - a picture-quality badge (*Sharp*, *Blurry*, *Glare*, *Too dark*);
  - the agent's latest tip in a white box.

  The buttons underneath tell the app which part you're filming (only needed if the agent misses it).
- **Conversation:** everything said, as chat bubbles. You can also type to the agent here.
- **Value by shelf:** a bar chart. Hover or tap a bar to see its value. Switch between **Value** and **Books** with the buttons.
- **Books:** every book with its status. **Identified** (green), **Unidentified** (red: the spine couldn't be read, so no title is guessed) or **Reading** (amber: still being processed).
- **Recent activity:** a log of what the agent, you and the system did, with a status pill (Completed, Pending or Failed).
- **Claim packets** and **Your corrections:** your results after the sweep (Part 6).
- **Settings:** the phone address and QR code, and the valuation country and models.

---

## Part 3: Use your phone (for the real sweep)

A phone is easier to walk around with. The computer runs the app; the phone connects to it over your Wi-Fi.

1. Put the **phone and the computer on the same Wi-Fi**.
2. On the computer, press **Use phone** (top right) or open **Settings**. You'll see an address like `https://192.168.1.23:8443` and a **QR code**.
3. On the phone, point the **camera app** at the QR code and tap the link. Or type the address into **Chrome**.
4. The phone warns **"Your connection is not private"**. That's expected: the security certificate was made by your own computer. Tap **Advanced**, then **Proceed to … (unsafe)**.
5. Press **Start sweep** on the phone and allow the camera and microphone.

`START_PHONE.bat` does the same as `START_HERE.bat`, but shows the phone address and QR code in the black window instead of opening the browser.

**If the phone can't open the address:**
- Check both devices are on the same Wi-Fi (not a "guest" Wi-Fi).
- Or connect the computer to the **phone's hotspot**, then close and restart the app. It shows a new address.
- Windows Firewall may be blocking it. Allow "Python" in *Windows Security → Firewall & network protection → Allow an app through firewall*.

---

## Part 4: Prepare the room (5 minutes)

The app measures centimetres and metres by comparing everything with a plain **A4 sheet** (29.7 × 21 cm).

1. **Walls:** tape one A4 sheet flat on each wall, at chest height, where furniture doesn't hide it.
2. **Shelves:** stand one A4 sheet upright at the end of each shelf, touching the front of the book spines, like a bookend.
3. Switch on the lights. Avoid strong sunlight on the books.
4. Make sure no people and no papers with personal details are in view.

**No shelves?** That's fine, see "front view" below.

---

## Part 5: Do the sweep (about 3 minutes)

Press **Start sweep** and talk to the agent normally. It will:

1. greet you and confirm the country and currency (India and rupees by default);
2. ask what you're showing (a room with bookshelves, or books in front of you);
3. ask for each **wall** in turn (stand back so the whole wall fits, floor to ceiling);
4. ask you to pan slowly along each **shelf**, top shelf first, close enough to read the spines;
5. ask for a slow look around for **furniture, lamps, pictures** and other items;
6. read back a summary when you say you're done.

**No bookshelves or no walls?** Say so, for example *"I don't have shelves, the books are on my desk."*
- The agent switches to **front view**: stack or line up the books with their **spines facing the camera**, put one A4 sheet next to them, and pan slowly.
- Without walls, the room size is reported as *not measured* and the report explains why.

**Talk over the agent at any time.** Speak normally for about half a second: its voice dips, then it stops and answers you. Coughs, clicks and its own voice from the speaker don't interrupt it. Under the title you'll see *Agent speaking · talk to interrupt* or *Listening*.

**Things you can say:**
- *"Next shelf"*, *"I'm on the second bookcase now"*
- *"Skip that shelf, those books aren't mine"*
- *"That one is a first edition"*, *"That book is signed"* (these are sent for expert valuation instead of being priced)
- *"That painting is just a print"*, *"It's an original"*
- *"I'm done"*

The buttons under the camera (**Wall · Shelf · Unit · Front · Room**) and **End sweep** at the top do the same things if the agent misses what you said. **Unit** means "next bookcase". **Wide** switches to the widest lens if the phone has one.

---

## Part 6: After the sweep

After you end the sweep, the app finishes reading, pricing and measuring. Usually this takes 1–3 minutes. Then:

- The agent reads a short summary aloud.
- The **Claim packets** section shows **Verified claim packet** with **Open report** and **Download JSON**.

### Disagree with something? Give your own details

You can say, for example, *"Can you do it again? The third book is The Great Gatsby and I paid 300 rupees for it"*, or *"You missed a book: Wings of Fire, 250 rupees."* You can also use the **Your corrections** form: pick the book, type the title or price and a comment, then press **Add correction**.

- Press **Create customer-advised packet** (or tell the agent you're done) to get a second, separate **Customer-advised claim packet**, with its own download buttons.
- Your values are clearly labelled *"stated by the policyholder, not verified"*, with your comment next to each one.
- The verified packet stays exactly as it was, so an adjuster can compare the two.

### Where the files are

Each sweep gets its own folder:

```
library-claim-agent\data\sweeps\<date-time>\
    report.html                          ← the readable claim report (double-click to open)
    claim_packet.json                    ← the same, as a structured data file
    customer_advised_report.html         ← only if you gave corrections
    customer_advised_claim_packet.json   ← only if you gave corrections
    frames\                              ← every photo the report refers to
```

---

## Everyday use

| I want to… | Do this |
|---|---|
| Start the app | Double-click `START_HERE.bat` |
| Stop the app | Close the black window |
| Start a fresh sweep | **(PH) menu → New sweep**, or reload the page |
| Change the currency | See "Changing settings" below |
| Run the self-tests | Double-click `RUN_TESTS.bat` (no API key needed) |
| Copy the app to another PC | Double-click `MAKE_ZIP.bat` |

## Changing settings

Open the file `.env` in the project folder with **Notepad** (right-click → Open with → Notepad). For example, to value in US dollars instead of rupees:

```
COUNTRY=US
CURRENCY=USD
```

Save the file, close the black window, and start the app again.

## Words used in this app

| Word | Meaning |
|---|---|
| **Sweep** | One walk around the room with the camera. |
| **Spine** | The narrow side of a book you see on a shelf, with the title printed on it. |
| **Unidentified** | A book whose spine couldn't be read clearly. Its size is still recorded, but no title is guessed. |
| **Claim packet** | The result: a report (`report.html`) and a data file (`claim_packet.json`). |
| **Review list** | Lines the system wasn't sure about, with the reason, for a person to check. |
| **Customer-advised packet** | A second packet that includes your own corrections, clearly labelled as stated by you. |
| **A4 reference** | The sheet of paper used to turn pixels into centimetres. |
| **Replacement cost / used value** | What the book costs new today / what a used copy sells for. |
| **Converted** | The price came from another country's shop and was converted with that day's exchange rate. |
| **Barge-in** | Talking over the agent to interrupt it. |

## Troubleshooting

| What you see | What to do |
|---|---|
| "Windows protected your PC" | Click **More info**, then **Run anyway**. |
| "Could not install Python automatically" | Install Python 3.12 from https://www.python.org/downloads/. On the first screen tick **"Add python.exe to PATH"**, then Install Now. Then double-click `START_HERE.bat` again. |
| "API key rejected" or "key is not valid" | Copy the key again from https://aistudio.google.com/apikey. Open `.env` in Notepad, put it after `GOOGLE_API_KEY=` (no spaces), save, and start again. |
| "The free Google quota is used up" | Free keys have per-minute and per-day limits. Wait a minute (or until the next day) and try again. |
| The browser didn't open | Open **http://127.0.0.1:8000** yourself. If 8000 was busy, the black window shows the address it used. |
| Red box: "camera only works over a secure connection" | On a phone, use the `https://…:8443` address from **Use phone**, not the computer's address. |
| "Camera or microphone permission was refused" | Tap the lock icon next to the address, allow Camera and Microphone, and reload the page. |
| You can't hear the agent | Turn the volume up and check the phone isn't on silent. Headphones help in noisy rooms. |
| The agent stops talking by itself | Use headphones, or lower the speaker volume a little. Its own voice from a loud speaker can sound like you. |
| Installing components failed | Check the internet. On an office network, try a home network or a phone hotspot. |
| Anything else | Close the black window and start again. If it still fails, send the file `data\app.log` to the developer. |

## macOS / Linux

Open a Terminal in the project folder and run `./start.sh` (opens on this computer) or `./start.sh --phone` (shows the phone address). If Python 3.12 is missing, the script tells you how to install it.

## Moving the project to another computer

Double-click **`MAKE_ZIP.bat`**. It creates `library-claim-agent.zip` next to the project folder, **without** your key, the installed components or past sweeps. Copy the ZIP to the other computer, unzip it, and start at Part 1, Step 2. You'll paste your key again there.

---

# For reviewers and developers

- **Architecture note (one page):** [docs/ARCHITECTURE_NOTE.md](docs/ARCHITECTURE_NOTE.md); full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Failure log (one page), with cost and latency per stage:** [docs/FAILURE_LOG.md](docs/FAILURE_LOG.md); all 15 issues in [docs/FAILURES.md](docs/FAILURES.md)
- **How the code is organised**, file by file and function by function: **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)**

### Deliverables checklist

| # | Deliverable | Where / how |
|---|---|---|
| 1 | Repository | On your own PC, double-click **`PUBLISH_TO_GITHUB.bat`**. It asks for your new private repo's address, commits, **refuses to upload if any secret file is staged**, and pushes. Then add the reviewers under Settings → Collaborators. `.env` and `data/` are git-ignored, and `.gitattributes` keeps the `.bat` files working after cloning. |
| 2 | Demo video | Record one unedited take of the real sweep (≤ 6 min) |
| 3 | Claim packet from the video's sweep | `data/sweeps/<id>/`: `claim_packet.json`, `report.html`, `frames/` (copy the folder into the submission) |
| 4 | Ground truth and results | Fill `eval/ground_truth/` from the template, then run `python -m eval.score …`, which writes `results.md` next to the packet |
| 5 | Architecture note | [docs/ARCHITECTURE_NOTE.md](docs/ARCHITECTURE_NOTE.md) |
| 6 | Failure log | [docs/FAILURE_LOG.md](docs/FAILURE_LOG.md) (replace the three errors with those from the demo sweep) |

### Run without the scripts

```bash
python -m venv .venv
.venv\Scripts\activate                     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                     # then set GOOGLE_API_KEY
python -m app.doctor                       # checks setup, picks working models -> data/models.json
python -m pytest -q                        # offline tests, no key needed
python -m app.launch                       # HTTP on 127.0.0.1:8000 + HTTPS for the phone on :8443
```

### Re-running and scoring a sweep

```bash
python -m app.replay data/sweeps/<id>                                        # re-run the pipeline offline (cached calls are free)
python -m eval.score data/sweeps/<id>/claim_packet.json eval/ground_truth    # pass/fail per bar -> results.md
```

Fill `eval/ground_truth/` from `eval/ground_truth_template/` (books.csv, items.csv, room.json).

### Reference app: kept, changed, thrown away

Starting point: [Insurance Claim Live Agent Team](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team). Before writing code we installed it, started it locally and read its server, tools and client code. At that point there was no API key. Once we had one, we ran its suggested intake (the fictional "Maya Singh, basement flooded" claim) through its live session:

- **Worked:** the agent replied in voice, `lookup_policy` returned "Maya Singh, Homeowners HO-3, active", and the adjuster packet ZIP (`claim.md`, `evidence.json`) downloaded.
- **Failed:** its background claim graph and sketch tool, on Google-side 429 (free quota used up) and 503 (high demand) errors that day.
- **Two things we changed to run it** (neither is part of our app): on the office network we started it with an OS certificate bundle and IPv4-first DNS, and for the second run we temporarily switched its hard-coded `gemini-3.8-flash` to `gemini-3.6-flash`, then switched it back.

- **Kept:** the interaction pattern (live voice and camera, a claim file that builds itself, background work). Also the FastAPI WebSocket bridge to Gemini Live (PCM16 mic in, 24 kHz audio out, transcripts) and non-blocking tool calls. The browser audio resampling and playback are adapted from its `app.js`.
- **Changed:** the live model no longer describes what it sees. It directs the sweep and reads back numbers that code produced. A second, high-resolution frame stream feeds a separate measuring pipeline, whose coaching messages are injected into the live session.
- **Thrown away:** the ADK claim graph, policy rules, policy directory, avatar, sketches and narrative notebook.

### Tools used

- **Models:** Gemini Live (voice agent) and Gemini Flash (spine and item perception), picked automatically per key by `app/doctor.py`. Gemini with Google Search grounding is used for prices only if the key has search quota.
- **Libraries:** OpenCV (ORB tracking, A4 detection, homographies), RapidFuzz (title matching), FastAPI and Uvicorn, Jinja2, google-genai, qrcode, cryptography.
- **Data sources:**
  - catalogues: Google Books API and Open Library;
  - prices: retailer listings from Crossword.in, Kitabay, Book Outlet and SecondSale (Shopify storefront search) and IKEA product search;
  - currency conversion: ECB reference rates via Frankfurter.
- **Front end:** plain HTML, CSS and JavaScript (no framework). Lucide icons are inlined as SVG, with the Inter font from Google Fonts.
- **Coding:** written with Claude Code as an AI pair programmer.
