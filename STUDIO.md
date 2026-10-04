# PostersPlus Studio

Studio is this fork's own page for managing the posters of a **Jellyfin** library. It lives inside the PostersPlus container at **`http://<server>:8183/studio`**. PostersPlus draws the posters; Studio decides which art, logo and colours each title gets, and sends the result to Jellyfin.

- [Getting started](#getting-started) · [Library](#library) · [Missing](#missing) · [Editing a title](#editing-a-title) · [Poster style](#poster-style)
- [Theatre](#theatre-stagemedia) · [Seasons](#seasons) · [Activity and the nightly run](#activity-and-the-nightly-run) · [Settings](#settings) · [Updating](#updating)

## Getting started

1. Open `/studio` and log in with the instance's `ADMIN_KEY`. The browser stays logged in for 30 days.
2. **Settings → Jellyfin**: the server address (e.g. `http://192.168.1.55:8096`) and an API key (Jellyfin: Dashboard → API Keys). Press **Test connection**, then **Save**.
3. **Settings → Libraries**: pick the libraries Studio manages. For each, choose what happens to titles no database knows (like YouTube concert videos): list them under *Needs attention*, or leave them alone.
4. **Activity → Preview run**. Studio reads your library and renders every poster, without touching Jellyfin.
5. When the previews look right, turn on **Upload posters to Jellyfin** and **Run every night** in Settings.

Studio starts safe: until you turn uploads on, every run is a preview.

## Library

Every title Studio manages, with the poster Jellyfin has right now. Chips show what's special about a title: *Pinned*, *Rotating ×3*, *Never ×2*, *Styled*, *Hands off*, *Needs match* (not identified, and no image of yours pinned), *Error*. A green tick means you've reviewed it.

- **Search, filter and sort** at the top; the library buttons narrow to one library.
- **Review one by one** walks through the list with a big preview: fix what's wrong, then press **Looks good** (or Enter). ← → move without marking.
- **Select** lets you tick titles (or *Select all shown*) and apply **Hands off**, **Manage again**, **Mark reviewed**, **Push now** or **Reset rules** to all of them.

Copies of the same film in two libraries (a 4K and a 1080p, say) share one set of rules.

## Missing

Every title Jellyfin has no **poster**, **backdrop**, **logo** or **thumb** for (**Missing** in the menu, with the count). The buttons at the top choose which of the four to list; a tile says what it lacks and opens the title on that tab. The list is what Studio saw the last time it read the library (every run does, previews included): **Read again** reads it now.

## Editing a title

The preview on the left is what Jellyfin will get. **Clicking any image previews it** without saving; everything else saves by itself and reaches Jellyfin in the nightly run, or right away with **Push now**. **Hands off** makes Studio leave the title alone; **Reset title** forgets every choice. ← → move to the previous or next title.

One tab per Jellyfin image: **Poster**, **Backdrop**, **Logo**, **Thumb**.

### Poster

- **Automatic**: PostersPlus's best textless poster, skipping anything marked Never.
- **Pinned**: always the same *look*.
- **Daily rotation**: a different look each night, shuffled, no repeats until every look has had its day. The strip shows the looks (*Today*, *Next*); click one to edit it, ✕ on its corner takes it out of the rotation.

A **look** is a poster plus its logo, frame and style. Under the strip:

- **Art**: *No text*, *With title* (used as they are, no logo on top), *Backdrops*, *Frames* and *Yours*. Clicking a backdrop or frame previews it as a poster (centred); ⤢ frames it. On each image: **Pin**, **↻** (in or out of the rotation), **⊘** Never, and **⤢** to frame it (posters and backdrops alike). Add your own with **Upload** (several at once), an image link, or by dropping files; they stay until you delete them (✕). Tick **Has title** on an uploaded poster that already shows the title.
- **Playbill cover** (next to Upload) makes a Playbill-style poster from any image: the yellow PLAYBILL header with the theatre's name, your image below it. Pick an image in the strip (yours, the candidates, frames; **+** uploads a new one, or drop a file on the dialog), drag the window and zoom to choose the part that sits under the header, and check the cover on the right. For theatre the **Theatre** is filled in for you: the show's Broadway house, else its West End one, else wherever your recordings played, the original run before revivals; the box lists the others, and you can type anything (empty = no name). **Save** adds the cover, 1000×1500, to *Yours* (ticked *Has title*, so no logo is drawn on it); **Save and pin** also makes it the poster.
- **Logo**: automatic, the title as text, any candidate or your own PNGs. **All** puts a logo on every look. **Draw the logo** puts the logo on a look even when its art shows its own title (off for such art by default). **Text logo** makes a transparent PNG from any text (18 fonts, colour, capitals, outline, shadow, spacing) and saves it with your logos; *Use* also applies it (here, in the Logo tab for Jellyfin, or as the thumb's logo).
- **Style**: *This poster* or *Whole title*: logo size and position, fades, notch, colours (**⌖** picks a colour from the preview). Each control shows the value it inherits until you change it; ↺ goes back. *Notch labels for this title* switches individual labels off.

**Frames** are stills: TMDB's episode stills for shows, and the chapter images Jellyfin extracted from your files. They work like any other image: frame, Pin, ↻, or pin as the Backdrop or Thumb. For films, FilmGrab and Movie-Screencaps (linked in the tab) have curated stills: copy an image link into the link box.

Backdrops of exactly the size in Settings (1920×1080 by default) come first, then those of unknown size (yours, frames), then every other size, bigger ones included. Under the preview: where the shown image comes from and its size.

### Backdrop, Logo, Thumb

Each tab has its own Upload / link / drop row (your backdrops and logos are shared with the Poster section). *Automatic* follows **Settings → Jellyfin images** (off until you switch it on: Jellyfin keeps its own). A generated Thumb (the landscape render, the Thumb source in Settings) is drawn on whatever art it has, a pinned image included. The Thumb tab has *Art*, *Logo* (the poster's, the title as text, none for art that already shows the title, or any logo) and *Style* (art, logo position, bands, the info line and badge); the Style page's **Thumbs** switch sets that style for the library. A title TMDB doesn't list (known by its IMDb or TVDB id) gets its generated Thumb too: on the backdrop you pinned for it, else on whatever IMDb or TVDB has. Theatre has no automatic pick: pin any image, StageMedia art included (framed to 16:9). *Pinned*: Pin any image (backdrops and thumbs can be framed with ⤢). *Keep Jellyfin's* leaves that image alone for this title. A pushed Backdrop replaces the title's backdrops in Jellyfin, the way a poster replaces the poster: Jellyfin keeps only the one Studio sent.

**Rotating backdrops**: **↻** on a backdrop puts it in the title's *Daily rotation* (the first one switches it on, and a pinned backdrop comes along). Like the posters' rotation: a different one each night, shuffled, no repeats until each has had its day. The strip shows them (*Today*, *Next*); ✕ takes one out, and taking the last one out goes back to Automatic.

**Resize to 1920×1080** (in the Backdrop tab, and in Settings): on, a backdrop you pin or rotate that has another size is cropped to fit (around the middle, or as you framed it) and resized to exactly the size in Settings. It applies to every title. Automatic backdrops are always exactly that size already.

### What you set stays

A choice of yours (a pinned or rotating poster or backdrop, a pinned logo or thumb, a style) is only ever changed by you:

- Studio draws exactly that image. Only what is drawn from data changes by itself: the notch, ratings and quality badges. (A look whose logo is *Automatic* follows PostersPlus's logo pick; choose a logo to fix it.)
- Every run checks what Jellyfin really holds. An image Jellyfin or a plugin replaced, or removed, is put back and reported as *Put back*.
- If Jellyfin later files the title under another id (a re-identify, a plugin), the title stays what it was in Studio, with your choices: its page then says *set by you*, and *Change → Jellyfin's* follows the new id if that is what you want.
- An image of yours that is pinned or in a rotation can't be deleted by a clean-up.
- Backups include these choices.

### Which title is this?

Under a title's name is what Studio knows it by (e.g. *TMDB 949 · IMDb tt0113277*) with **Change**; a title Jellyfin gave no id for shows the panel straight away. It is kept in Studio only: Jellyfin isn't changed. Choose how to identify it:

- **Jellyfin's**: follow Jellyfin's ids (the default): StageMedia for theatre, else TMDB, IMDb or TVDB, whichever it has.
- **TMDB**: search, or paste an id or link.
- **IMDb**: an id or link (`tt0113277`). Works without TMDB: a title only IMDb lists is drawn from IMDb's data.
- **TVDB**: the series or movie id. A title TMDB doesn't list needs a TVDB key in PostersPlus.
- **StageMedia**: search shows by name, give a show id (Encora's and StageMedia's are the same number), or give an Encora recording's id and Studio finds its show. Without an Encora API key (Settings) the search only lists shows already in your library.
- **None**: no database knows it (a home video, a recording). Studio makes the poster from an image you pin: an upload, a link or a frame, with your style and the title as text or a logo of yours.

The images offered come from every database the title's ids reach, not only TMDB: **TMDB** (by TMDB id), **TVDB** (by its own id, or found by IMDb/TMDB id), **Fanart** (films by TMDB or IMDb id, shows by TVDB id) and **IMDb** (its poster, background and logo, by IMDb id, no key needed); theatre titles get **StageMedia**'s. The *source* menu in the Art tab filters them. TVDB and Fanart need their keys in PostersPlus's admin dashboard.

You don't have to identify a title to push a poster: pin an image of yours on an unidentified title and **Push now** (and the nightly run) sends it. Images and choices made before a title was identified come along when you identify it. Seasons follow their show.

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
- **Playbill**: the yellow PLAYBILL header with the theatre (the show's Broadway house, else its West End one; editable under *Venue*), the art below. Studio draws it at every run on whatever art the show has. Leave this on *Posters+* for a cover you made with **Playbill cover**, which already has its header.

Without a StageMedia key, theatre recordings are left alone. A recording the plugin didn't tag (or tagged wrongly) can be tied to its show by hand: *Change → StageMedia* on its page.

## Seasons

Turn on **Season posters** in Settings. Each season gets TMDB's season art (or the show's) with "Season 2" in the notch, using the show's colours and layout. Seasons are edited like a title: the strip at the top of a show (and of each season) has the show and every season; on a season, **← Show** goes back and ← → step through the seasons. In *Review one by one*, seasons stay in the review: **Looks good** on a season returns to its show.

## Activity and the nightly run

Each run reads the library, renders every managed title and uploads a poster **only when it actually changed**. If Jellyfin (a metadata refresh, say) replaced or removed an image Studio sent, the next run puts it back and says so. For backdrops, logos and thumbs it compares the image Jellyfin holds, not just Jellyfin's word for it. Activity shows the next run, live progress, the last runs and their per-title results, and anything that needs attention.

The nightly run happens at the time set in Settings (04:00 by default). If the server was off, it runs as soon as it's back. Rotations move on only in the nightly run.

If Jellyfin restarts while a run is going (after a plugin update, say), the run waits for it, up to five minutes, and carries on. If Jellyfin doesn't come back, the run stops as *failed* and the titles it didn't reach are done by the next one.

**"Jellyfin can't give back the image it was just sent"** means Jellyfin can't read that title's folder: usually a USB drive that was unplugged and plugged back in. Jellyfin then shows no images at all for the titles on it until it is restarted (`docker restart jellyfin`).

## Settings

Jellyfin connection, libraries, uploads, the nightly run and its time, season posters, poster size (1000×1500 recommended), **Jellyfin images** (automatic backdrops of an exact size, textless first; resizing the backdrops you pick to that size; logos; thumbs as a landscape render or a titled backdrop), the StageMedia key, an optional Encora key (only for identifying theatre titles by hand), and **Backup**: download all your rules, restore them from a file. Studio also keeps a backup of the last 14 nights in the cache volume's `studio-backups` folder. API keys are never in a backup.

## Updating

On the server:

```bash
bash ~/docker/postersplus/update.sh              # newest version
bash ~/docker/postersplus/update.sh --rollback   # back to the one before
```

Settings → Version says when an update is available.
