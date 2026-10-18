# Create's built-in sounds

Every sound Create puts in a Short without a library is made by Clipper's own code when the video is built, from
sine waves and noise (`src/clipper/create/sound.py`, `effect()`). No sound file ships with Clipper and none is taken
from anyone: there is nothing for Content ID to match and no licence to follow (D156, D197, D223). They are free to
use and share like the rest of Clipper's code.

| Sound | Made from | Used for |
|---|---|---|
| Boom | a deep sine falling in pitch, saturated, with a low noise tail | sound hits after a surprising line (D223) |
| Record scratch | noise swept down and back up, twice | hits on a turn; cut-ins (D197) |
| Bonk | a wood block: three decaying sines | hits after something dumb; cut-ins |
| Boing | a wobbling, rising sine | hits after something silly; cut-ins |
| Ding | a small bell on the music's scale | hits on a right answer; highlighted numbers |
| Pop | a short blip dropping in pitch | hits; the hook |
| Whoosh | band-passed noise swept up and down | hits; whip transitions |
| Riser | noise and a tone climbing two octaves, ending on its moment | hits before a reveal; before the punchline |
| Thump | a low falling sine with a click | hits; punch-ins |

Edit-only sounds, not offered as hits: zoom, swish, swell, chalk and the two-note sound logo.

## The sound library (optional)

With `FREESOUND_API_KEY` in `.env` (Settings, Footage and sound libraries), a hit's sound can come from
[Freesound](https://freesound.org) through its official API (apiv2). Only sounds under
[CC0](https://creativecommons.org/publicdomain/zero/1.0/) are offered and kept, checked again when one is picked,
so no credit is owed in a video. The preview file (MP3) is what's downloaded; the original file would need an
OAuth sign-in. Picked sounds are kept in `<data>/create/sfx/` as `freesound-<id>.mp3`.

Freesound's API terms: a free key, rate limits not published, "Terms for commercial use of the API will be
negotiated on a case by case basis". Clipper fetches a few sounds by hand for one person's channel; ask Freesound
before running it as a hosted product for others.

Pixabay also has sound effects on its site, but its API covers only images and videos, so the Pixabay key
doesn't reach them.
