# Security and privacy

Tweebit is designed for one owner on a trusted local computer. It is not a multi-tenant or publicly hosted service.

## Access boundaries

The bot accepts control messages only from `OWNER_ID` in private chat. The editor binds to loopback and requires a short-lived, single-use code from the bot. Browser sessions expire after 12 hours and end on restart. Host, origin and same-site cookie checks protect local API mutations.

Telegram source reading uses anonymous public previews without personal-account sessions, cookies or authentication. Only explicitly selected source usernames are requested. Source links and media are not followed. X uses an app-only token for selected public accounts.

Text content is sent to the configured AI provider. Image generation sends a visual brief to fal.ai only when explicitly requested. These providers have their own data policies.

## Operational care

Keep `.env`, editor swap files, database backups and generated media private. Do not include them in Git, Docker build contexts, issue reports or screenshots. Rotate exposed tokens through their issuing provider. Back up SQLite while the service is stopped; retain backups locally.

AI verification can miss unsupported statements. Generated pictures are illustrations, not evidence. Human review remains necessary.

## Reporting a vulnerability

Do not put live credentials or sensitive content in a public issue. Use GitHub's private vulnerability reporting for this repository if enabled; otherwise contact the repository owner privately. Include a minimal redacted reproduction and the affected commit.
