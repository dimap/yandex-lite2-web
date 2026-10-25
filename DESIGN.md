# Station Web design context

## Product and visual direction

This is an owner-operated control panel for one Yandex Station Lite 2. Its
distinctive visual anchor is the physical four-digit segmented display beside
the speaker's 3D form. Status and controls stay compact so the device remains
the focus; cards, charts, and navigation use the existing upstream Station Web
language rather than introducing a second design system.

## Runtime token source

The existing inline theme tokens in `static/index.html` remain canonical. This
file documents those values; it does not generate CSS. Light and dark modes
remap the same semantic roles in the adjacent `:root` selectors.

| Role | Light | Dark | Runtime variable |
| --- | --- | --- | --- |
| Page | `#F6F6F8` | `#111116` | `--bg` |
| Card | `#FFFFFF` | `#1A1A21` | `--card` |
| Main text | `#16161D` | `#EDEDF2` | `--text` |
| Muted text | `#5F5F6E` | `#9C9CAB` | `--muted` |
| Accent | `#6C47FF` | `#8B6CFF` | `--accent` |
| Display | `#16161D` | `#0B0B0F` | `--display` |

Manrope carries headings and body copy; JetBrains Mono carries device values,
raw frames, and chart labels. Cards use the established 18px radius and a quiet
one-pixel border. The interface reflows its navigation and card columns below
760px; the 36-channel editor uses an auto-fitting grid of native labeled range
inputs.

## Interaction and access

Keep existing theme, PWA, keyboard-focus, and mobile patterns. Use native
controls for range, number, file, disclosure, and password input. Each device
write is an explicit action with a bounded TTL and a visible result; imported
animations remain local previews while native Quasar arbitration is unknown.
Unsupported snapshots and live events are described as unavailable rather
than represented with simulated state.
