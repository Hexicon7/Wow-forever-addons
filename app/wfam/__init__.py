"""
WoW Forever Addon Manager
=========================

A small, dependency-free addon manager for World of Warcraft: Forever.

* Paste CurseForge links -> it downloads the WoW Forever file and extracts it
  into  World of Warcraft\\_forever_\\Interface\\AddOns
* "Check for updates" / "Update all" keep everything current without the
  CurseForge app.
* A local page in your browser lists every installed addon, its version,
  whether an update is waiting, and which folders it owns.

Double-click this file to start it (needs Python 3.9+ from python.org).
It needs a free CurseForge API key - see README.txt.

Command line (optional):
    python WoWForeverAddons.pyw                 open the manager in your browser
    python WoWForeverAddons.pyw --list          list installed addons
    python WoWForeverAddons.pyw --check         check for updates
    python WoWForeverAddons.pyw --update-all    check and install all updates
    python WoWForeverAddons.pyw --add URL ...   install addons from links

The code is split by area:

    config, errors, util      constants, exceptions, small helpers
    net                       HTTPS (kept-alive connections, which key may go to which host)
    addonfiles                .toc files, zips, folder fingerprints, AddOns.txt, SavedVariables
    sources                   CurseForge, GitHub, tukui.org, Wago, wago.io
    system                    WoW folders, the game process, Windows integration, icon
    store                     config.json and state.json
    manager/                  the addon manager, one mixin per area
    watcher, server, webui    Downloads watcher, the local web server, the page (ui/)
    main                      command line and start-up
"""
from .config import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]
