import asyncio
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import tmdb
from discovery import extract_discovery_meta, pick_sash, release_date_label
from i18n import load_languages, translate_sash


TODAY = date(2026, 9, 14)


def _iso(days: int, today: date = TODAY) -> str:
    return (today + timedelta(days=days)).isoformat()


class ReleaseDateLabelTests(unittest.TestCase):
    def test_within_a_year_reads_month_day_and_window(self):
        self.assertEqual(release_date_label("2026-10-16", "Cinema", today=TODAY), "Oct 16 Cinema")
        self.assertEqual(release_date_label("2026-12-15", "Streaming", today=TODAY), "Dec 15 Streaming")
        self.assertEqual(release_date_label("2026-11-04", "Physical", today=TODAY), "Nov 4 Physical")
        # Eleven months out still names the day — the coming August is the
        # only August it can mean.
        self.assertEqual(release_date_label("2027-08-01", "Cinema", today=TODAY), "Aug 1 Cinema")

    def test_a_year_or_more_out_reads_month_and_year(self):
        self.assertEqual(release_date_label("2027-12-15", "Cinema", today=TODAY), "Dec 2027 Cinema")
        self.assertEqual(release_date_label(_iso(365), "Cinema", today=TODAY), "Sep 2027 Cinema")
        self.assertEqual(release_date_label(_iso(364), "Cinema", today=TODAY), "Sep 13 Cinema")

    def test_past_today_garbage_and_no_window_give_nothing(self):
        self.assertIsNone(release_date_label("2026-09-14", "Cinema", today=TODAY))
        self.assertIsNone(release_date_label("2026-09-13", "Cinema", today=TODAY))
        self.assertIsNone(release_date_label(None, "Cinema", today=TODAY))
        self.assertIsNone(release_date_label("soon", "Cinema", today=TODAY))
        self.assertIsNone(release_date_label("2026-10-16", None, today=TODAY))


class ReleaseStatusSashTests(unittest.TestCase):
    def _meta(self, status, upcoming=None, window="Cinema"):
        return extract_discovery_meta(
            tmdb_data={}, media_type="movie", award_wins=[], award_noms=[],
            trending_rank=None, release_status_override=status,
            upcoming_release_date=upcoming,
            upcoming_release_window=window if upcoming else None,
        )

    def test_dated_production_and_cinema_wear_the_date_and_window(self):
        soon = (date.today() + timedelta(days=39)).isoformat()
        self.assertEqual(pick_sash(self._meta("Production", soon), ["production"]),
                         (release_date_label(soon, "Cinema"), "alert"))
        self.assertEqual(pick_sash(self._meta("Cinema", soon, "Streaming"), ["cinema"]),
                         (release_date_label(soon, "Streaming"), "alert"))
        self.assertEqual(pick_sash(self._meta("Cinema", soon, "Physical"), ["release_status"]),
                         (release_date_label(soon, "Physical"), "alert"))

    def test_slot_matching_still_keys_off_the_status(self):
        soon = (date.today() + timedelta(days=39)).isoformat()
        meta = self._meta("Cinema", soon)
        self.assertEqual(meta.release_status, "Cinema")
        self.assertIsNone(pick_sash(meta, ["production"]))
        self.assertIsNone(pick_sash(meta, ["streaming"]))

    def test_undated_and_released_titles_keep_the_bare_status(self):
        self.assertEqual(pick_sash(self._meta("Production"), ["production"]),
                         ("Production", "alert"))
        self.assertEqual(pick_sash(self._meta("Cinema"), ["cinema"]),
                         ("Cinema", "alert"))
        # A date on a released title is meaningless and must never be shown.
        soon = (date.today() + timedelta(days=39)).isoformat()
        self.assertEqual(pick_sash(self._meta("Streaming", soon), ["streaming"]),
                         ("Streaming", "alert"))

    def test_a_date_that_has_lapsed_falls_back_to_the_status(self):
        gone = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(pick_sash(self._meta("Production", gone), ["production"]),
                         ("Production", "alert"))


class UpcomingReleaseDateTests(unittest.TestCase):
    def _run(self, status, info, primary=None):
        async def _fake(client, tmdb_id, key, tmdb_status, primary_release_date=None):
            return info
        with patch.object(tmdb, "fetch_movie_release_info", _fake):
            return asyncio.run(tmdb.fetch_upcoming_movie_release(
                None, "1", "k", None, status=status, primary_release_date=primary,
            ))

    def test_production_takes_the_soonest_future_date_and_names_its_window(self):
        info = {"theatrical_date": _iso(60, date.today()),
                "digital_date": _iso(30, date.today()), "physical_date": None}
        self.assertEqual(self._run("Production", info), (_iso(30, date.today()), "Streaming"))
        info = {"theatrical_date": _iso(20, date.today()),
                "digital_date": _iso(30, date.today()), "physical_date": None}
        self.assertEqual(self._run("Production", info), (_iso(20, date.today()), "Cinema"))

    def test_a_same_day_cinema_and_streaming_release_is_a_streaming_one(self):
        # A streamer's film opening in a few cinemas the day it streams
        # (Animals: US theatrical and Netflix both Oct 9) reaches home that day.
        day = _iso(9, date.today())
        info = {"theatrical_date": day, "digital_date": day, "physical_date": None}
        self.assertEqual(self._run("Production", info), (day, "Streaming"))
        info = {"theatrical_date": day, "digital_date": None, "physical_date": day}
        self.assertEqual(self._run("Production", info), (day, "Physical"))

    def test_cinema_ignores_its_own_theatrical_date(self):
        info = {"theatrical_date": _iso(-10, date.today()),
                "digital_date": _iso(45, date.today()), "physical_date": _iso(40, date.today())}
        self.assertEqual(self._run("Cinema", info), (_iso(40, date.today()), "Physical"))
        # Nothing published for home release yet — no date, bare "Cinema".
        info = {"theatrical_date": _iso(-10, date.today()),
                "digital_date": None, "physical_date": None}
        self.assertIsNone(self._run("Cinema", info))
        # The primary date is theatrical; it must not leak in for Cinema.
        self.assertIsNone(self._run("Cinema", info, primary=_iso(20, date.today())))

    def test_production_falls_back_to_the_primary_release_date(self):
        # The pre-release shortcut never fetches /release_dates, so the row
        # carries no dates; the details endpoint's date is all there is.
        info = {"status": "Production"}
        self.assertEqual(self._run("Production", info, primary=_iso(200, date.today())),
                         (_iso(200, date.today()), "Cinema"))
        self.assertIsNone(self._run("Production", info, primary=_iso(-5, date.today())))
        self.assertIsNone(self._run("Production", None))

    def test_released_statuses_never_look(self):
        for status in ("Streaming", "Physical", "Cancelled", None):
            with self.subTest(status=status):
                self.assertIsNone(self._run(status, {"digital_date": _iso(9, date.today())}))


class PreReleaseShortcutTests(unittest.TestCase):
    """A film TMDB calls Planned / In Production skips /release_dates, unless
    its primary release date is ahead: then the dates say what that day is."""

    def _fetch(self, cached, primary, tmdb_status="Planned"):
        calls, stored = [], {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                day = _iso(9, date.today())
                return {"results": [{"iso_3166_1": "US", "release_dates": [
                    {"type": 2, "release_date": day}, {"type": 4, "release_date": day}]}]}

        class _Client:
            async def get(self, url, params=None):
                calls.append(url)
                return _Resp()

        with patch.object(tmdb, "get_cached_movie_release_info", lambda k: cached), \
             patch.object(tmdb, "set_cached_movie_release_info",
                          lambda k, info, *a: stored.update(info)):
            info = asyncio.run(tmdb.fetch_movie_release_info(
                _Client(), "1", "k", tmdb_status, primary_release_date=primary))
        return info, calls

    def test_no_future_primary_date_keeps_the_shortcut(self):
        info, calls = self._fetch(None, None)
        self.assertEqual((info["status"], calls), ("Production", []))
        self.assertTrue(info["pre_release_shortcut"])

    def test_a_future_primary_date_looks_and_finds_the_streaming_day(self):
        info, calls = self._fetch(None, _iso(9, date.today()))
        self.assertEqual(len(calls), 1)
        self.assertEqual(info["digital_date"], _iso(9, date.today()))
        self.assertTrue(info["dates_checked"])

    def test_a_shortcut_row_is_looked_past_but_a_checked_one_is_kept(self):
        shortcut = {"status": "Production", "theatrical_date": None, "digital_date": None,
                    "physical_date": None, "premiere_date": None, "pre_release_shortcut": True}
        _, calls = self._fetch(shortcut, _iso(9, date.today()))
        self.assertEqual(len(calls), 1)
        checked = dict(shortcut, pre_release_shortcut=False, dates_checked=True)
        _, calls = self._fetch(checked, _iso(9, date.today()))
        self.assertEqual(calls, [])


class ReleasedFlagTests(unittest.TestCase):
    def test_released_with_only_future_dates_is_still_in_production(self):
        # TMDB flips "Released" days before the first theatrical date.
        today = date.today()
        self.assertEqual(
            tmdb._compute_movie_status_from_dates(
                today + timedelta(days=2), None, None, "Released"),
            "Production",
        )
        self.assertEqual(
            tmdb._compute_movie_status_from_dates(
                None, today + timedelta(days=30), None, "Released"),
            "Production",
        )

    def test_a_future_festival_premiere_is_proof_it_is_not_out(self):
        today = date.today()
        self.assertEqual(
            tmdb._compute_movie_status_from_dates(
                None, None, None, "Released", today + timedelta(days=10)),
            "Production",
        )
        # ...but a past premiere alone is not a cinema release.
        self.assertEqual(
            tmdb._compute_movie_status_from_dates(
                None, None, None, "Released", today - timedelta(days=10)),
            "Streaming",
        )

    def test_legacy_rows_are_kept_only_when_they_carry_a_date(self):
        # Rows from before limited-theatrical / premiere dates were read.
        self.assertFalse(tmdb._release_info_is_current(
            {"status": "Streaming", "theatrical_date": None,
             "digital_date": None, "physical_date": None}))
        self.assertTrue(tmdb._release_info_is_current(
            {"status": "Cinema", "theatrical_date": "2026-08-01",
             "digital_date": None, "physical_date": None}))
        # Anything written since carries the key, dated or not.
        self.assertTrue(tmdb._release_info_is_current(
            {"status": "Production", "premiere_date": None}))

    def test_released_with_no_dates_at_all_still_reads_streaming(self):
        self.assertEqual(
            tmdb._compute_movie_status_from_dates(None, None, None, "Released"),
            "Streaming",
        )

    def test_a_past_date_still_wins(self):
        today = date.today()
        self.assertEqual(
            tmdb._compute_movie_status_from_dates(
                today - timedelta(days=3), today + timedelta(days=60), None, "Released"),
            "Cinema",
        )


class ReleaseDateTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_languages()

    def test_english_passes_through(self):
        self.assertEqual(translate_sash("Oct 16 Cinema", "en"), "Oct 16 Cinema")
        self.assertEqual(translate_sash("Dec 2027 Cinema", None), "Dec 2027 Cinema")

    def test_english_dates_streaming_as_streams(self):
        self.assertEqual(translate_sash("Sep 29 Streaming", "en"), "Streams Sep 29")
        self.assertEqual(translate_sash("Dec 2027 Streaming", "en-US"), "Streams Dec 2027")
        # A language with no file falls back to English, not the raw label.
        self.assertEqual(translate_sash("Sep 29 Streaming", "ja"), "Streams Sep 29")
        self.assertEqual(translate_sash("Sep 29 Streaming", None), "Streams Sep 29")
        # Other languages keep their generic template.
        self.assertEqual(translate_sash("Aug 1 Streaming", "fr"), "Streaming 1 Août")

    def test_languages_reorder_and_translate_month_and_window(self):
        self.assertEqual(translate_sash("Oct 16 Cinema", "fr-FR"), "Au cinéma 16 Oct")
        self.assertEqual(translate_sash("Aug 1 Streaming", "fr"), "Streaming 1 Août")
        self.assertEqual(translate_sash("Dec 2027 Cinema", "es"), "En cines Dic 2027")
        self.assertEqual(translate_sash("Jan 9 Cinema", "it"), "Al cinema 9 Gen")
        self.assertEqual(translate_sash("Oct 23 Physical", "pt-BR"), "Mídia Física 23 Out")

    def test_every_language_carries_twelve_months_and_both_templates(self):
        from pathlib import Path
        import json
        for path in (Path(__file__).resolve().parents[1] / "languages").glob("*.json"):
            with self.subTest(language=path.stem):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(len(data["monthsShort"]), 12)
                self.assertIn("{day}", data["sashLabels"]["releaseDay"])
                self.assertIn("{window}", data["sashLabels"]["releaseDay"])
                self.assertIn("{year}", data["sashLabels"]["releaseMonth"])
                self.assertIn("{window}", data["sashLabels"]["releaseMonth"])


class JustAddedDigitalDateTests(unittest.TestCase):
    def _run(self, info):
        async def _fake(client, tmdb_id, key, tmdb_status, primary_release_date=None):
            return info
        with patch.object(tmdb, "fetch_movie_release_info", _fake):
            return asyncio.run(tmdb.fetch_recent_movie_digital_release_date(
                None, "1", "k", "Released"))

    def test_a_new_film_arriving_digitally_is_just_added(self):
        fresh = _iso(-3, date.today())
        info = {"theatrical_date": _iso(-60, date.today()),
                "digital_date": fresh, "digital_latest_date": fresh, "physical_date": None}
        self.assertEqual(self._run(info), fresh)

    def test_a_fresh_date_on_an_old_film_is_not(self):
        # Point Break (1991): a cached row whose latest "digital" date is a
        # Canal+ broadcast on the 12th of September 2026.
        fresh = _iso(-3, date.today())
        info = {"theatrical_date": "1991-07-12", "digital_date": "1998-06-20",
                "digital_latest_date": fresh, "physical_date": "1992-01-01"}
        self.assertIsNone(self._run(info))

    def test_tv_broadcasts_are_not_the_latest_digital_date(self):
        fresh = _iso(-3, date.today())

        class _Resp:
            def raise_for_status(self): pass
            def json(self):
                return {"results": [
                    {"iso_3166_1": "US", "release_dates": [
                        {"type": 3, "release_date": _iso(-60, date.today())},
                        {"type": 4, "release_date": _iso(-30, date.today())}]},
                    {"iso_3166_1": "FR", "release_dates": [
                        {"type": 6, "release_date": fresh}]},
                ]}

        class _Client:
            async def get(self, *a, **k): return _Resp()

        with patch.object(tmdb, "get_cached_movie_release_info", lambda k: None), \
             patch.object(tmdb, "set_cached_movie_release_info", lambda *a, **k: None):
            info = asyncio.run(tmdb.fetch_movie_release_info(_Client(), "1", "k", "Released"))
        self.assertEqual(info["digital_latest_date"], _iso(-30, date.today()))
        self.assertEqual(info["digital_date"], _iso(-30, date.today()))


if __name__ == "__main__":
    unittest.main()
