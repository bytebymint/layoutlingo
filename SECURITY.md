# Security policy

## Supported configuration

Use the current `main` branch with a unique `SECRET_KEY`, HTTPS, and a production database for any shared deployment. The default configuration binds only to `127.0.0.1`.

## Reporting a vulnerability

Do not open a public issue for a security vulnerability. Contact the maintainer privately through the GitHub account profile with a concise reproduction, affected version, and impact. Do not include real documents, keys, or personal data.

## Deployment note

LayoutLingo is local-first. Setting `HOST=0.0.0.0` makes the app reachable on the network; only do this behind a reverse proxy with TLS and an access-control policy you operate.
