# BridgeCraft

BridgeCraft is an independent, community-built MCP plugin for ArtCraft and
Storytold's Craft apps. It works with any
MCP-capable AI assistant, including ChatGPT, Claude, Gemini CLI, Grok (via the
xAI API), and Kimi:

- **Website**: [bridgecraft.onrender.com](https://bridgecraft.onrender.com)
  is the public introduction and read-only Storytold project catalog.
- **Official ArtCraft website**: [getartcraft.com](https://getartcraft.com).
- **Branding**: BridgeCraft uses its own name and logo (`static/bridgecraft.svg`, a phoenix
  rising with its wings raised, traced in VectorCraft by `design/draw_logo.py`). The
  ArtCraft name, wordmark and logos are trademarks of the ArtCraft Team and are not open
  source. BridgeCraft never uses them as its own branding: the name appears in plain text
  to say what BridgeCraft works with. The unmodified ArtCraft logo appears on the catalog
  card that links to ArtCraft's repository and on the back of the homepage phoenix, labelled
  "Built for ArtCraft", with "Not an official Storytold or ArtCraft product" in the footer of
  every page. The other cards show each Craft app's
  own icon, which is licensed like its code.
- **ArtCraft apps and projects**: list, search, and read public Storytold
  repositories, including the Craft creative-app projects.
- **ArtCraft image and video generation**: list models, see the price first,
  submit jobs and check their status. This runs in the local server on your own
  Windows, macOS or Linux computer, so the ArtCraft API key stays there.
- **PdfCraft PDF workflows**: inspect and search PDFs, extract text or selected
  pages, combine PDFs, and rotate pages into a new PDF. PDF access is confined
  to your Documents folder.
- **PhotoCraft**: discover and invoke the official PhotoCraft MCP tools. Its
  file access is confined to the dedicated `ArtCraft` folder inside Documents.

The repository tools are read-only discovery; they do not control the apps in
those repositories. Image generation can consume ArtCraft account credits. The
local server asks for one image by default and supports a maximum batch of four.
PdfCraft writes new output files only and refuses to overwrite existing files.
PhotoCraft's generic native-tool proxy is treated as write-capable. VectorCraft
and the remaining video, audio, and 3D app operations are not exposed until
their official MCP interfaces provide a verified workspace boundary.

## What BridgeCraft is

BridgeCraft extends Storytold's open-source Craft apps, the ones ArtCraft brings
together, into ChatGPT, Claude and other AI assistants. They are the same apps,
run as they are: you ask in a chat, and your assistant uses them for you.

**Status:** community-built and run by Adithya Shankaran. It is not an official
Storytold or ArtCraft product. I have proposed that Storytold adopt it in
[RFC-0016](https://github.com/storytold/craft-contrib/pull/23); until they do,
it must not be presented as part of ArtCraft.

## Run the local MCP server

The local server runs on your own computer, so ArtCraft image and video
generation can use your own ArtCraft API key without it leaving the machine. It
runs on the same systems as ArtCraft:

- **Windows** 10 or 11, x64 or Arm64
- **macOS**, Intel or Apple Silicon
- **Linux**, x86_64 or aarch64

You need Python 3.12 (what I test with) and a copy of this repository. The
steps below show each system's commands. On Windows they are PowerShell; on
macOS and Linux they are a POSIX shell such as zsh or bash.

### 1. Install the server

macOS and Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

### 2. Save your ArtCraft API key

ArtCraft API keys are enabled per account. In
[ArtCraft](https://app.getartcraft.com/), open **Account → Settings → API Keys**
and create a key. If the API Keys section is missing, contact the ArtCraft team
to enable API access. The key is shown once. Don't paste it into a chat, add it
to Render, or commit it to Git.

The server reads the key from your computer's own credential store: the macOS
Keychain, Windows Credential Manager, or the Secret Service on Linux (GNOME
Keyring or KWallet). It looks for a password saved under the service name
`artcraft-plugin` with your user name as the account, and reads it only when an
ArtCraft tool is called. Items saved under the earlier name
`artcraft-chatgpt-mcp` are still read.

Save the key with the `keyring` command that step 1 installed. It asks for the
key without showing it or saving it in your shell history:

```sh
# macOS and Linux
.venv/bin/python -m keyring set artcraft-plugin "$(whoami)"
```

```powershell
# Windows
.venv\Scripts\python -m keyring set artcraft-plugin $env:USERNAME
```

On macOS you can instead make the item by hand: in **Keychain Access**, select
the **login** keychain, create a password item named `artcraft-plugin`, set
**Account Name** to the output of `whoami`, and set **Password** to the key.

A Linux machine with no desktop keyring (a server, say) has no credential store
to save into. There, set `ARTCRAFT_API_KEY` in the environment of the process
that starts the server, typed in so it stays out of your history:

```sh
read -rs -p "ArtCraft API key: " ARTCRAFT_API_KEY; echo
export ARTCRAFT_API_KEY
```

`ARTCRAFT_API_KEY` works on every system and is also handy for automated tests.
Never set it on the public Render service.

### 3. Install the PdfCraft and PhotoCraft CLIs (optional)

The PDF and PhotoCraft tools hand the work to the official PdfCraft and
PhotoCraft command-line tools. Skip this step if you only want image and video
generation. I pin the same releases the hosted Studio runs, PdfCraft 0.15.0 and
PhotoCraft 0.17.0, because I have checked that their folder limits hold.

Each release is a different file per system. On macOS it's the universal CLI
zip. On Windows the CLI, `<app>-cli.exe`, ships inside the portable zip (the
`.msi` installers are for the desktop app). On Linux it's `bin/<app>-cli`
inside the app tarball. These are the files and their SHA-256 checksums, from
each release's `SHA256SUMS.txt`:

| App | System | File | SHA-256 |
| --- | --- | --- | --- |
| PdfCraft 0.15.0 | macOS | `pdfcraft-cli-0.15.0-macos-universal.zip` | `0304db20f3597e4e9ad3044d67f79cb89bdad49833c6ad7a60c0d0d6a7afaa50` |
| PdfCraft 0.15.0 | Windows x64 | `pdfcraft-0.15.0-windows-x64-portable.zip` | `92df7bef0b4c1eb7a59d0410716903e69fe4d2c4ea0074c6b53780096c3e20f9` |
| PdfCraft 0.15.0 | Windows Arm64 | `pdfcraft-0.15.0-windows-arm64-portable.zip` | `12207c2a822c92a80b126835c57e57ed2270ae2944db471f0a19bdb70e2cfa8d` |
| PdfCraft 0.15.0 | Linux x86_64 | `pdfcraft-0.15.0-linux-x86_64.tar.gz` | `b167a18c60a7b329f05a5bbf97e32dc678aad3f8d5866d8e4378ff24273f8313` |
| PdfCraft 0.15.0 | Linux aarch64 | `pdfcraft-0.15.0-linux-aarch64.tar.gz` | `b380bc7a4c28d05d7ea95eb23085ca322c49ca1a21b8bb79389a8e083ae92b9d` |
| PhotoCraft 0.17.0 | macOS | `photocraft-cli-0.17.0-macos-universal.zip` | `6edff99cc1977508bea9b5566f551374e7b8ef42a21502ee084a1d969f0d54c0` |
| PhotoCraft 0.17.0 | Windows x64 | `photocraft-0.17.0-windows-x64-portable.zip` | `3453777af0a0b376bfea0d1ccf42bedcbe1c324e1836b50d6227b9e1d64a20ab` |
| PhotoCraft 0.17.0 | Windows Arm64 | `photocraft-0.17.0-windows-arm64-portable.zip` | `fe0f621702ee723d51d771f245aed2de2e5111349bce524afc04a66efa1c2627` |
| PhotoCraft 0.17.0 | Linux x86_64 | `photocraft-0.17.0-linux-x86_64.tar.gz` | `5259f9c428c8ee3478c35500ece98713561cafc050e4c9eb354a5050f36eb2b8` |
| PhotoCraft 0.17.0 | Linux aarch64 | `photocraft-0.17.0-linux-aarch64.tar.gz` | `a9478dee2b34f270a1f845a7d7f4da501c529d443680ee23c2c8b588867d61b8` |

Run the commands for your system once per app. Set `app`, `v` and `sum` (or
`sums`) from the table first; the PdfCraft values are filled in. Each download
goes into a new empty folder and is checked before anything is unpacked.

macOS (the universal zip runs on Intel and Apple Silicon):

```sh
app=pdfcraft v=0.15.0 sum=0304db20f3597e4e9ad3044d67f79cb89bdad49833c6ad7a60c0d0d6a7afaa50
dl=$(mktemp -d)
curl -fL "https://github.com/storytold/$app/releases/download/v$v/$app-cli-$v-macos-universal.zip" \
  -o "$dl/$app.zip"
echo "$sum  $dl/$app.zip" | shasum -a 256 -c -
unzip -q "$dl/$app.zip" -d "$dl"
spctl --assess --type install -vv "$dl/$app-cli-$v-macos-universal/$app-cli"
dest="$HOME/.local/share/artcraft-cli/$app/$v"
mkdir -p "$dest"
cp -R "$dl/$app-cli-$v-macos-universal/." "$dest/"
```

`spctl` should report the binary as notarized by Learning Machines LLC; stop if
it doesn't.

Linux:

```sh
app=pdfcraft v=0.15.0
arch=$(uname -m)  # x86_64 or aarch64
case "$arch" in
  x86_64) sum=b167a18c60a7b329f05a5bbf97e32dc678aad3f8d5866d8e4378ff24273f8313 ;;
  aarch64) sum=b380bc7a4c28d05d7ea95eb23085ca322c49ca1a21b8bb79389a8e083ae92b9d ;;
esac
dl=$(mktemp -d)
curl -fL "https://github.com/storytold/$app/releases/download/v$v/$app-$v-linux-$arch.tar.gz" \
  -o "$dl/$app.tar.gz"
echo "$sum  $dl/$app.tar.gz" | sha256sum -c -
tar -xzf "$dl/$app.tar.gz" -C "$dl" "$app-$v-linux-$arch/bin/$app-cli"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/artcraft-cli/$app/$v"
mkdir -p "$dest"
install -m 755 "$dl/$app-$v-linux-$arch/bin/$app-cli" "$dest/$app-cli"
```

Windows:

```powershell
$app = "pdfcraft"; $v = "0.15.0"
$sums = @{
  x64   = "92df7bef0b4c1eb7a59d0410716903e69fe4d2c4ea0074c6b53780096c3e20f9"
  arm64 = "12207c2a822c92a80b126835c57e57ed2270ae2944db471f0a19bdb70e2cfa8d"
}
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
$name = "$app-$v-windows-$arch-portable"
$dl = New-Item -ItemType Directory (Join-Path $env:TEMP ([guid]::NewGuid()))
$zip = Join-Path $dl "$app.zip"
$ProgressPreference = "SilentlyContinue"
Invoke-WebRequest "https://github.com/storytold/$app/releases/download/v$v/$name.zip" -OutFile $zip
if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $sums[$arch]) {
  throw "Checksum mismatch. Don't install this file."
}
Expand-Archive $zip -DestinationPath $dl
$dest = Join-Path $env:LOCALAPPDATA "artcraft-cli\$app\$v"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Recurse "$dl\$name\*" $dest
```

The server looks for each CLI at these paths:

| System | Path |
| --- | --- |
| macOS | `~/.local/share/artcraft-cli/<app>/<version>/<app>-cli` |
| Linux | `$XDG_DATA_HOME/artcraft-cli/<app>/<version>/<app>-cli`, or under `~/.local/share` when `XDG_DATA_HOME` isn't set |
| Windows | `%LOCALAPPDATA%\artcraft-cli\<app>\<version>\<app>-cli.exe` |

If you put a CLI somewhere else, set `PDFCRAFT_CLI_PATH` or
`PHOTOCRAFT_CLI_PATH` to its full path.

PhotoCraft also needs its workspace folder, `ArtCraft` inside your Documents
folder:

```sh
# macOS
mkdir -p "$HOME/Documents/ArtCraft"
# Linux
mkdir -p "$(xdg-user-dir DOCUMENTS 2>/dev/null || echo "$HOME/Documents")/ArtCraft"
```

```powershell
# Windows
New-Item -ItemType Directory -Force (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "ArtCraft")
```

"Documents" means the folder your system calls Documents: `~/Documents` on
macOS, the Documents known folder on Windows (which OneDrive may have moved),
and `XDG_DOCUMENTS_DIR` on Linux, falling back to `~/Documents`.

#### PdfCraft PDF workflows

The local server runs the PdfCraft CLI over stdio, with its read and write
roots set to your Documents folder. It doesn't expose the CLI as a network
service. In your assistant, read-only tools can inspect, search, and extract PDF
text; creating a combined, extracted, or rotated PDF is a write operation and
should require approval. Source files are left unchanged. The output folder
must already exist inside Documents, the output filename must be new, and
input and output paths outside that folder (including symlink escapes) are
rejected.

#### PhotoCraft workspace integration

PhotoCraft's official MCP server runs as a persistent local subprocess. Its
automation read root, write root and working directory are all the `ArtCraft`
folder inside Documents, and the CLI refuses file access outside it. The tools
that drive the desktop app's window (`control_call` and the `ui_*` tools) are
hidden, as they are in the hosted Studio. In your assistant, call
`list_photocraft_tools` to see the upstream tool names and input schemas, then
call `call_photocraft_tool` with a discovered name and its arguments. The native
PhotoCraft tool result, including structured data and images, is forwarded to
the assistant. Because the native tool set includes editing tools, calls
through the proxy are marked write-capable.

VectorCraft is not enabled yet. Although current upstream source documents
automation root flags, the latest official VectorCraft 0.8.0 CLI release
tested here rejects `--automation-read-root`; this server will not bridge its
file-capable tools until a released CLI passes the workspace-confinement check.

### 4. Start it

The server talks over stdio and doesn't open a network port. Your AI app or
`tunnel-client` starts it, as the next two sections show. To try it by hand,
for example with MCP Inspector:

```sh
.venv/bin/python local_artcraft_mcp.py        # macOS and Linux
```

```powershell
.venv\Scripts\python local_artcraft_mcp.py    # Windows
```

## Connect it to Claude, Gemini, and other MCP clients

Desktop and command-line clients can launch the local server directly over
stdio, with no tunnel. Point them at the virtual environment's Python and
`local_artcraft_mcp.py`, using full paths. On macOS and Linux:

```json
{
  "mcpServers": {
    "artcraft": {
      "command": "/Users/YOU/bridgecraft/.venv/bin/python",
      "args": ["/Users/YOU/bridgecraft/local_artcraft_mcp.py"]
    }
  }
}
```

On Windows, the Python is under `Scripts`, and backslashes are doubled in JSON:

```json
{
  "mcpServers": {
    "artcraft": {
      "command": "C:\\Users\\YOU\\bridgecraft\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\YOU\\bridgecraft\\local_artcraft_mcp.py"]
    }
  }
}
```

- **Claude Desktop** (macOS and Windows): add the block to
  `claude_desktop_config.json`, which is in
  `~/Library/Application Support/Claude/` on macOS and `%APPDATA%\Claude\` on
  Windows.
- **Claude Code:** `claude mcp add artcraft -- <python path> <server path>`.
- **Gemini CLI:** add the same `mcpServers` block to `~/.gemini/settings.json`
  (`%USERPROFILE%\.gemini\settings.json` on Windows).
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
3. Install OpenAI's `tunnel-client`.

   On macOS, OpenAI supports Homebrew only (its release zips aren't notarized):

   ```sh
   brew install openai/tools/tunnel-client
   ```

   On Windows and Linux, download the release zip from
   [openai/tunnel-client](https://github.com/openai/tunnel-client/releases) and
   check it against the release's `SHA256SUMS.txt`. Set `release` to the
   release you want. The zip holds `tunnel-client` and the `cloudflared` it
   needs, so keep the two together.

   Linux (`linux-amd64` or `linux-arm64`):

   ```sh
   release=v0.0.19 platform=linux-amd64
   base="https://github.com/openai/tunnel-client/releases/download/$release"
   dl=$(mktemp -d)
   curl -fL "$base/tunnel-client-$release-$platform.zip" -o "$dl/tunnel-client-$release-$platform.zip"
   curl -fL "$base/SHA256SUMS.txt" -o "$dl/SHA256SUMS.txt"
   (cd "$dl" && sha256sum -c --ignore-missing SHA256SUMS.txt)
   dest="$HOME/.local/share/tunnel-client/$release"
   mkdir -p "$dest"
   unzip -q "$dl/tunnel-client-$release-$platform.zip" -d "$dest"
   export PATH="$dest:$PATH"
   ```

   Windows:

   ```powershell
   $release = "v0.0.19"
   $platform = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "windows-arm64" } else { "windows-amd64" }
   $base = "https://github.com/openai/tunnel-client/releases/download/$release"
   $dl = New-Item -ItemType Directory (Join-Path $env:TEMP ([guid]::NewGuid()))
   $zip = Join-Path $dl "tunnel-client-$release-$platform.zip"
   $ProgressPreference = "SilentlyContinue"
   Invoke-WebRequest "$base/tunnel-client-$release-$platform.zip" -OutFile $zip
   Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile (Join-Path $dl "SHA256SUMS.txt")
   $line = Select-String -Path (Join-Path $dl "SHA256SUMS.txt") -SimpleMatch "  tunnel-client-$release-$platform.zip" |
     Select-Object -First 1
   if (-not $line -or (Get-FileHash $zip -Algorithm SHA256).Hash -ne ($line.Line -split "\s+")[0]) {
     throw "Checksum mismatch. Don't install this file."
   }
   $dest = Join-Path $env:LOCALAPPDATA "tunnel-client\$release"
   Expand-Archive $zip -DestinationPath $dest
   $env:Path = "$dest;$env:Path"
   ```

   The checksum file comes from the same release page as the zip. For a check
   that doesn't rest on that page, `gh attestation verify` confirms the zip was
   built by OpenAI's release workflow; the tunnel-client README describes it.

4. Enter the OpenAI Runtime API key without adding it to shell history.

   macOS (zsh, the default shell):

   ```zsh
   read -s "CONTROL_PLANE_API_KEY?OpenAI Runtime API key: "
   echo
   export CONTROL_PLANE_API_KEY
   ```

   Linux (bash):

   ```sh
   read -rs -p "OpenAI Runtime API key: " CONTROL_PLANE_API_KEY; echo
   export CONTROL_PLANE_API_KEY
   ```

   Windows (PowerShell):

   ```powershell
   $secure = Read-Host "OpenAI Runtime API key" -AsSecureString
   $env:CONTROL_PLANE_API_KEY = [System.Net.NetworkCredential]::new("", $secure).Password
   ```

5. Initialize and run the tunnel profile. Replace the tunnel ID and the paths
   with your own; use the same Python and server paths as in the client
   configuration above (on Windows, `.venv\Scripts\python.exe`). Paths with
   spaces need quoting inside `--mcp-command`, so a folder without spaces is
   simplest.

   ```sh
   tunnel-client init \
     --sample sample_mcp_stdio_local \
     --profile artcraft-local \
     --tunnel-id tunnel_REPLACE_WITH_YOUR_ID \
     --mcp-command "/Users/YOU/bridgecraft/.venv/bin/python /Users/YOU/bridgecraft/local_artcraft_mcp.py"

   tunnel-client doctor --profile artcraft-local --explain
   tunnel-client run --profile artcraft-local
   ```

   In PowerShell, put each command on one line, or end lines with a backtick
   instead of `\`.

   Keep this terminal running while you use ArtCraft. After changing MCP tools,
   stop the old process with `Ctrl-C`, run `tunnel-client run --profile
   artcraft-local` again in the same terminal, then refresh ArtCraft's tools in
   ChatGPT so it picks up the updated tool list.
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
  restricted to the `ArtCraft` folder inside Documents.

The API key and tunnel runtime key are different credentials. Never send either
key in a chat prompt or commit them to source control.

## Public website and project catalog

The public website is [https://bridgecraft.onrender.com](https://bridgecraft.onrender.com).
It introduces the unified integration and displays public Storytold repositories.
For local development, run the catalog at `http://127.0.0.1:5001/`; the health
check is `http://127.0.0.1:5001/health`.

The site used to live at `artcraft-storytold-mcp.onrender.com`. That deployment
now has `MOVED_TO=https://bridgecraft.onrender.com`, which answers every request
except `/health` with a 308 redirect to the same path here, so old links and
connectors that follow redirects keep working.

The legacy read-only MCP catalog is available at
`https://bridgecraft.onrender.com/mcp/` for existing clients. Do not
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
docker build -t bridgecraft .
docker run --rm -v "$PWD/tests:/app/tests:ro" bridgecraft \
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
| Library | `list_media`, `import_chat_files`, `import_media_from_url`, `create_upload_link`, `get_download_link`, `inspect_media`, `delete_media` |
| Video & audio (FFmpeg) | `trim_video`, `transform_video` (resize, speed, mute, rotate, crop, convert), `join_videos`, `add_audio_to_video`, `add_text_to_video`, `extract_audio`, `capture_frame` |
| Images (Pillow) | `edit_image` (crop, resize, rotate, flip, brightness/contrast/saturation, blur, sharpen, grayscale, sepia, border, text…), `layer_images` |
| PDF | `read_pdf_text`, `organize_pdf` (reorder, delete, extract, rotate), `merge_pdfs`, `add_text_to_pdf`, `fill_pdf_form`, `watermark_pdf`, `sign_pdf` |
| Craft apps | `list_craft_apps`, `open_craft_session`, `describe_craft_tools`, `add_craft_files`, `run_craft_tool`, `save_craft_files`, `close_craft_session` |

Edits never overwrite: each produces a new file and a one-hour download link.
`sign_pdf` adds a visible electronic signature plus an audit-trail page with the
original document's SHA-256; it is not a certificate-based (PAdES) digital
signature.

`import_chat_files` takes files attached in the chat. It declares
`openai/fileParams`, so ChatGPT (including on a phone) passes each attachment's
temporary download link to it directly, and it accepts up to 10 files per call.
Other clients can keep using `import_media_from_url` or `create_upload_link`.

Safety limits: URL and chat-file imports are HTTPS-only on port 443 and refuse
private/internal addresses, including after redirects. Each connection goes to
the address that passed that check, so a DNS answer can't change between the
check and the download;
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

- **CADCraft 0.15.1, FilmCraft 0.15.0, GridCraft 0.14.2 and LightCraft 0.16.0**
  don't have `--automation-read-root`/`--automation-write-root` in their
  releases. Support is in progress upstream. At least one earlier build accepted
  the flags silently without enforcing them, so a listed flag is never taken on
  trust.
- **EffectCraft 0.19.0** refuses every path outside the folder that it's given,
  but a project inside the folder can still name footage outside it, and
  rendering shows that footage. storytold/effectcraft#2719 fixes that on main
  and is not in the 0.19.0 release, so Studio does not host this build.
- A shared folder lock and escape tests for every app are proposed in
  [storytold/craft-contrib#22](https://github.com/storytold/craft-contrib/pull/22).
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
   identifier `https://connect.getartcraft.com/mcp` and RS256 signing. Studio
   accepts a token only when the issuer, signature, expiry, audience and the
   `connector` scope all check out, and the audience has to be that identifier
   exactly. Any other audience is rejected. Enable *Dynamic Client Registration*
   and the *Resource Parameter Compatibility Profile* so ChatGPT and Claude can
   register themselves, and promote a login connection (e.g. Google, email) to a
   domain-level connection. The connector token is not sent to ArtCraft.
   Generation and library reads exchange it, at the issuer only, for a separate
   token scoped to `generation` and `library:read`. Studio stores that token
   encrypted with `GRANT_KEY` under the user's own prefix, and deletes it on
   disconnect, account close, or revocation.
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
   URLs carry their own authorization). Studio deletes everything under
   `users/<id>/` 7 days after the session ends (the session ends after
   `CRAFT_IDLE_MINUTES`, default 10, without a request). Deleting a file,
   disconnecting, or closing the account deletes immediately. The sweep runs at
   startup and every hour. A bucket lifecycle rule of the same age is a backstop
   for media objects.
3. **Render.** The service builds from `Dockerfile` (includes FFmpeg). Set
   `OAUTH_ISSUER` (e.g. `https://<tenant>.us.auth0.com/`; it must match exactly, including the
   trailing slash), `GRANT_KEY` (a Fernet key; see `render.yaml`), and the storage
   variables in the dashboard. `GRANT_KEY` is required on Render. Long videos need
   more than the free plan's 512 MB of memory.
4. **Connect.** In ChatGPT developer mode or Claude's custom connectors, add
   `https://bridgecraft.onrender.com/studio` and sign in.

Locally, without OAuth, set `STUDIO_DEV_USER=yourname` to act as one user; files
go to `.studio-storage/`. This bypass is disabled on Render.

### Install it in other apps

Every app below connects to the same hosted address,
`https://bridgecraft.onrender.com/studio`, and asks you to sign in
the first time.

- **Claude Code**, from this repository's plugin marketplace
  ([`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json)), in
  one command:

  ```bash
  claude plugin install bridgecraft --marketplace adikshan11/bridgecraft
  ```

  Inside a session, `/plugin install bridgecraft --marketplace adikshan11/bridgecraft`
  does the same. Older Claude Code versions need two steps:
  `claude plugin marketplace add adikshan11/bridgecraft`, then
  `claude plugin install bridgecraft@bridgecraft`.
- **Cursor:** the "Add to Cursor" link on the website's setup section, or add
  `{"url": "https://bridgecraft.onrender.com/studio"}` under
  `mcpServers.bridgecraft` in `~/.cursor/mcp.json`.
- **VS Code:**

  ```bash
  code --add-mcp '{"name":"bridgecraft","type":"http","url":"https://bridgecraft.onrender.com/studio"}'
  ```

- **Claude and ChatGPT (web, desktop and phone):** add a custom connector with
  the address, as on the website. Once the directory submissions below are
  approved, people can search for BridgeCraft there instead.

### Publishing publicly

Each listing is submitted from the publisher's own account.

1. **Official MCP Registry** (read by VS Code, GitHub, and other clients that
   browse servers). [`server.json`](server.json) describes the hosted server as
   `io.github.adikshan11/bridgecraft`. Install
   [`mcp-publisher`](https://github.com/modelcontextprotocol/registry), then from
   this folder run `mcp-publisher login github` and `mcp-publisher publish`.
   GitHub sign-in proves ownership of the `io.github.adikshan11/` name. Raise
   `version` in `server.json` before each later publish.
2. **Anthropic's directory** ([claude.ai/directory](https://claude.ai/directory)):
   submit the `plugins/bridgecraft` plugin and the `/studio` server as a
   connector from the developer portal at
   [claude.ai/directory/manage](https://claude.ai/directory/manage). It needs a
   paid claude.ai plan, the privacy policy, terms, support page, icon and a test
   account. Once approved, people find it by searching on claude.ai, in the
   desktop app and in Cowork, and Claude Code loads it for them as
   `bridgecraft@synced`.
3. **ChatGPT apps directory:** the listing's **Website**, icon, and "Developer
   mode" label come from the app submission, not from this server. Submit the
   `/studio` URL through the OpenAI Platform's app submission flow with a
   verified developer identity, privacy policy, terms, support contact, icon,
   and test credentials. It is public only after review approval.

The directories list it as BridgeCraft, a community project. If Storytold
adopts it ([RFC-0016](https://github.com/storytold/craft-contrib/pull/23)), it
can be listed under ArtCraft's name with ArtCraft sign-in instead.

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
