WoW Forever Addon Manager
=========================

Install and update World of Warcraft: Forever addons from CurseForge links,
without the CurseForge app. One file, no extra libraries, nothing running in
the background when you are not using it.


INSTALL (about 2 minutes)
-------------------------
1. Double-click WoWForeverAddons-Setup-3.7.2.exe and answer Yes.
   Windows may say "Windows protected your PC" because the installer is new
   and not code-signed: click "More info" -> "Run anyway".
   The installer:
   - puts the manager in %LOCALAPPDATA%\Programs\WoW Forever Addons
     (no administrator rights needed, about 25 MB),
   - downloads its own private copy of Python from python.org once
     (about 11 MB; it checks that it is signed by the Python Software
     Foundation) - so you don't need Python installed yourself,
   - adds "WoW Forever Addons" to the desktop and the Start menu,
   - adds it to Settings > Apps > Installed apps, with an Uninstall button.
   To update later, run the new Setup exe. It closes the manager, deletes
   every file of the old version and installs the new one from scratch
   (Python is reused only if it is exactly the right, signed version).
   Your settings, API keys and addons are kept.
   Silent install: WoWForeverAddons-Setup-3.7.2.exe /quiet

UNINSTALL
---------
Settings > Apps > Installed apps > WoW Forever Addon Manager > Uninstall
(or run Uninstall.exe in the program folder). It removes everything the
manager created: program files, its Python, shortcuts and taskbar/Start
pins, its registry entries (including the notes Windows keeps about the
program), settings, API keys, saved addon versions, the app window's
browser profile, temporary files, and any working folders it left in your
WoW folder (an interrupted install is put back first).
If you have WTF backups it asks whether to delete them or move them to
Documents\WoW Forever WTF backups. Your addons in WoW are not touched.
"Uninstall.exe /uninstall /quiet" does it without questions (backups deleted).

2. Get a free CurseForge API key
     https://console.curseforge.com/  -> sign in -> "API keys".
   The key is stored only on your PC (%APPDATA%\WoWForeverAddonManager).
   Keep it to yourself: CurseForge's terms say keys must not be shared.

3. Start "WoW Forever Addons". The first screen asks for the key and finds
   your World of Warcraft\_forever_\Interface\AddOns folder by itself.

(You can still double-click WoWForeverAddons.pyw directly if you have
Python installed - it is the same program.)


EVERYDAY USE
------------
* Browse: open the "Browse CurseForge" tab, search (or pick a category /
  sort order) and click Install. Only addons that have a WoW Forever
  version are listed. Click several Install buttons in a row - they queue.
* Or paste one or more CurseForge links, e.g.
      https://www.curseforge.com/wow/addons/questie
  and press Install (Ctrl+Enter works too). The newest file tagged for
  WoW Forever is installed. Required libraries come along automatically.
* Check for updates / Update all: buttons at the top.
* The table shows every managed addon: installed version, newest Forever
  version, status, and how many folders it owns (hover to see them).
* Pin keeps an addon at its current version. Remove deletes its folders.
* "Other folders in AddOns" lists things you installed by hand. If the
  addon was packaged by CurseForge, a "Manage with updates" button takes it
  over; otherwise paste its CurseForge link to do the same.
* An orange "⚠ .toc targets ..." note means the addon says it was made for a
  different game client (not interface 16001) and may not load.
* Quit stops the manager. It also stops by itself ~3 minutes after you close
  the browser tab.

Addons whose authors only allow downloads on CurseForge's site
  If the addon has a GitHub page with a WoW Forever build, it is installed
  and updated from GitHub instead - no browser involved (the zip is checked
  against GitHub's checksum and must contain a Forever .toc). GitHub allows
  about 60 checks per hour without an account. Otherwise:
  Click Install (or Update) as usual. The manager opens that addon's download
  page on CurseForge in your browser; when the download finishes, the zip in
  your Downloads folder is checked against CurseForge's checksum, installed,
  and then deleted from Downloads (also any folder you unpacked it into). "Update all" does the same for them.
  Settings lets you turn this off, keep the zips, or pick another Downloads
  folder. "Install zip..." still works as a manual fallback.

Linking to a specific file (…/files/1234567) installs exactly that file
and pins it.


NEW IN 3.7
----------
* Updates itself: when a new version is published on GitHub, an "Update
  now" box appears above Settings (and a notification). One click and the
  program closes, updates and opens again.
* After an update, a summary shows what changed (old -> new version, with
  "What's new"). "Undo this update" puts the earlier versions back in one go
  (they get pinned until fixed versions are out). Also in the "..." menu.
* Settings > Backups folder: keep your WTF backups in OneDrive or another
  synced folder. Existing backups are moved there.
* Browse: switch between CurseForge and Wago (with a Wago key).
* Fixed: the AddOns folder could be shown as the name of an addon's
  dependency (e.g. "MerfinPlus"). Your setting itself was never changed.


NEW IN 3.6
----------
* New look: a sidebar like the CurseForge and Wago apps, dark title bar,
  no white flash at start, Settings as a page of its own.
* My addons: filter chips instead of big counters, updates and problems
  listed first, libraries (Ace3, LibStub...) folded into one group, and the
  rarely used buttons (pin, revert, remove...) in a "..." menu per addon.
  "Add addons" opens a small window for pasting links.
* Click any addon (also in Browse) for a detail panel: description and
  screenshots, every WoW Forever version with its notes and an "Install"
  button for that exact version, and links to its source and issue page.
* Characters: click a cell to switch an addon on or off per character, copy
  one character's set to another, or save a set as a profile and use it on
  other characters. Writes the game's own AddOns.txt (a WTF backup is made
  first). Do it at the character select screen or with WoW closed.
* "Hide" for folders you look after yourself (e.g. Zygor).
* Uses far less memory in the tray: after two minutes hidden the window is
  closed completely and opens again when you click the tray icon. Started
  with Windows, it doesn't open the window until you ask for it.


NEW IN 3.5
----------
* System tray, like the Wago and CurseForge apps: closing the window keeps
  the manager running next to the clock. Left-click the icon to open it,
  right-click for "Check for updates now", "Update all addons" and "Quit".
* Background updates: checks every 3 hours (choose 1-24 hours or never in
  Settings) and installs what it finds, or only notifies you if you turn
  automatic installs off. A Windows notification tells you what happened.
  It never opens CurseForge download pages by itself - addons that need a
  download from CurseForge's site are only reported.
* Settings > "Start with Windows (in the tray)". Uninstall removes it.


NEW IN 3.4
----------
* A real desktop program: "WoW Forever Addons.exe" opens the manager in
  its own window (with WebView2, the web component built into Windows),
  like the Wago or CurseForge apps - no browser involved. Links you click
  open in your normal web browser. Closing the window stops the manager.
  (Without WebView2 - very old Windows 10 - it falls back to an Edge
  app window.)


NEW IN 3.3
----------
* Notices when another tool (the Wago app, the CurseForge app, a manual
  copy) updated an addon. The new version shows right away, and the next
  "Check for updates" finds out exactly which release it is (CurseForge
  file, Wago release or GitHub release). Unknown builds show their .toc
  version and are compared by date.
* The app window gets its own taskbar button with the manager's icon and
  name, instead of turning into an Edge/Chrome button. Pinning it pins
  the manager.


NEW IN 3.2
----------
* A real Windows installer (WoWForeverAddons-Setup-<version>.exe)
  that installs, updates and uninstalls - no scripts.
* Uninstall removes everything (see UNINSTALL above) - nothing is left.
* Updating wipes the old version first instead of copying over it.
* At startup the manager tidies up after a crash or power cut: it puts
  back addon folders from an interrupted install, finishes an interrupted
  settings restore, and deletes stale temporary files and saved versions
  of addons you no longer have.


NEW IN 3.1
----------
* A real installer (Install.cmd) and uninstaller - no PyInstaller and no
  Python installation needed.

NEW IN 3.0
----------
* Opens in its own window (no browser tabs or address bar) with its own
  icon. It uses the Edge engine built into Windows - nothing to install.
  Closing the window stops the manager. (Settings can switch back to a
  normal browser tab.)
* Settings -> "Add to desktop & Start menu" creates shortcuts with the
  icon. Right-click the Start menu entry to pin it to the taskbar.
* Characters tab: which addons each character has switched on, read from
  the WTF folder (read-only for now).

NEW IN 2.3
----------
* Follows CurseForge's API terms: nothing from CurseForge (names, versions,
  search results) is saved on disk any more - only which project/file you
  have and which folders it owns. Everything else is fetched fresh each time
  the manager starts (a couple of seconds). Without internet the list still
  works, using the names and versions from the addons' own .toc files.
* Missing requirements: if an addon's .toc lists another addon it can't
  load without, the row says "Needs: ..." and "Install missing" fetches it.

NEW IN 2.2 - Wago Addons (optional, needs a Wago Patreon key)
-------------------------------------------------------------
* Settings -> Wago Addons key: paste your key from addons.wago.io/patreon
  and press Test.
* Each update check also asks Wago. If Wago has a newer WoW Forever version
  than the one you have, the row says "Update available - Wago" and Update
  takes it from there. If CurseForge gets something newer later, it switches
  back automatically. Addons are linked to Wago through the X-Wago-ID in
  their .toc, the same project name, or (for CurseForge-site-only addons) a
  search by name - only when the names match.
* Addons whose authors only allow CurseForge's own site are fetched from
  Wago first, then GitHub, and only then via CurseForge's download page.
* Every Wago file must contain a .toc that WoW Forever loads, or it isn't
  installed. The Wago key is only ever sent to Wago, the CurseForge key
  only to CurseForge.

NEW IN 2.1 (faster, lighter)
----------------------------
* Checks for updates by itself every time you open it.
* Update checks and "Update all" are several times faster: connections are
  reused, lookups and downloads run in parallel, installs stay one at a time.
* Lighter while open: the page only redraws when something changed, slows
  down in a background tab, and the Downloads watcher rests unless a
  download is expected. No helper programs are started to detect WoW.

NEW IN 2.0
----------
* Backups tab: your addon settings (the WTF folder - profiles, Questie
  progress, bars...) are zipped automatically before addons change, at most
  once per 10 minutes; the newest 10 are kept. Restore with one click
  (WoW must be closed; your current settings are backed up first).
* Revert: after an update the previous version is kept. "Revert" puts it
  back and pins it so Update all skips it. Click Revert again to switch back.
* What's new: link under the latest version shows the changelog.
* Look up on CurseForge (Other folders): recognises addons you installed by
  hand from their files. "Manage" starts tracking them without downloading;
  a non-Forever copy gets replaced by the Forever version.
* Export list / Import list: save your addon list and reinstall it anywhere.
* A banner warns when WoW is running (changes need /reload).
* Settings -> Run diagnostics: checks everything against the real CurseForge
  and GitHub and gives a report you can copy. Your API key is never in it.


OPTIONAL: COMMAND LINE / SCHEDULED UPDATES
------------------------------------------
  python WoWForeverAddons.pyw --update-all     check + install all updates
  python WoWForeverAddons.pyw --check          only check
  python WoWForeverAddons.pyw --list           show what's installed
  python WoWForeverAddons.pyw --add URL [URL ...]

Settings, the API key and the list of managed addons live in
%APPDATA%\WoWForeverAddonManager. Deleting that folder resets the manager;
your addons stay in the game folder.
