# Haven Quick Recovery

## Start (stable mode)

Terminal A:
```bash
cd /home/travis/.openclaw/workspace/projects/mina-gui
source "$HOME/.cargo/env"
npm run dev
```

Terminal B:
```bash
cd /home/travis/.openclaw/workspace/projects/mina-gui/src-tauri
source "$HOME/.cargo/env"
cargo run --release --no-default-features
```

## If Haven hangs

1. In UI, click **Recovery** in avatar controls.
2. If still stuck, restart both processes:
```bash
pkill -f "mina_gui|target/debug/mina_gui|vite" || true
```
Then run the two startup commands above.

## Overlay fallback

- **Use Live Overlay** toggles live avatar.
- **Force Fallback** keeps UI on local image states.
- Recovery forces fallback + idle state.
