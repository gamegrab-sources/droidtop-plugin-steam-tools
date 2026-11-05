# Steam tools for droidtop

An unofficial [gamegrab-sources](https://github.com/gamegrab-sources) plugin for
[droidtop](https://github.com/Droidtop/droidtop): what people use ArchiSteamFarm and its
plugins for, on the Steam account droidtop is already signed in to. Not part of droidtop,
not affiliated with Valve or ArchiSteamFarm.

- **Free games**: a background service (Settings > the plugin > its service switch) reads
  Steam's product changes every half hour, keeps the apps and packages that are free to own
  (demos, playtests and free weekends only if you turn them on) and claims them. droidtop
  keeps Steam's limit for the account (30 an hour); the rest waits for the next hour.
- **Redeem keys**: paste one or more keys on the plugin's page; they are redeemed one by
  one (10 seconds apart, droidtop's rule) and each result is listed.
- **Show games as played** (off by default): the app ids you type are shown as played while
  the service runs, for play time and card drops.

It uses droidtop's Steam account API (`steam.account`, droidtop docs/plugin-api.md 3 G8):
droidtop runs every call on its own Steam session, so this plugin never signs in and never
sees a password, token or cookie. Trades, the market and Steam Guard confirmations are not
offered.

Add it as a source in droidtop (Settings > Plugins > Keys you trust: the gamegrab origin).

Tests: `./build.sh && python3 test_plugin.py`.

Licence: GPL-3.0, see LICENSE and NOTICE.
