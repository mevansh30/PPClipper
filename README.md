# YouTube Livestream Clipper

A simple Flask backend that triggers a clip command from Nightbot, creates a YouTube timestamp link from your currently live stream, and sends it to a Discord webhook.

## Setup

1. Add environment variables in Railway:
   - `YT_API_KEY`
   - `YT_CHANNEL_ID`
   - `DISCORD_WEBHOOK`

2. Deploy this to Railway or another always-on service.

3. Add a custom Nightbot command like:

```
!clip $(urlfetch https://your-railway-url/clip?user=$(user))
```

This will clip the last 35 seconds from your active livestream and send the link to Discord.