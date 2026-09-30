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

The preview on the left is what Jellyfin will get. **Clicking any image previews it** without saving; everything else saves by itself and reaches Jellyfin in the nightly run, or right away with **Push now**. **Hands off** makes Studio leave the title alone; **Reset title** forgets every choice. ← → move to the previous or next title.

One tab per Jellyfin image: **Poster**, **Backdrop**, **Logo**, **Thumb**.

### Poster

- **Automatic**: PostersPlus's best textless poster, skipping anything marked Never.
- **Pinned**: always the same *look*.
- **Daily rotation**: a different look each night, shuffled, no repeats until every look has had its day. The strip shows the looks (*Today*, *Next*); click one to edit it.

A **look** is a poster plus its logo, frame and style. Under the strip:

- **Art**: *No text*, *With title* (used as they are, no logo on top), *Backdrops*, *Frames* and *Yours*. On each image: **Pin**, **↻** (in or out of the rotation), **⊘** Never, and **⤢** to frame it (posters and backdrops alike). Add your own with **Upload** (several at once), an image link, or by dropping files; they stay until you delete them (✕). Tick **Has title** on an uploaded poster that already shows the title.
- **Logo**: automatic, the title as text, any candidate or your own PNGs. **All** puts a logo on every look.
- **Style**: *This poster* or *Whole title*: logo size and position, fades, notch, colours (**⌖** picks a colour from the preview). Each control shows the value it inherits until you change it; ↺ goes back. *Notch labels for this title* switches individual labels off.

**Frames** are stills: TMDB's episode stills for shows, and the chapter images Jellyfin extracted from your files. **Add to backdrops** makes one usable like any backdrop. For films, FilmGrab and Movie-Screencaps (linked in the tab) have curated stills: copy an image link into the link box.

### Backdrop, Logo, Thumb

*Automatic* follows **Settings → Jellyfin images** (off until you switch it on: Jellyfin keeps its own). *Pinned*: Pin any image (backdrops and thumbs can be framed with ⤢). *Keep Jellyfin's* leaves that image alone for this title.

Titles Jellyfin couldn't match show a **Which title is this?** panel; the match is kept in Studio only.

## Poster style

The look every poster gets (**Style** in the menu), with the same controls as a title's Style tab plus the library-wide ones: logo language and baseline, the notch's shape and size, rating and quality badges, and the background for titles with no art. Which labels the notch shows is on the **Notch** page. **Advanced** takes raw settings, or a poster URL from the PostersPlus configurator.

Changes are a **draft**: they show in the previews on six of your titles (*Other titles* picks new ones; *Before* compares). Jellyfin keeps the current style until you press **Apply to library**, which updates every poster; **Discard changes** throws the draft away and **Undo last apply** brings the previous style back as a draft.

Titles keep their own overrides on top of the style.

## Notch

The label at the top of a poster (**Notch** in the menu). A poster shows the first label on the list that applies to its title.

- **Labels**: every label with what triggers it and where the data comes from (MDBList for Oscar/Emmy awards, Metacritic, cult and true-story; PostersPlus's own data for Golden Globes and festival prizes; TMDB for trending, seasons and release status; your lists for notable studios, directors and cast). Switch labels on or off and move them up. This is part of the style, so it's a draft until you **Apply** it.
- **Notable lists**: who counts as a notable director, studio or cast member, and what the notch prints for them (e.g. "Ghibli" for Studio Ghibli). Search TMDB to add someone; names must match TMDB's. **Save** updates posters in the next run; **Back to the built-in list** undoes your list.
- **Per title**, in the editor's *Style* tab: **Notch text** to write your own, and *Notch labels for this title*: what it says now, every label the title qualifies for, and a switch to turn any of them off for that title only.

## Theatre (StageMedia)

Recordings imported by the Encora plugin have no TMDB entry, only a StageMedia show id. Add your **StageMedia API key** in Settings (and switch the Theatre library on), and Studio makes their posters from StageMedia's artwork, or your uploads. Every recording of the same show shares one set of rules.

Two designs, chosen per show under *Style → Theatre → Design*:

- **Posters+ style**: the global style over the art, with your logo or the show's name.
- **Playbill**: the yellow PLAYBILL header with the venue (taken from the recording's name, editable), the art below.

Without a StageMedia key, theatre recordings are left alone.

## Seasons

Turn on **Season posters** in Settings. Each season gets TMDB's season art (or the show's) with "Season 2" in the notch, using the show's colours and layout. Seasons are edited from their show's page (the *Seasons* strip), exactly like a title.

## Activity and the nightly run

Each run reads the library, renders every managed title and uploads a poster **only when it actually changed**. If Jellyfin (a metadata refresh, say) replaced a poster Studio sent, the next run puts it back and says so. Activity shows the next run, live progress, the last runs and their per-title results, and anything that needs attention.

The nightly run happens at the time set in Settings (04:00 by default). If the server was off, it runs as soon as it's back. Rotations move on only in the nightly run.

## Settings

Jellyfin connection, libraries, uploads, the nightly run and its time, season posters, poster size (1000×1500 recommended), **Jellyfin images** (automatic backdrops with a minimum size, 16:9 and textless first; logos; thumbs as a landscape render or a titled backdrop), the StageMedia key, and **Backup**: download all your rules, restore them from a file. Studio also keeps a backup of the last 14 nights in the cache volume's `studio-backups` folder. API keys are never in a backup.

## Updating

On the server:

```bash
bash ~/docker/postersplus/update.sh              # newest version
bash ~/docker/postersplus/update.sh --rollback   # back to the one before
```

Settings → Version says when an update is available.
