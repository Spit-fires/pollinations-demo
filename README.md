---
title: Pollinations Postcard Starter
emoji: 🌼
colorFrom: yellow
colorTo: pink
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
license: gpl-3.0
fullWidth: true
suggested_hardware: cpu-basic
short_description: Duplicate, connect Pollen, make image plus narration.
tags:
- gradio
- mcp-server
- pollinations
- byop
---

# Pollinations Postcard Starter

[![Made with pollinations.ai](https://raw.githubusercontent.com/pollinations/pollinations/main/packages/ui/src/brand/badge-made-with.svg)](https://pollinations.ai/?ref=badge)

Duplicate it, connect Pollen, make a postcard. One prompt makes image plus caption plus narration. Visitors pay with their own key, the Space pays nothing.

Built for [quest #17249](https://github.com/pollinations/pollinations/issues/17249): a Space people duplicate to build with Pollinations.

## Use it

1. Choose Sign in with Pollen. That is Bring Your Own Pollen, using the Pollinations device flow: a short code appears, you approve it at enter.pollinations.ai/device, and the page updates by itself. The key is held in `sessionStorage` for that tab only, never sent to this server for storage and never written to logs.
2. Write a prompt, choose Generate postcard.
3. Read the caption and play the narration. The caption is the audio transcript.

Signing out clears the key from the tab.

## MCP

True MCP server via `demo.launch(mcp_server=True)`. One tool: `make_postcard(prompt, api_key, image_model, text_model, voice)`. Typed with an `Args:` docstring so the schema stays clean.

Schema: `/gradio_api/mcp/schema`. Add this Space from Hub MCP settings, no code needed.

## Make it your own

1. Duplicate this Space. CPU Basic is enough: the app calls the Pollinations API and never
   uses a GPU. ZeroGPU duplicates also start, and the runtime probe only runs if clicked.
2. Optional: create an App Key at enter.pollinations.ai/keys and set it as Space Variable
   `POLLINATIONS_APP_KEY`. Variables carry over on Duplicate, Secrets do not. No Redirect URI
   is needed, sign-in uses the device flow, so every duplicate works without extra setup.
   Without a key, approval still works and simply shows the Space hostname instead of your app name.
3. Swap starters, default models, or voices in `app.py`.
4. Model IDs use `publisher/model` names. Aliases still work, match both when restoring selections.
5. Submit with the app submission form, Quest field `#17249`.

## ZeroGPU note

This Space calls the Pollinations API and never uses a GPU; CPU Basic is enough.
ZeroGPU duplicates still start: ZeroGPU requires at least one `@spaces.GPU`
function, so the one-second `gpu_runtime_check` probe exists to satisfy that
check. It runs only if you click the Runtime check button, so duplicates consume
no GPU quota by default. Keep `suggested_hardware: cpu-basic`.

## Config

- `GEN_BASE` (default `https://gen.pollinations.ai`), `ENTER_BASE` (default `https://enter.pollinations.ai`), `POLLINATIONS_APP_KEY` (`pk_...`, public App Key).
- Outbound calls use ports 80/443 only.
- CPU Basic sleeps after idle, wakes on visit.
