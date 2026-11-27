# Tailscale

[Tailscale](https://tailscale.com) gives your devices a private, encrypted network (a "tailnet"). With it turned on, Cognitrix gets:

- a **private address** like `https://studio.your-tailnet.ts.net` that only your own devices can open, from anywhere, with no ports opened on your router and no domain to buy;
- a **public address** like `https://studio.your-tailnet.ts.net:8443` that services on the internet can post webhooks to (GitHub, Twilio, WhatsApp, Teams, your own automations), and nothing else unless you widen it.

Everything is in **Settings › Tailscale**. Nothing is read from environment variables.

## Turning it on

1. **Use Tailscale.** The page says what it found:
   - *A computer that already has Tailscale* (the desktop app, a Linux server you installed it on): Cognitrix uses that Tailscale. It never signs it in or out; you do that in Tailscale's own app.
   - *A server in Docker or on Fly*: the image ships Tailscale, and Cognitrix runs it itself. Press **Sign in to Tailscale** and open the page it shows (there is a QR code for your phone), or paste a join key from Tailscale's admin page. The key is used once and never kept. The server's Tailscale identity lives on the data volume, so it keeps its name across restarts and updates.
   - *No Tailscale*: a link to install it.
2. **Private address.** Turn it on and the address appears. Open it from any device on your tailnet.
3. **Public address** (optional). Turn it on for webhooks.

The page tells you when Tailscale needs something from you, with the link or command:

| It says | What to do |
| --- | --- |
| Your tailnet hasn't turned on HTTPS addresses / Funnel yet | Open the link (Tailscale's admin page) and turn it on. The page carries on by itself. |
| Tailscale only lets an administrator of this computer do this | On Linux, run `sudo tailscale set --operator=$USER` once. |
| Port 443 already serves something else | Cognitrix never takes over a port another program set up in Tailscale. Pick another port. |
| MagicDNS is turned off | Turn MagicDNS on in Tailscale's DNS settings: the address is made from the computer's name. |

## The public address

Tailscale offers public addresses on ports 443, 8443 and 10000 only. Cognitrix uses 8443 unless you change it, so the private address can have 443.

**What it lets through** decides what the internet can reach. Anything else gets a plain "Not found" before Cognitrix even looks at it.

| Choice | Reachable from the internet |
| --- | --- |
| Webhooks only (default) | Automation webhooks, GitHub webhooks, and the Twilio, WhatsApp and Teams endpoints. Each checks its own secret or signature. |
| Webhooks and public links | Also public links to agents and published pages. |
| The whole app | Everything, behind sign-in. Use strong passwords or passkeys. |

Wherever the app shows a webhook address (a task's webhook trigger, Settings › Connected apps for SMS, WhatsApp and Teams, GitHub pull request watches), it now shows the public address when there is one. A public address typed in Settings › Advanced still wins.

On the **desktop app**, the public address works while the app is running. GitHub pull request watches use it for instant news and keep their timed check for when the app is closed.

Requests through the public address are limited to 240 a minute from one address.

## Only reachable through Tailscale

On a server, **Only reachable through Tailscale** makes the server stop listening on the local network (it listens on this machine only), so the Tailscale addresses are the only way in. It applies after a restart. Leave it off where your host checks the server's own port, as Fly does.

## Passkeys

The private address is https and a real name, so passkeys work there. A passkey belongs to the address it was made at: one made at the Tailscale address does not work at `localhost` or another domain, and the other way round. See [passkeys](passkeys.md#with-tailscale).

## How it is built

`cognitrix/tailscale/`:

- `cli.py` finds and runs the Tailscale client, always with a time limit.
- `daemon.py` runs the built-in Tailscale (`tailscaled` in userspace mode) where the machine has none.
- `doors.py` opens two extra listeners, on this machine only:
  - the **tailnet door**, which Tailscale Serve forwards the private address to;
  - the **public door**, which Tailscale Funnel forwards the public address to. It holds the allow list above.
- `service.py` keeps Tailscale matching Settings and writes the addresses to `~/.cognitrix/tailscale/addresses.json`, which `cognitrix/common/addresses.py` reads for every process.

Two rules keep this safe:

1. **Tailscale's "who is this" headers count only at the tailnet door.** Tailscale adds headers naming the visitor, and says to trust them only on a listener nothing else can reach. The main listener can be reached from the local network, so those headers are stripped from every request that did not come through the tailnet door (`DoorGuard`). The public door strips them too. (Nothing reads them yet; the next step, sharing Apps with people on your tailnet, will.)
2. **A request through a door never counts as this computer.** Its caller address is the one Tailscale forwarded and is never a loopback address, so nothing that trusts "the same machine" (the desktop app's passkey step) can be reached from outside.

Tailscale's own limits apply: public addresses need MagicDNS and HTTPS turned on for the tailnet and the `funnel` permission in its access rules, and traffic through them is rate limited by Tailscale.

### For developers

`COGNITRIX_TAILSCALE_PATH` and `COGNITRIX_TAILSCALED_PATH` point at a different Tailscale client and daemon (tests use a stand-in).
