# ArtCraft Plugin

This project provides one unified **ArtCraft** MCP plugin that works with any
MCP-capable AI assistant, including ChatGPT, Claude, Gemini CLI, Grok (via the
xAI API), and Kimi:

- **Website**: [artcraft-storytold-mcp.onrender.com](https://artcraft-storytold-mcp.onrender.com)
  is the public introduction and read-only Storytold project catalog.
- **Official ArtCraft website**: [getartcraft.com](https://getartcraft.com).
- **ArtCraft branding**: the local MCP server uses the
  [open-source ArtCraft icon from the Storytold repository](https://github.com/storytold/artcraft/blob/main/frontend/apps/artcraft/app/public/artcraft-icon.svg).
- **ArtCraft apps and projects**: list, search, and read public Storytold
  repositories, including the Craft creative-app projects.
- **ArtCraft image and video generation**: list models, see the price first,
  submit jobs and check their status. Connect the local server with OpenAI's Secure MCP Tunnel so the
  ArtCraft API key stays on your Mac.
- **PdfCraft PDF workflows**: inspect and search PDFs, extract text or selected
  pages, combine PDFs, and rotate pages into a new PDF. PDF access is confined
  to `~/Documents`.
- **PhotoCraft**: discover and invoke the official PhotoCraft MCP tools. Its
  file access is confined to the dedicated `~/Documents/ArtCraft` workspace.

The repository tools are read-only discovery; they do not control the apps in
those repositories. Image generation can consume ArtCraft account credits. The
local server asks for one image by default and supports a maximum batch of four.
PdfCraft writes new output files only and refuses to overwrite existing files.
PhotoCraft's generic native-tool proxy is treated as write-capable. VectorCraft
and the remaining video, audio, and 3D app operations are not exposed until
their official MCP interfaces provide a verified workspace boundary.

## Image-generation prerequisites

ArtCraft API keys are enabled per account. In
[ArtCraft](https://app.getartcraft.com/), open **Account → Settings → API Keys**
and create a key. If the API Keys section is missing, contact the ArtCraft team
to enable API access. The key is shown once; do not paste it into a chat, add
it to Render, or commit it to Git.

Save the key in the macOS Keychain:

1. Open **Keychain Access** and select the **login** keychain.
2. Create a new password item with the item name
   `artcraft-plugin`. (Items saved under the earlier name
   `artcraft-chatgpt-mcp` are still read.)
3. Set **Account Name** to the output of `whoami` in Terminal and **Password**
   to the ArtCraft API key.

The local server reads that exact Keychain item only when an ArtCraft tool is
called. For automated testing, `ARTCRAFT_API_KEY` can instead be provided as a
local environment variable. Never set it on the public Render service.

## PdfCraft PDF workflows

The local server delegates PDF work to the official PdfCraft CLI over stdio,
with the CLI's `--root` set to `~/Documents`. It does not expose the PdfCraft
CLI as a network service. This installation matches the PdfCraft 0.4.0 desktop
app. Download the official macOS universal release and verify its published
checksum before installing:

```sh
mkdir -p /tmp/pdfcraft-cli-0.4.0 "$HOME/.local/share/pdfcraft-cli/0.4.0"
curl -fL \
  https://github.com/storytold/pdfcraft/releases/download/v0.4.0/pdfcraft-cli-0.4.0-macos-universal.zip \
  -o /tmp/pdfcraft-cli-0.4.0.zip
echo "a8bf7a4274d94ced4ec30f0086fd8541704960b61ca59c2e68e973d9bfb79ffe  /tmp/pdfcraft-cli-0.4.0.zip" \
  | shasum -a 256 -c -
unzip -q /tmp/pdfcraft-cli-0.4.0.zip -d /tmp/pdfcraft-cli-0.4.0
cp -R /tmp/pdfcraft-cli-0.4.0/pdfcraft-cli-0.4.0-macos-universal/. \
  "$HOME/.local/share/pdfcraft-cli/0.4.0/"
chmod 755 "$HOME/.local/share/pdfcraft-cli/0.4.0/pdfcraft-cli"
```

Set `PDFCRAFT_CLI_PATH` if the executable is installed somewhere else. In
your assistant, read-only tools can inspect, search, and extract PDF text; creating a
combined, extracted, or rotated PDF is a write operation and should require
approval. Source files are left unchanged. The output folder must already exist
inside `~/Documents`, the output filename must be new, and input/output paths
outside that folder (including symlink escapes) are rejected.

## PhotoCraft workspace integration

PhotoCraft's official MCP server runs as a persistent local subprocess. Both its
automation read root and write root, and its working directory, are set to
`~/Documents/ArtCraft`; the CLI rejects file access outside those roots. The
matching official PhotoCraft 0.5.0 CLI is installed at
`~/.local/share/artcraft-cli/photocraft/0.5.0/photocraft-cli`. Install the
matching macOS universal release and verify its published checksum before
installing:

```sh
mkdir -p /tmp/photocraft-cli-0.5.0 \
  "$HOME/.local/share/artcraft-cli/photocraft/0.5.0" \
  "$HOME/Documents/ArtCraft"
curl -fL \
  https://github.com/storytold/photocraft/releases/download/v0.5.0/photocraft-cli-0.5.0-macos-universal.zip \
  -o /tmp/photocraft-cli-0.5.0.zip
echo "8af20fa1254a17f75cd99ccf1d25b6c9ad5cfb51c6a47ff7463c228ef25ad01c  /tmp/photocraft-cli-0.5.0.zip" \
  | shasum -a 256 -c -
unzip -q /tmp/photocraft-cli-0.5.0.zip -d /tmp/photocraft-cli-0.5.0
spctl --assess --type install -vv \
  /tmp/photocraft-cli-0.5.0/photocraft-cli-0.5.0-macos-universal/photocraft-cli
cp /tmp/photocraft-cli-0.5.0/photocraft-cli-0.5.0-macos-universal/photocraft-cli \
  "$HOME/.local/share/artcraft-cli/photocraft/0.5.0/photocraft-cli"
chmod 755 "$HOME/.local/share/artcraft-cli/photocraft/0.5.0/photocraft-cli"
```

Set `PHOTOCRAFT_CLI_PATH` if the CLI is installed elsewhere. In your assistant, call
`list_photocraft_tools` to see the upstream tool names and input schemas, then
call `call_photocraft_tool` with a discovered name and its arguments. The native
PhotoCraft tool result, including structured data and images, is forwarded to
the assistant. Because the native tool set includes editing and UI controls, calls
through the proxy are marked write-capable.

VectorCraft is not enabled yet. Although current upstream source documents
automation root flags, the latest official VectorCraft 0.8.0 CLI release
tested here rejects `--automation-read-root`; this server will not bridge its
file-capable tools until a released CLI passes the workspace-confinement check.

## Run the local MCP server

Install project dependencies in a virtual environment if not already installed:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The private server uses stdio and is intended to be launched by
`tunnel-client`; it does not open an inbound network port. You can also run it
directly for MCP Inspector:

```sh
python local_artcraft_mcp.py
```

## Connect it to Claude, Gemini, and other MCP clients

Desktop and command-line clients can launch the local server directly over
stdio, with no tunnel. Point them at the virtual environment's Python and
`local_artcraft_mcp.py`:

```json
{
  "mcpServers": {
    "artcraft": {
      "command": "/Users/YOUR_MAC_USERNAME/workspace/artcraft_plugin/.venv/bin/python",
      "args": ["/Users/YOUR_MAC_USERNAME/workspace/artcraft_plugin/local_artcraft_mcp.py"]
    }
  }
}
```

- **Claude Desktop:** add the block above to
  `~/Library/Application Support/Claude/claude_desktop_config.json`.
- **Claude Code:** `claude mcp add artcraft -- <python path> <server path>`.
- **Gemini CLI:** add the same `mcpServers` block to `~/.gemini/settings.json`.
- **Cursor, Kimi CLI, and other clients:** use their MCP settings with the same
  command and arguments.

Web chat apps that cannot launch local processes (claude.ai, the Gemini and
Grok web apps) can use the public `/studio` endpoint instead, where the client
supports custom remote MCP servers. Which consumer apps allow that is decided
by each vendor, not by this server.

## Connect it to ChatGPT with Secure MCP Tunnel

Secure MCP Tunnel keeps this local MCP server off the public internet. It
requires a tunnel ID, a Runtime API key for `tunnel-client`, and permission to
use that tunnel in the ChatGPT workspace.

1. In [OpenAI Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels),
   create a tunnel associated with the ChatGPT workspace you will use. Copy its
   `tunnel_id`.
2. Create a **Runtime API key** in
   [OpenAI Platform API keys](https://platform.openai.com/settings/organization/api-keys).
   This is separate from the ArtCraft API key. Do not use an OpenAI Admin API key
   for the tunnel client.
3. Install the official macOS client using Homebrew:

   ```sh
   brew install openai/tools/tunnel-client
   ```

4. In Terminal, enter the OpenAI Runtime API key without adding it to shell
   history. In the default macOS zsh shell, run:

   ```zsh
   read -s "CONTROL_PLANE_API_KEY?OpenAI Runtime API key: "
   echo
   export CONTROL_PLANE_API_KEY
   ```

5. Initialize and run the tunnel profile. Replace the tunnel ID and project
   path with your own values:

   ```sh
   tunnel-client init \
     --sample sample_mcp_stdio_local \
     --profile artcraft-local \
     --tunnel-id tunnel_REPLACE_WITH_YOUR_ID \
     --mcp-command "/Users/YOUR_MAC_USERNAME/workspace/artcraft_plugin/.venv/bin/python /Users/YOUR_MAC_USERNAME/workspace/artcraft_plugin/local_artcraft_mcp.py"

   tunnel-client doctor --profile artcraft-local --explain
   tunnel-client run --profile artcraft-local
   ```

   Keep this Terminal process running while you use ArtCraft. After changing
   MCP tools, stop the old process with `Ctrl-C`, run `tunnel-client run
   --profile artcraft-local` again in the same Terminal, then refresh ArtCraft's
   tools in ChatGPT so it picks up the updated tool list.
6. In [ChatGPT Plugins](https://chatgpt.com/plugins), select **+ → Add custom
   MCP server**, choose **Tunnel** for the connection, select this tunnel, and
   create the app. The connection itself needs no separate MCP authentication.

The local MCP server exposes image and video generation, the read-only project catalog,
and the supported PdfCraft and PhotoCraft workflows as one ChatGPT app. Connect
that unified server only; do not add the public catalog's `/mcp/` endpoint as a
second ChatGPT app, because it duplicates the project-discovery tools. The
public Render website remains available separately and does not receive or use
the ArtCraft API key.

## Use the generation tools

- `list_artcraft_models`: ArtCraft's image or video models with the aspect
  ratios, resolutions, durations and batch sizes each accepts.
  `available_with_api_key` marks the ones this server can generate; the others
  run through other services' accounts inside the desktop app.
- `estimate_artcraft_cost`: ArtCraft's own price for a generation, in credits
  and US cents. It spends nothing.
- `generate_artcraft_image` and `generate_artcraft_video`: submit a job and
  return a job token. Free models start straight away. A paid one starts only
  when `max_credits` covers ArtCraft's estimate; otherwise the tool returns the
  price so the assistant can ask you first. `generate_artcraft_video` can also
  animate an image from an `https` URL (`start_frame_image_url`).
- `get_artcraft_job`: checks a job token and returns progress or the finished
  image or video URL. Pending jobs can be checked again shortly.
  (`get_artcraft_image_job` still works for images.)

Your media library, saved 3D Stage scenes and job history aren't available:
ArtCraft accepts only the desktop app's sign-in for those, not API keys.
- `inspect_pdf_document`, `search_pdf_text`, and `extract_pdf_text`: read PDFs.
- `combine_pdf_documents`, `extract_pdf_pages`, and `rotate_pdf_pages`: create
  new PDF files without replacing their source or an existing output.
- `list_photocraft_tools`: discover PhotoCraft's native MCP tool schemas.
- `call_photocraft_tool`: invoke a discovered PhotoCraft tool, with access
  restricted to `~/Documents/ArtCraft`.

The API key and tunnel runtime key are different credentials. Never send either
key in a chat prompt or commit them to source control.

## Public website and project catalog

The public website is [https://artcraft-storytold-mcp.onrender.com](https://artcraft-storytold-mcp.onrender.com).
It introduces the unified integration and displays public Storytold repositories.
For local development, run the catalog at `http://127.0.0.1:5001/`; the health
check is `http://127.0.0.1:5001/health`.

The legacy read-only MCP catalog is available at
`https://artcraft-storytold-mcp.onrender.com/mcp/` for existing clients. Do not
add it as another ChatGPT app; its repository tools are already included in the
unified ArtCraft connection. Render supplies HTTPS and
`RENDER_EXTERNAL_HOSTNAME`, which the service uses for the MCP Host allowlist.
The `GITHUB_TOKEN` setting is optional and only used for a higher GitHub API
rate limit.

## Verify

```sh
python -m unittest discover -s tests -v
```

The real-app checks run inside the Docker image, where the Craft CLIs are installed:

```sh
docker build -t artcraft-plugin .
docker run --rm -v "$PWD/tests:/app/tests:ro" artcraft-plugin \
  python -m unittest tests.test_craft_real -v
```

## ArtCraft Studio (public, multi-user)

`/studio` is the public MCP endpoint for anyone using ChatGPT, Claude, or another
MCP client. Users sign in with OAuth; every tool works only on the signed-in
user's own files in S3-compatible storage such as Backblaze B2 or Cloudflare R2
(`users/<opaque-user-id>/media/…`). No
personal API key is shared with the public.

| Area | Tools |
| --- | --- |
| Library | `list_media`, `import_media_from_url`, `create_upload_link`, `get_download_link`, `inspect_media`, `delete_media` |
| Video & audio (FFmpeg) | `trim_video`, `transform_video` (resize, speed, mute, rotate, crop, convert), `join_videos`, `add_audio_to_video`, `add_text_to_video`, `extract_audio`, `capture_frame` |
| Images (Pillow) | `edit_image` (crop, resize, rotate, flip, brightness/contrast/saturation, blur, sharpen, grayscale, sepia, border, text…), `layer_images` |
| PDF | `read_pdf_text`, `organize_pdf` (reorder, delete, extract, rotate), `merge_pdfs`, `add_text_to_pdf`, `fill_pdf_form`, `watermark_pdf`, `sign_pdf` |
| Craft apps | `list_craft_apps`, `open_craft_session`, `describe_craft_tools`, `add_craft_files`, `run_craft_tool`, `save_craft_files`, `close_craft_session` |

Edits never overwrite: each produces a new file and a one-hour download link.
`sign_pdf` adds a visible electronic signature plus an audit-trail page with the
original document's SHA-256; it is not a certificate-based (PAdES) digital
signature.

Safety limits: URL imports are HTTPS-only and refuse private/internal addresses;
FFmpeg only accepts ordinary media containers from local files (no playlists or
network inputs); images are capped at 80 MP; files default to 500 MB and each
user to 5 GB (`STUDIO_MAX_FILE_MB`, `STUDIO_USER_QUOTA_MB`).

### Hosted Craft apps

Studio also runs Storytold's open-source Craft apps on the server, so people can
use them from claude.ai, the ChatGPT website, or a phone with nothing installed.
Eight apps are included:

| App | What it does | File types |
| --- | --- | --- |
| WordCraft 0.12.0 | Documents | docx, doc, odt, rtf, md, txt, tex; exports PDF, PNG |
| DeckCraft 0.12.0 | Presentations | pptx, .deckcraft; exports PDF, PNG, JPEG |
| PdfCraft 0.15.0 | PDF editing, forms, redaction, OCR, accessibility | pdf; exports docx, pptx, txt, images |
| PhotoCraft 0.17.0 | Photo editing | png, jpg, webp, gif, bmp, tiff, psd, .pcraft |
| VectorCraft 0.18.0 | Vector art (always started with `--headless`) | .vectorcraft, pdf, ai, png, jpg |
| DesignCraft 0.14.0 | Page layout | .designcraft, idml, images; exports PDF, PNG |
| FigCraft 0.12.1 | Interface design | .figcraft, .fig, images; exports PNG, JPG, PDF, WebP |
| SoundCraft 0.14.0 | Audio editing and mixing | .scraft, wav, aiff, flac, mp3, ogg, m4a, mid; bounces WAV, AIFF, FLAC |

The other Craft apps are left out until their folder lock can be trusted on a
shared server:

- **CADCraft, FilmCraft, GridCraft and LightCraft** don't enforce
  `--automation-read-root`/`--automation-write-root` in their current releases.
  Support is in progress upstream. At least one earlier build accepted the flags
  silently without enforcing them, so a listed flag is never taken on trust.
- **EffectCraft 0.18.0** has the flags, but testing found commands that skip
  them: one reads files outside the folder through relative paths and symlinks,
  and another writes outside it.
- **AniCraft** has no command-line or MCP server yet.

SVG and HTML exports are not stored in the library, because a download link that
renders active content is an XSS risk; save as PDF or PNG instead.

An assistant calls `open_craft_session` with an app and the user's media ids,
then drives the app with `run_craft_tool` using the app's own tools (listed in
the session; for apps with many tools, such as PdfCraft, the list has names and
short descriptions and `describe_craft_tools` returns full schemas), saves with the app's save tool, and calls `save_craft_files` to
store the results as new library files.

Each session is one copy of the app's MCP server for one user:

- It runs in a private temporary folder holding copies of that user's files,
  started inside the folder with `--automation-read-root` and
  `--automation-write-root` set to it. Apps are only listed in
  [`craft_apps.json`](craft_apps.json) after checking by hand that they refuse
  every path outside the folder (absolute paths, `../`, and symlinks).
- It gets a minimal environment, so it never sees storage keys or OAuth
  settings, plus limits on memory (`CRAFT_MEMORY_LIMIT_MB`, default 1536), file
  size, and time per call (`CRAFT_CALL_TIMEOUT_SECONDS`, default 120). A call
  that runs over closes the session.
- Tools that need the desktop app (screenshots, clicks, UI bridges) or reach
  outside the session (printing, OS keychains, PdfCraft's JavaScript console)
  are hidden. Engine commands that the apps' generic command tools could reach
  and that leave the session are refused before the app sees them
  (`blocked_commands`): FigCraft's live-collaboration servers, publishing and
  JavaScript plug-ins, and SoundCraft's record-folder setting.
- Sessions close after `CRAFT_IDLE_MINUTES` (default 10) without use. Each user
  may have `CRAFT_MAX_SESSIONS_PER_USER` (default 2) open, and the server
  `CRAFT_MAX_SESSIONS` (default 6).
- Sessions live in the server's memory, so run Studio as a single instance.

The Docker build downloads each app's Linux release, checks it against the
sha256 pinned in `craft_apps.json`, and keeps only `<app>-cli` (plus its license
files) in `/opt/craft`. To add an app, check its confinement (including its
generic command tools, and paths that are relative or go through symlinks), then add its
version, checksums, file types, hidden tools, blocked commands, and any start
options (`mcp_args`) to the manifest.

### Deploy

1. **OAuth (Auth0 shown; Clerk works the same way).** Create an API with
   identifier `https://artcraft-storytold-mcp.onrender.com/studio` and RS256
   signing. Enable *Dynamic Client Registration* and the *Resource Parameter
   Compatibility Profile* so ChatGPT and Claude can register themselves, and
   promote a login connection (e.g. Google, email) to a domain-level connection.
2. **Storage.** Either provider works; Backblaze B2 needs no card for its free
   10 GB.
   - *Backblaze B2:* create a **private** bucket, then an application key
     restricted to that bucket with read and write access. Set `S3_ENDPOINT_URL`
     (the bucket's endpoint, e.g. `https://s3.us-west-004.backblazeb2.com`),
     `S3_ACCESS_KEY_ID` (keyID), `S3_SECRET_ACCESS_KEY` (applicationKey), and
     `S3_BUCKET`.
   - *Cloudflare R2:* create a private bucket and an API token scoped to *Object
     Read & Write* on it; set `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
     `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET`.

   Allow `GET` and `PUT` from any origin in the bucket's CORS rules (presigned
   URLs carry their own authorization) and expire objects after, e.g., 7 days.
3. **Render.** The service builds from `Dockerfile` (includes FFmpeg). Set
   `OAUTH_ISSUER` (e.g. `https://<tenant>.us.auth0.com/` — exact, including the
   trailing slash), `OAUTH_AUDIENCE` (the API identifier above), and the storage
   variables in the dashboard. Long videos need more than the free plan's
   512 MB of memory.
4. **Connect.** In ChatGPT developer mode or Claude's custom connectors, add
   `https://artcraft-storytold-mcp.onrender.com/studio` and sign in.

Locally, without OAuth, set `STUDIO_DEV_USER=yourname` to act as one user; files
go to `.studio-storage/`. This bypass is disabled on Render.

### Publishing publicly

The ChatGPT listing's **Website**, icon, and "Developer mode" label come from
the app submission, not from this server. Submit the `/studio` URL through the
OpenAI Platform's app submission flow with a verified developer identity,
privacy policy, terms, support contact, icon, and test credentials; it is
public only after review approval. Claude's connector directory has its own
submission form.

## License and credit

Copyright 2026 Adithya Shankaran. ArtCraft Plugin is licensed under the
[Apache License 2.0](LICENSE).

You may use, change and share it, including commercially. If you redistribute
it or a modified version, keep the [`NOTICE`](NOTICE) file and the copyright
lines at the top of each source file, and state what you changed. To cite the
project, use [`CITATION.cff`](CITATION.cff) (GitHub's "Cite this repository"
button).

This is an unofficial community project, not affiliated with Storytold or the
ArtCraft team. The Craft apps are downloaded during the Docker build and keep
their own MIT and Apache-2.0 licenses.
