# Sign-ins and signing in with a code

## Signed-in devices

Every sign-in (password, Google, GitHub or a code) is now a device you can see in
**Settings › Sign-ins**. Each row shows what the device is, its rough address, when it
was last used and how it signed in. You can sign out one device, or **Sign out all
others**. **Settings › Account › Sign out of all devices** still ends every session.

Session tokens carry the device's id (`sid`). A signed-out device's token stops working
within about 30 seconds. Tokens from before this change keep working until they expire
or are renewed; a renewed one gets its own row (shown as "Signed in before this list
started").

Signing out from the account menu now also ends that device's sign-in on the server.
Changing or resetting a password, or an admin disabling a person or setting their
password, signs out the other devices.

## Signing in a new device with a code

1. On a device where you're signed in, open **Settings › Sign-ins** and press **Get a code**.
2. On the new device, open Cognitrix, pick **Sign-in code** and type the code. Or scan the
   QR code with a phone's camera: it opens the sign-in page with the code filled in, and
   you press **Sign in**.
3. The dialog on the first device shows which device signed in, with **That wasn't me,
   sign it out** in case it wasn't you.

Rules:

- A code works once, for 5 minutes. At most 3 are open per person; making a 4th cancels the oldest.
  Closing the dialog cancels an unused code.
- The code is a secret for those minutes: whoever types it first is signed in as you.
  Only type it yourself, and never send it to someone who asks.
- One address may try 10 wrong codes per 10 minutes.
- A device that signed in with a code can't make codes for its first 24 hours.
- Every code sign-in goes to the audit log and puts a "New sign-in" notice in your Inbox.
- API keys can't make codes or list sign-ins.
- Turn it off for everyone with `COGNITRIX_CODE_SIGNIN=false`.

## API

| Call | What it does |
| --- | --- |
| `GET /api/v1/auth/sign-ins` | This account's signed-in devices |
| `DELETE /api/v1/auth/sign-ins/{id}` | Sign that device out |
| `POST /api/v1/auth/sign-ins/sign-out` | Sign this device out |
| `POST /api/v1/auth/sign-ins/sign-out-others` | Sign out every other device |
| `POST /api/v1/auth/sign-ins/codes` | Make a code `{code, expires_in}` |
| `GET /api/v1/auth/sign-ins/codes/{code}` | `waiting`, `used` (with the device) or `expired` |
| `DELETE /api/v1/auth/sign-ins/codes/{code}` | Cancel a code |
| `POST /api/v1/auth/code-sign-in` | `{code, device_name?, device_kind?}` → `{user, access_token}` (no sign-in needed) |

`POST /api/v1/auth/login` also takes optional `device_name` and `device_kind`
(`browser`, `desktop`, `cli`), so an app can name itself in the list.
