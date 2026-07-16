# Cheapest domain for home beta (~$1)

You do **not** need a domain to beta. Use the free Cloudflare quick tunnel first.

When you can spare about **$1–$3**:

## Buy steps

1. Open [Porkbun](https://porkbun.com) (or [Spaceship](https://www.spaceship.com) if their promo is cheaper that day).
2. Search a name like `yourname-sentinel.xyz` or `homesentinel.xyz`.
3. Choose a **`.xyz`** promo in the ~$0.95–$2 range for the **first year**.
4. Before checkout, read the **renewal price** (often about $11–$20/year — not $1 forever).
5. Turn **auto-renew off** if money is tight; set a calendar reminder 30 days before expiry.
6. Keep WHOIS privacy on (usually free at Porkbun).

## After you own the domain (stable URL)

1. Create a free [Cloudflare](https://dash.cloudflare.com) account.
2. Add the domain and switch nameservers as Cloudflare shows.
3. Create a **named** Cloudflare Tunnel on the Pi (systemd), pointing `cam.yourdomain.xyz` → `http://127.0.0.1:5000`.
4. Keep dashboard token auth enabled.
5. Bookmark `https://cam.yourdomain.xyz/?token=...` on your phone.

Until then, keep using `.\scripts\deploysshtunnel.ps1` (free, URL may change on restart).

## Skip for now

- Paid `.com` domains (~$10+/year)
- Marketing / sales website
- Inviting non-household testers before auth + a stable URL
