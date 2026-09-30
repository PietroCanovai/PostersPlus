# PostersPlus Studio

Studio is this fork's own page for managing the posters of a **Jellyfin** library. It lives inside the PostersPlus container at **`http://<server>:8183/studio`**. PostersPlus draws the posters; Studio decides which art, logo and colours each title gets, and sends the result to Jellyfin.

- [Getting started](#getting-started) · [Library](#library) · [Editing a title](#editing-a-title) · [Poster style](#poster-style)
- [Theatre](#theatre-stagemedia) · [Seasons](#seasons) · [Activity and the nightly run](#activity-and-the-nightly-run) · [Settings](#settings) · [Updating](#updating)

## Getting started

1. Open `/studio` and log in with the instance's `ADMIN_KEY`. The browser stays logged in for 30 days.
2. **Settings → Jellyfin**: the server address (e.g. `http://192.168.1.55:8096`) and an API key (Jellyfin: Dashboard → API Keys). Press **Test connection**, then **Save**.
3. **Settings → Libraries**: pick the libraries Studio manages. For each, choose what happens to titles no database knows (like YouTube concert videos): list them under *Needs attention*, or leave them alone.
4. **Activity → Preview run**. Studio reads your library and renders every poster, without touching Jellyfin.
5. When the previews look right, turn on **Upload posters to Jellyfin** and **Run every night** in Settings.

Studio starts safe: until you turn uploads on, every run is a preview.

## Library

Every title Studio manages, with the poster Jellyfin has right now. Chips show what's special about a title: *Pinned*, *Rotating ×3*, *Never ×2*, *Styled*, *Hands off*, *Needs match*, *Error*. A green tick means you've reviewed it.

- **Search, filter and sort** at the top; the library buttons narrow to one library.
- **Review one by one** walks through the list with a big preview: fix what's wrong, then press **Looks good** (or Enter). ← → move without marking.
- **Select** lets you tick titles (or *Select all shown*) and apply **Hands off**, **Manage again**, **Mark reviewed**, **Push now** or **Reset rules** to all of them.

Copies of the same film in two libraries (a 4K and a 1080p, say) share one set of rules.

## Editing a title

The preview on the left is exactly what Jellyfin will get. Everything saves by itself; it reaches Jellyfin in the nightly run, or right away with **Push now**.

**How the title picks its poster**

- **Automatic**: PostersPlus's best textless poster, skipping anything you marked **Never**.
- **Pinned**: always the same *look*.
- **Daily rotation**: a different look each night, shuffled, with no repeats until every look has had its day.

A **look** is a poster plus everything that goes with it: its own logo, colours, frame and layout. In a rotation, click a look in the strip to edit it.

**Posters** tab: every candidate from TMDB, Fanart and TVDB, split into *No text*, *With title* (used as they are, no logo on top), *Backdrops* (you frame a poster-shaped part) and *Yours*. On each: **Pin**, **↻ Rotate** (add to or remove from the rotation) and **Never**.

**Your own images**: **Upload posters** / **Upload backdrops** (several files at once), paste a link (ThePosterDB download links work), or drop files on the upload row. Every image you add stays in the title's library (*Yours* for posters, at the front of *Backdrops* for backdrops, first in *Logos* for logos) until you delete it with ✕, and you use it like any other: Pin it, put it in the rotation, frame a backdrop. Tick **Has its title** on an uploaded poster that already shows the title, so Studio doesn't put a logo on it.

**Logos** tab: the logo for the current look: automatic, the title as text, any candidate, or your own PNGs (**Upload logos**, links or drag and drop). **Never** keeps a logo out of the automatic pick.

**Colours & layout** tab: notch colour, fade colour, notch text colour and logo colour (solid, or keeping the logo's shading). **Pick** takes a colour straight from the preview. Logo size and position, the bottom fade, the notch on or off, and your own notch text.

**Hands off** makes Studio leave the title's poster in Jellyfin alone. **Reset this title** forgets every choice.

Titles Jellyfin couldn't match show a **Find this title on TMDB** panel; the match is kept in Studio only.

## Poster style

The look every poster gets (**Style** in the menu): logo size and language, fades, the notch (shape, look, size, which labels it may show and in what order), rating and quality badges, and the background for titles with no art. **Advanced** takes raw settings, or a poster URL from the PostersPlus configurator.

Changes are a **draft**: they show in the previews on six of your titles (*Other titles* picks new ones; *Before* compares). Jellyfin keeps the current style until you press **Apply to library**, which updates every poster; **Discard changes** throws the draft away and **Undo last apply** brings the previous style back as a draft.

Titles keep their own overrides on top of the style.

## Theatre (StageMedia)

Recordings imported by the Encora plugin have no TMDB entry, only a StageMedia show id. Add your **StageMedia API key** in Settings (and switch the Theatre library on), and Studio makes their posters from StageMedia's artwork, or your uploads. Every recording of the same show shares one set of rules.

Two designs, chosen per show under *Colours & layout → Design*:

- **Posters+ style**: the global style over the art, with your logo or the show's name.
- **Playbill**: the yellow PLAYBILL header with the venue (taken from the recording's name, editable), the art below.

Without a StageMedia key, theatre recordings are left alone.

## Seasons

Turn on **Season posters** in Settings. Each season gets TMDB's season art (or the show's) with "Season 2" in the notch, using the show's colours and layout. Seasons are edited from their show's page (the *Seasons* strip), exactly like a title.

## Activity and the nightly run

Each run reads the library, renders every managed title and uploads a poster **only when it actually changed**. If Jellyfin (a metadata refresh, say) replaced a poster Studio sent, the next run puts it back and says so. Activity shows the next run, live progress, the last runs and their per-title results, and anything that needs attention.

The nightly run happens at the time set in Settings (04:00 by default). If the server was off, it runs as soon as it's back. Rotations move on only in the nightly run.

## Settings

Jellyfin connection, libraries, uploads, the nightly run and its time, season posters, poster size (1000×1500 recommended), the StageMedia key, and **Backup**: download all your rules, restore them from a file. Studio also keeps a backup of the last 14 nights in the cache volume's `studio-backups` folder. API keys are never in a backup.

## Updating

On the server:

```bash
bash ~/docker/postersplus/update.sh              # newest version
bash ~/docker/postersplus/update.sh --rollback   # back to the one before
```

Settings → Version says when an update is available.
