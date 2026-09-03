"""Candidate validation tests (loader/match.py)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.match import candidate_ok, is_bad_version, title_matches


class TestTitleMatches(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(title_matches("Зло", "Зло"))
        self.assertTrue(title_matches("Time", "Time"))

    def test_substring_with_word_boundary(self):
        self.assertTrue(title_matches("Зло", "Зло (Live)"))
        self.assertTrue(title_matches("Time", "Time - Official Video"))
        # Not a substring-with-boundary: "Злость" != "Зло"
        self.assertFalse(title_matches("Зло", "Злость"))

    def test_case_and_punctuation(self):
        self.assertTrue(title_matches("viva la vida", "Viva La Vida (Remastered)"))
        self.assertTrue(title_matches("31-я весна", "31-я весна [muzne.net]"))

    def test_wrong_track(self):
        self.assertFalse(title_matches("Дверь в параллельный мир", "Волны"))
        self.assertFalse(title_matches("Time", "Time After Time"))


class TestBadVersion(unittest.TestCase):
    def test_marks_are_bad(self):
        for t in ["Зло (Live)", "Зло (Remix)", "Зло (Cover)", "Зло - Slowed",
                  "Зло (Official Video)", "Зло (клип)", "Зло - Lyric Video",
                  "Зло (Instrumental)", "Зло (Sped Up)"]:
            self.assertTrue(is_bad_version(t, ""), t)

    def test_plain_title_ok(self):
        self.assertFalse(is_bad_version("Зло", ""))
        self.assertFalse(is_bad_version("Daddy Issues", ""))

    def test_marker_in_wanted_title_allowed(self):
        # User asked for a cover → covers must match.
        self.assertFalse(is_bad_version("Time (Cover)", "Time (Cover)"))
        self.assertFalse(is_bad_version("Зло (Live)", "Зло (Live)"))
        # User asked for plain track → cover is rejected.
        self.assertTrue(is_bad_version("Time (Cover)", "Time"))

    def test_trap_bass_edit_markers(self):
        # Genre/upload retags are non-canonical versions ("кривые треки").
        self.assertTrue(is_bad_version("Вино (Trap Remix)", "Вино"))
        self.assertTrue(is_bad_version("Вино (Trap Edit)", "Вино"))
        self.assertTrue(is_bad_version("Вино (Bass Boosted)", "Вино"))

    def test_sped_up_variants(self):
        # sped_up / Sped-Up / spedup — all spellings of the speed marker.
        self.assertTrue(is_bad_version("Track sped_up", "Track"))
        self.assertTrue(is_bad_version("Track Sped-Up", "Track"))
        self.assertTrue(is_bad_version("Track spedup", "Track"))

    def test_original_mix_not_a_version(self):
        # "Original Mix" is the canonical version, not a remix upload.
        self.assertFalse(is_bad_version("Вино (Original Mix)", "Вино"))


class TestCandidateOk(unittest.TestCase):
    def test_accept_canonical(self):
        self.assertTrue(candidate_ok("Электрофорез", "Зло", "ЭЛЕКТРОФОРЕЗ – Зло", "ЭЛЕКТРОФОРЕЗ"))

    def test_reject_cover_by_other_artist(self):
        self.assertFalse(candidate_ok("Электрофорез", "Зло", "LASTTEREN — Зло", "LASTTEREN"))

    def test_reject_version_marker(self):
        self.assertFalse(candidate_ok("Электрофорез", "Зло", "Зло (slowed)", "Электрофорез"))

    def test_reject_video_clip(self):
        self.assertFalse(candidate_ok("Coldplay", "Yellow", "Yellow (Official Video)", "Coldplay"))

    def test_accept_when_wanted_has_marker(self):
        self.assertTrue(candidate_ok("X", "Time (Cover)", "Time (Cover)", "X"))
        self.assertTrue(candidate_ok("X", "Зло (Live)", "Зло (Live)", "X"))

    def test_empty_artist_skips_artist_check(self):
        self.assertTrue(candidate_ok("", "Зло", "Зло", "Anyone"))

    def test_wrong_artist_rejected(self):
        self.assertFalse(candidate_ok("Электрофорез", "Зло", "Зло", "Nikolya"))

    def test_remaster_request_matches_plain_candidate(self):
        # Requested "Выше домов (ремастер)", candidate is plain "Выше домов"
        self.assertTrue(candidate_ok("Сироткин", "Выше домов (ремастер)",
                                     "Сироткин – Выше домов [mp3uk.net]", "Сироткин"))
        self.assertTrue(candidate_ok("Сироткин", "Выше домов (ремастер)",
                                     "Сироткин – выше домов", "Сироткин"))

    def test_remaster_candidate_rejected_for_plain_request(self):
        # User asked for the plain track; a "(remastered)" copy is a version.
        self.assertFalse(candidate_ok("X", "Выше домов", "Выше домов (ремастер)", "X"))

    def test_collaborator_list_matches(self):
        self.assertTrue(candidate_ok("Электрофорез", "Зло",
                                     "Электрофорез,Ash Code – Зло", "Электрофорез,Ash Code"))

    def test_artist_substring_too_loose(self):
        # "Римас (Сироткин)" is a different performer, not a collaborator.
        self.assertFalse(candidate_ok("Сироткин", "Выше домов",
                                      "Римас (Сироткин) – Выше домов", "Римас (Сироткин)"))

    def test_reject_trap_bass_edit_versions(self):
        # Trap/bass/edit retags on a candidate the user didn't ask for.
        self.assertFalse(candidate_ok("Khleb", "Вино", "Вино (Trap Edit)", "Khleb"))
        self.assertFalse(candidate_ok("Khleb", "Вино", "Вино (Trap Remix)", "Khleb"))
        self.assertFalse(candidate_ok("Khleb", "Вино", "Вино (Bass Boosted)", "Khleb"))

    def test_accept_original_mix(self):
        # "Original Mix" is the canonical version — must be accepted.
        self.assertTrue(candidate_ok("Электрофорез", "Зло",
                                     "Зло (Original Mix)", "Электрофорез"))

    def test_artist_name_with_marker_words(self):
        # An artist whose NAME carries a marker word must not have its
        # own canonical tracks rejected ("DJ Bass - Track" is "Track").
        self.assertTrue(candidate_ok("Ivan House", "Track", "Ivan House - Track", "Ivan House"))
        self.assertTrue(candidate_ok("DJ Bass", "Track", "DJ Bass - Track", "DJ Bass"))

    def test_high_freq_markers_not_stripped_in_title_match(self):
        # Regression: trap/bass are excluded from strip-equality, so
        # "Trap Queen" no longer reduces to "queen" and must NOT match
        # a plain "Queen" / "Super" upload (residue fix).
        self.assertFalse(candidate_ok("Fetty Wap", "Trap Queen", "Queen", "Fetty Wap"))
        self.assertFalse(title_matches("Trap Queen", "Queen"))
        self.assertFalse(candidate_ok("Nicki Minaj", "Super Bass", "Super", "Nicki Minaj"))


if __name__ == "__main__":
    unittest.main()
