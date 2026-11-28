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

Turning the public address off closes it at once: Cognitrix stops letting anything in through it, whether or not Tailscale has taken the address down yet. If Tailscale refuses to take it down, Settings says so and Cognitrix keeps trying.

## Only reachable through Tailscale

On a server, **Only reachable through Tailscale** makes the server stop listening on the local network (it listens on this machine only), so the Tailscale addresses are the only way in. It applies after a restart. Leave it off where your host checks the server's own port, as Fly does.

## Sign in from the tailnet

Off until an admin turns on **Sign in from the tailnet** (Settings › Tailscale). It never signs anyone in by itself after that: each person links their own Tailscale login.

1. Open Cognitrix at the private address on a device that is on the tailnet, and sign in the usual way.
2. In **Settings › Sign-ins**, under Tailscale, press **Link**. The page shows the Tailscale login it sees for that device, and says what linking means before you confirm. A device that itself signed in with a code in the last day can't link a login, like it can't make a code or approve a PC.
3. From then on, the sign-in page at the private address shows **Continue as you@example.com**. One click signs you in. It shows in Sign-ins as "Signed in from the tailnet" and can be signed out like any device.

Nothing is linked by matching email addresses: a Tailscale login is tied only to the account that was signed in when its owner linked it. One Tailscale login belongs to one account. A link stops working when you unlink it, when the admin turns the setting off, when the private address is off, or when the account is disabled. Link a login only if it is yours alone: anyone who can use a device signed in to Tailscale as that login can open your account.

Tagged devices have no Tailscale login, so they are never signed in this way. The public address never signs anyone in this way either.

## Apps for people on your tailnet

In an App's **Share** dialog there is a **Your tailnet** section while the private address is on. You can share the App with Tailscale logins you type, or with **everyone on your tailnet**. Naming logins is the safer choice. Those people open `https://<private address>/apps/open/<app>` and the App opens with no Cognitrix account.

- They can use everything in the App, including anything in it that saves or changes the App's own data. This is not a read-only view. They can't change the App itself (its code, settings, versions or sharing), and they see nothing else in Cognitrix.
- The App is told who they are the same way as for accounts, with their Tailscale login as the id and email, plus `X-Cognitrix-User-Source: tailscale`. `window.cognitrix.guest` is true in the page, and the things that need an account (notifications, connected apps) reject.
- The link only works at the private address, and only while Tailscale says it is them: on every request, not once.
- "Everyone on your tailnet" also lets in people outside your tailnet whom you shared this machine with in Tailscale: Tailscale names them to the server the same way. "Everyone on this server" still means accounts only.

## Pair a PC over the tailnet

See [Pair your own PC](pair-your-pc.md#over-tailscale): a PC on the tailnet can find the server by itself and pair without a code.

## Agents' computers on your tailnet

This is for servers whose agents get a computer on Fly ([the agents' computer on Fly](sandbox-on-fly.md)). With it, each computer joins your tailnet when it starts, as a tagged machine named `cognitrix-sandbox-…`. Two things follow:

- **The server doesn't have to run on Fly.** A home server or the desktop app, on the same tailnet, reaches the computers by their tailnet address. A server that does run on Fly keeps using Fly's own network.
- **Agents working on their computer can reach your machines**, exactly as far as your tailnet policy opens them to the computers' tag. Nothing is open until you add a line for it.

To set it up, in **Settings › Tailscale**, under **Agents' computers on your tailnet**:

1. Pick whose computers join: nobody (the default), admins' computers or everyone's.
2. On Tailscale's [Trust credentials](https://console.tailscale.com/admin/settings/trust-credentials) page, make an OAuth credential with write access to **Auth Keys** (`auth_keys`) and **Devices › Core** (`devices:core`), and give it the computers' tag (`tag:cognitrix-sandbox` unless you changed it). Paste its ID and secret into Settings. The secret is stored encrypted and never shown again.
3. Copy the **lines for your tailnet policy** into your policy file. They say who owns the tag, let this server reach the computers' two ports, and let the computers reach this server (for [hidden keys](sandbox-on-fly.md#hidden-keys-and-snapshots)). Add one more line for each machine and port the computers may reach, for example a NAS.
4. Press **Check the connection**. It tries the OAuth client and makes one unused join key.

How a computer joins: the server asks Tailscale for a join key that works once, lasts ten minutes, is already approved and carries the tag. The key is put in the new machine's settings; the computer's start script (running as root) starts Tailscale with it and removes it from the environment before anything of the agents' runs. Tailscale's identity is kept in a root-only folder on the computer's volume, so sleeping and waking keep the same name. That folder is left out of snapshots. Resetting a computer removes it from your tailnet.

Things to know:

- **Commands agents run inside their computer don't ask you first.** So anything your policy opens to the tag, an agent can use on its own. Open ports one at a time, to the tag, never `*`.
- Computers don't take DNS settings or routes from the tailnet (`--accept-dns=false`, `--accept-routes=false`). Agents reach a machine by its tailnet address or full name.
- A computer made before you turned this on gets its join key the next time it is used: a server on Fly waits until the computer next wakes, a server elsewhere restarts it once.
- The computers' own image needs Tailscale inside it, which the published image has from this version on.

## Devices agents may use

Two tools let an agent work on a real machine on your tailnet:

- **List Tailnet Devices** shows the machines and which of them the agent may use. It only reads.
- **Run On Tailnet Device** runs one command on one machine, as one login name, and returns what it printed.

Nothing is allowed until an admin sets it up in **Settings › Tailscale**, under **Devices agents may use**:

1. Pick whose agents may use devices: nobody's (the default), admins' or everyone's.
2. For each machine, press **Allow** and type the login names agents may use there. Pick how the server signs in:
   - **Tailscale SSH**: the machine has Tailscale SSH on (`tailscale set --ssh`), and the SSH part of your tailnet policy lets this server in as that login. A tagged server can only reach tagged machines this way.
   - **This server's SSH key**: for any machine with an ordinary SSH server. Copy the key shown in Settings into `~/.ssh/authorized_keys` of each login name on that machine. The server makes this key itself and keeps the private half encrypted.
3. Add the two tools to each agent that should have them, on the agent's page. No agent gets them by default.

**A person approves every command.** The approval card shows the machine, the login name and the full command, and says it is a real machine and not the agent's sandbox. This holds whatever the agent's permission level is: "Auto-approve everything", the action reviewer and "allow all commands in this chat" never answer for it. **Always allow** on the card remembers the start of a command for that one login on that one machine, nothing wider. Every call is in the audit log.

Other things to know:

- The server remembers each machine's SSH identity the first time and refuses if it later changes. Removing a machine from the list forgets it.
- If Tailscale asks you to confirm it is you (SSH "check" mode), the agent is given the link to pass on to you, and tries again after you open it.
- A command gets 60 seconds unless the agent asks for more, 15 minutes at most. A command stopped for time may still be running on the machine.
- Agents' own computers never appear in this list.
- The server needs an `ssh` program. The Docker image has one; on a desktop it uses the system's.

## Passkeys

The private address is https and a real name, so passkeys work there. A passkey belongs to the address it was made at: one made at the Tailscale address does not work at `localhost` or another domain, and the other way round. See [passkeys](passkeys.md#with-tailscale).

## How it is built

`cognitrix/tailscale/`:

- `cli.py` finds and runs the Tailscale client, always with a time limit.
- `daemon.py` runs the built-in Tailscale (`tailscaled` in userspace mode) where the machine has none.
- `doors.py` opens two extra listeners, on this machine only:
  - the **tailnet door**, which Tailscale Serve forwards the private address to;
  - the **public door**, which Tailscale Funnel forwards the public address to. It holds the allow list above.
- `api.py` talks to Tailscale's web API with the saved OAuth client (join keys, removing a machine). `computers.py` is the Fly computers' side of joining, and `outbound.py` lets a server on the built-in Tailscale dial a tailnet address.
- `devices.py` keeps the list of devices agents may use and runs their commands over SSH; the tools are in `cognitrix/tools/tailnet.py`.
- `service.py` keeps Tailscale matching Settings and writes the addresses to `~/.cognitrix/tailscale/addresses.json`, which `cognitrix/common/addresses.py` reads for every process.

Three rules keep this safe:

1. **Tailscale's "who is this" headers count only at the tailnet door.** Tailscale adds headers naming the visitor, and says to trust them only on a listener nothing else can reach. The main listener can be reached from the local network, so those headers are stripped from every request that did not come through the tailnet door (`DoorGuard`). The public door strips them too. `identity.py` is the one place that reads them: it is what Apps for your tailnet, signing in from the tailnet and pairing without a code are built on.
2. **Off means off.** When an address is switched off in Settings, its door answers 404 to everything, even if Tailscale has not taken the forwarding down yet. The Tailscale page says so while it keeps trying to remove it.
3. **A request through a door never counts as this computer.** Its caller address is the one Tailscale forwarded and is never a loopback address, so nothing that trusts "the same machine" (the desktop app's passkey step, linking the PC) can be reached from outside.

Tailscale's own limits apply: public addresses need MagicDNS and HTTPS turned on for the tailnet and the `funnel` permission in its access rules, and traffic through them is rate limited by Tailscale.

### For developers

`COGNITRIX_TAILSCALE_PATH` and `COGNITRIX_TAILSCALED_PATH` point at a different Tailscale client and daemon (tests use a stand-in).
