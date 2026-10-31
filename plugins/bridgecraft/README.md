# BridgeCraft

BridgeCraft connects Claude to Storytold's open-source Craft apps, the editors
behind [ArtCraft](https://getartcraft.com): WordCraft, DeckCraft, PdfCraft,
PhotoCraft, VectorCraft, DesignCraft, FigCraft and SoundCraft. Ask for a
document, a slide, a flyer, a photo edit, a drawing or a short piece of audio,
and Claude opens the right app, does the work and saves the result as a new file
in your library. Your originals are never changed.

BridgeCraft is a community project by Adithya Shankaran. It is not an official
Storytold or ArtCraft product.

## What this plugin does

The plugin adds one remote MCP server and nothing else: no skills, hooks,
commands or local programs.

- **Where it connects:** `https://artcraft-storytold-mcp.onrender.com/studio`,
  over HTTPS.
- **Sign-in:** the first time it is used, Claude asks you to sign in through
  your browser (OAuth). The plugin never reads credentials from your computer.
- **What it sends:** the files you choose to upload, and the requests Claude
  makes to the tools. They are stored in a private folder for your account only.
- **How long files are kept:** files are deleted automatically after 7 days.
  Download links stop working after an hour.

Privacy policy: https://artcraft-storytold-mcp.onrender.com/privacy
Terms: https://artcraft-storytold-mcp.onrender.com/terms
Support: https://artcraft-storytold-mcp.onrender.com/support
Live status: https://artcraft-storytold-mcp.onrender.com/status

## License

Apache-2.0. The source is at https://github.com/adikshan11/bridgecraft.
