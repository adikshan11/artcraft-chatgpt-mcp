# ArtCraft Storytold MCP

A small, read-only MCP server that lets ChatGPT discover public ArtCraft and related
Storytold repositories. It also includes a browser-based catalog at `/`.

## MCP tools

- `list_artcraft_projects`: list Storytold projects relevant to creative work.
- `search_artcraft_projects`: find projects by repository name or description.
- `get_artcraft_project`: get a repository's public details and README.

The MCP server does **not** launch or control the ArtCraft desktop editor and does
not generate or edit images, video, audio, or 3D scenes. ArtCraft's public source
does not currently provide those capabilities through a documented MCP tools
interface; these tools provide read-only project discovery.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The catalog is at `http://127.0.0.1:5001/`, the health check is at
`http://127.0.0.1:5001/health`, and the Streamable HTTP MCP endpoint is at
`http://127.0.0.1:5001/mcp/`.

ChatGPT needs to reach an MCP server over a public HTTPS URL; `localhost` is not
reachable from ChatGPT. This project includes a Render Blueprint in `render.yaml`.
Push the project to a GitHub repository, create a Blueprint in Render from that
repository, and deploy the `artcraft-storytold-mcp` web service. Render supplies
HTTPS and `RENDER_EXTERNAL_HOSTNAME`, which the service uses for the MCP Host
allowlist while keeping DNS-rebinding protection enabled. For another host, set
`MCP_ALLOWED_HOSTS` to the hostnames accepted in the Host header, e.g.
`your-app.example.com,your-app.example.com:*`.

After deployment, use `https://YOUR-PUBLIC-HOST/mcp/` as the remote MCP server URL
in ChatGPT's developer settings.

In ChatGPT, open **Plugins → + → Add custom MCP server**, enter the HTTPS endpoint,
choose **No authentication** (all tools are read-only public repository lookups),
review the risk prompt, and create the plugin. Install it from your personal
plugins, then select it in a ChatGPT Work chat to call its tools. Availability of
custom MCP apps depends on the ChatGPT account or workspace.

```sh
uvicorn app:asgi_app --host 0.0.0.0 --port 5001 --proxy-headers
```

Set `GITHUB_TOKEN` optionally to a GitHub personal access token with no extra
permissions for a higher GitHub API rate limit. Do not commit tokens to source
control.

## Verify

```sh
python -m unittest discover -s tests -v
```
