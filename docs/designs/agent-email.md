# Agent email

Connect a mailbox in **Settings › Agent email** and each agent gets its own address.
Email an agent to give it work; it answers from that address in the same thread, and
the thread shows up as a chat in Cognitrix.

## Setting it up

1. Make a mailbox just for your agents (for example a new Gmail account, or `agents@yourdomain`).
2. Get a password that works over IMAP and SMTP. Gmail, iCloud, Fastmail and Zoho need an
   *app password*. Outlook.com / Microsoft 365 no longer accept passwords over IMAP, so they
   don't work yet.
3. In Settings › Agent email pick the provider, enter the address and password, and press
   **Connect mailbox**. Cognitrix checks both servers before saving.

Addresses:

- **Plus addresses** (default): `agents+researcher@gmail.com`. Works with almost every mailbox.
- **Own addresses**: `researcher@yourdomain.com`. Needs a catch-all, so all of the domain's
  mail lands in the connected mailbox.

Mail to the mailbox's own address goes to the agent picked under "Mail to … goes to".

## Who can give agents work

Only allowed senders: your own account email, unless you list others (addresses or
`@domain`). By default the mail must also pass SPF, DKIM or DMARC for the sender's domain,
as your mail server reports it in `Authentication-Results`, so a faked From line is ignored.
Turn that check off only for a server that doesn't add the header.

Anyone else is ignored. With **Answer other people** on, they get a read-only answer
(the agent can look things up but can't change anything or use your apps, memory or
sign-ins). A reply from someone else to an email the agent sent shows in your Inbox.

Bounces, out-of-office replies and list mail are never answered, and agent replies carry
`Auto-Submitted: auto-replied` so other robots don't answer them.

## During a chat by email

- Approvals arrive as an email: reply `APPROVE` or `DENY` (they also show in the Inbox).
- Ask User questions arrive as an email: reply with the answer.
- Small text attachments are read; other attachments are listed by name only.

Agents can also send mail from their address with the **Send Email** tool. It asks
before every email.

## Server settings

- `COGNITRIX_EMAIL=0` turns agent email off (needed when several API processes share
  one database, so mail isn't read twice).
- `COGNITRIX_EMAIL_POLL_SECONDS` (default 30): how often the mailbox is checked.

Only mail that arrives after connecting is read. Mail is marked read in the mailbox; nothing
is deleted.
