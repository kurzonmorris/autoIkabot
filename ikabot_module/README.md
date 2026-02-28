# ikabot Multi-Backend Notification Module

Adds **Discord webhook** and **ntfy.sh** push notification support to ikabot,
alongside the existing Telegram backend. All 39+ existing modules gain
multi-backend support with minimal code changes.

## How It Works

The enhanced `botComm.py` is a drop-in replacement for ikabot's
`helpers/botComm.py`. When any module calls `sendToBot(session, msg)`,
the message is now routed to:

1. **Telegram** (existing behavior, unchanged)
2. **Discord** webhook (if configured)
3. **ntfy.sh** push notification (if configured)

Each backend is independent — if one fails, others still receive the message.

## Installation

### Step 1: Copy helper files

Copy these files into your ikabot installation:

```
ikabot_module/helpers/botComm.py     -> ikabot/helpers/botComm.py      (replaces existing)
ikabot_module/helpers/discordComm.py -> ikabot/helpers/discordComm.py  (new file)
ikabot_module/helpers/ntfyComm.py    -> ikabot/helpers/ntfyComm.py     (new file)
```

### Step 2: Copy the setup module

```
ikabot_module/function/notificationSetup.py -> ikabot/function/notificationSetup.py (new file)
```

### Step 3: Update command_line.py

See `command_line.patch` for the 3 small changes needed to add the
Notification Setup menu item to ikabot's Settings section.

## Configuration

After installation, run ikabot and go to:
**Settings > Notification setup**

From there you can:
- Set up Telegram bot (same as before)
- Set up Discord webhook
- Set up ntfy.sh push notifications
- Test all configured backends
- Remove a backend

## Session Data Storage

New backend configs are stored flat in `sessionData["shared"]`, the same
pattern as Telegram:

```json
{
  "shared": {
    "telegram": {
      "botToken": "...",
      "chatId": "..."
    },
    "discord": {
      "webhookUrl": "https://discord.com/api/webhooks/..."
    },
    "ntfy": {
      "server": "https://ntfy.sh",
      "topic": "my-ikabot-alerts-abc123",
      "token": ""
    }
  }
}
```

## Backward Compatibility

- All existing `sendToBot()` calls work identically
- If no Discord/ntfy config exists, behavior is the same as the original
- Reverting to the original `botComm.py` restores Telegram-only mode
  (the `notifications` key in session data is simply ignored)
- `telegramDataIsValid()` still checks only Telegram
- `checkTelegramData()` now returns True if ANY backend is configured
- `getUserResponse()` still reads from Telegram only (bidirectional)

## Dependencies

No new dependencies. Uses the `requests` library already required by ikabot.

## Files

| File | Purpose |
|------|---------|
| `helpers/botComm.py` | Enhanced drop-in replacement — routes sendToBot() to all backends |
| `helpers/discordComm.py` | Discord webhook send/validate/setup functions |
| `helpers/ntfyComm.py` | ntfy.sh send/validate/setup functions |
| `function/notificationSetup.py` | Interactive setup menu module |
| `command_line.patch` | Instructions for adding menu entry |
