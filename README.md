# ArtCraft ChatGPT MCP

This project provides one unified **ArtCraft** MCP connection for ChatGPT:

- **Website**: [artcraft-storytold-mcp.onrender.com](https://artcraft-storytold-mcp.onrender.com)
  is the public introduction and read-only Storytold project catalog.
- **ArtCraft branding**: the local MCP server uses the official
  [ArtCraft icon](https://getartcraft.com/artcraft-icon.svg).
- **ArtCraft apps and projects**: list, search, and read public Storytold
  repositories, including the Craft creative-app projects.
- **ArtCraft image generation**: submit image-generation jobs and check their
  status. Connect the local server with OpenAI's Secure MCP Tunnel so the
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
to enable API access. The key is shown once; do not paste it into ChatGPT, add
it to Render, or commit it to Git.

Save the key in the macOS Keychain:

1. Open **Keychain Access** and select the **login** keychain.
2. Create a new password item with the item name
   `artcraft-chatgpt-mcp`.
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
ChatGPT, read-only tools can inspect, search, and extract PDF text; creating a
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

Set `PHOTOCRAFT_CLI_PATH` if the CLI is installed elsewhere. In ChatGPT, call
`list_photocraft_tools` to see the upstream tool names and input schemas, then
call `call_photocraft_tool` with a discovered name and its arguments. The native
PhotoCraft tool result, including structured data and images, is forwarded to
ChatGPT. Because the native tool set includes editing and UI controls, calls
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
     --mcp-command "/Users/YOUR_MAC_USERNAME/workspace/artcraft_chatgpt_app/.venv/bin/python /Users/YOUR_MAC_USERNAME/workspace/artcraft_chatgpt_app/local_artcraft_mcp.py"

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

The local MCP server exposes image generation, the read-only project catalog,
and the supported PdfCraft and PhotoCraft workflows as one ChatGPT app. Connect
that unified server only; do not add the public catalog's `/mcp/` endpoint as a
second ChatGPT app, because it duplicates the project-discovery tools. The
public Render website remains available separately and does not receive or use
the ArtCraft API key.

## Use the image tools

- `generate_artcraft_image`: submits a prompt and model, returning a job token.
  An example ArtCraft model ID is `flux_1_schnell`; use a model available to
  your account. Generation may use credits.
- `get_artcraft_image_job`: checks a job token and returns progress or the
  completed image URL. Pending jobs can be checked again shortly.
- `inspect_pdf_document`, `search_pdf_text`, and `extract_pdf_text`: read PDFs.
- `combine_pdf_documents`, `extract_pdf_pages`, and `rotate_pdf_pages`: create
  new PDF files without replacing their source or an existing output.
- `list_photocraft_tools`: discover PhotoCraft's native MCP tool schemas.
- `call_photocraft_tool`: invoke a discovered PhotoCraft tool, with access
  restricted to `~/Documents/ArtCraft`.

The API key and tunnel runtime key are different credentials. Never send either
key in a ChatGPT prompt or commit them to source control.

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
