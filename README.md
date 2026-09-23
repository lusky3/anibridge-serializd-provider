# anibridge-serializd-provider

Serializd provider for [AniBridge](https://github.com/anibridge/anibridge).

## Configuration

```yaml
providers:
  serializd:
    class: anibridge.providers.list.serializd.SerializdListProvider
    config:
      email: "you@example.com"
      password: "your-serializd-password"
```

## Known limitations

- **Status is progress-only.** Serializd has no pause/drop/plan-to-watch
  concept for the account's own watch state (only a friends/social view of
  other users). `status` reflects only `None` (untouched), `CURRENT`
  (partially watched), or `COMPLETED` (fully watched) — writes to `status`
  are a documented no-op.
- **Progress reconstruction assumes linear, in-order watching.** Serializd
  exposes only a "next unwatched episode" pointer, not an enumerated
  watched-episode set. A show watched out of order will report an
  approximate `progress`.
- **A show completed purely through raw per-episode "mark watched" toggles,
  with zero diary/review entries ever created for it, is indistinguishable
  from a show that was never started.** No Serializd API distinguishes the
  two cases. This provider reports such a show as absent.
