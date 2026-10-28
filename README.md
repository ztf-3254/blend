# Blend Signal (cloud)

Your S&P 500 blend, running free on GitHub: a check after every US close, Telegram alerts, and a dashboard.

- **Rule:** the sleeve holds UPRO while SPY's daily close is above its 200-day simple average, otherwise SGOV.
  The core (VOO) is always held. Rebalance to 50/50 on the first trading day of each quarter.
- **Alerts:** only when the sleeve should switch, on quarter starts, and once at startup.
- **Dashboard:** `https://<your-github-name>.github.io/<repo-name>/`. Market data only; the share counts you type
  in stay in your browser.

## Files

| File | What it is |
| --- | --- |
| `blend_cloud.py` | The daily check; writes `docs/data.json` and `state.json` |
| `config.json` | Names in the messages (VOO / UPRO / SGOV) and the 200-day setting |
| `.github/workflows/blend.yml` | The schedule: weekdays at 21:15 and 22:15 UTC (after the close in summer and winter) |
| `docs/index.html` | The dashboard (GitHub Pages) |

Run it by hand any time: **Actions → Blend Signal → Run workflow** (tick "Send the current status" to get a
Telegram message right away).
