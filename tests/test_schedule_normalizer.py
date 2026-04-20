import unittest
from datetime import date

from tools.schedule_normalizer import compute_reschedule, current_week_start


WEEK = date(2026, 4, 20)  # Monday


def _post(pid, ct, day, time="09:00", status="draft"):
    return {
        "id": pid,
        "content_type": ct,
        "scheduled_date": f"2026-04-{day:02d}",
        "scheduled_time": time,
        "status": status,
    }


class ComputeRescheduleTests(unittest.TestCase):
    def test_doubled_photo_moves_to_empty_day(self):
        posts = [
            _post(1, "photo", 20),
            _post(2, "photo", 20),  # doubled Monday
            _post(3, "photo", 21),
            _post(4, "photo", 22),
            _post(5, "photo", 23),
            # Friday Apr 24 is empty → post 2 should land there
        ]
        moves = compute_reschedule(posts, WEEK, photo_time="07:00")
        move_map = {pid: (d, t) for pid, d, t in moves}
        self.assertIn(2, move_map)
        self.assertEqual(move_map[2], ("2026-04-24", "07:00"))

    def test_doubled_story_fills_empty_day(self):
        # Two stories on Tuesday, one missing for some other day → move one
        posts = [
            _post(1, "story", 20),
            _post(2, "story", 21),
            _post(3, "story", 21),  # doubled Tuesday
            _post(4, "story", 22),
            _post(5, "story", 23),
            _post(6, "story", 24),
            _post(7, "story", 25),
            # Sunday Apr 26 missing → post 3 moves there
        ]
        moves = compute_reschedule(posts, WEEK, story_time="09:00")
        move_map = {pid: (d, t) for pid, d, t in moves}
        self.assertIn(3, move_map)
        self.assertEqual(move_map[3], ("2026-04-26", "09:00"))

    def test_published_post_is_never_moved(self):
        posts = [
            _post(1, "photo", 20, status="published"),
            _post(2, "photo", 20),  # doubled on published day
        ]
        moves = compute_reschedule(posts, WEEK, photo_time="07:00")
        move_map = {pid: (d, t) for pid, d, t in moves}
        self.assertNotIn(1, move_map)
        # Post 2 should be pushed to a later day (Tuesday)
        self.assertIn(2, move_map)
        self.assertEqual(move_map[2][0], "2026-04-21")

    def test_wrong_time_is_corrected(self):
        posts = [_post(1, "story", 20, time="14:30")]
        moves = compute_reschedule(posts, WEEK, story_time="09:00")
        self.assertEqual(moves, [(1, "2026-04-20", "09:00")])

    def test_already_correct_week_produces_no_moves(self):
        posts = [_post(i, "story", 20 + i - 1, time="09:00") for i in range(1, 8)]
        moves = compute_reschedule(posts, WEEK, story_time="09:00")
        self.assertEqual(moves, [])

    def test_excess_photos_are_unscheduled(self):
        # 6 photos in a week that caps at 5
        posts = [_post(i, "photo", 19 + i) for i in range(1, 7)]
        # Only one is inside the week (Apr 20-26). Actually each on a different
        # day, but id 6 lands on Apr 25 — all still within the week. Rework:
        posts = [
            _post(1, "photo", 20),
            _post(2, "photo", 21),
            _post(3, "photo", 22),
            _post(4, "photo", 23),
            _post(5, "photo", 24),
            _post(6, "photo", 24),  # overflow
        ]
        moves = compute_reschedule(posts, WEEK, photo_time="07:00", photo_days=5)
        move_map = {pid: (d, t) for pid, d, t in moves}
        self.assertIn(6, move_map)
        self.assertEqual(move_map[6], (None, None))

    def test_current_week_start_is_monday(self):
        # Apr 22 2026 is a Wednesday
        self.assertEqual(current_week_start(date(2026, 4, 22)), date(2026, 4, 20))
        # Apr 20 2026 is a Monday
        self.assertEqual(current_week_start(date(2026, 4, 20)), date(2026, 4, 20))
        # Apr 26 2026 is a Sunday
        self.assertEqual(current_week_start(date(2026, 4, 26)), date(2026, 4, 20))


if __name__ == "__main__":
    unittest.main()
