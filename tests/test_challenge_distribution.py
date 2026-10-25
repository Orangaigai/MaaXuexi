"""Distribution behavior and scoring-rule priority, independent of UI rendering."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from challenge_preferences import challenge_length_weights,challenge_profile_summary

class ChallengeDistributionTests(unittest.TestCase):
    def test_planned_means_are_truncated_not_the_unbounded_centers(self):
        rows=challenge_profile_summary()
        for row,mean in zip(rows,(4.71,8.81,11.84,16.00,20.86)):
            self.assertAlmostEqual(row['mean'],mean,places=2)

    def test_discarding_lower_tail_does_not_pile_its_mass_on_three(self):
        counts,weights=challenge_length_weights(3,'beginner',50)
        self.assertEqual(counts[0],3)
        self.assertGreater(weights[0],.25);self.assertLess(weights[0],.30)
        self.assertAlmostEqual(sum(weights),1)

    def test_higher_profiles_have_substantial_probability_beyond_fifteen(self):
        rows={r['name']:r for r in challenge_profile_summary()}
        self.assertGreater(rows['knowledgeable']['sixteen_or_more'],.25)
        self.assertGreater(rows['adept']['sixteen_or_more'],.50)
        self.assertGreater(rows['master']['sixteen_or_more'],.70)

    def test_actual_scoring_minimum_outranks_the_configured_upper_limit(self):
        counts,weights=challenge_length_weights(40,'beginner',20)
        self.assertEqual(counts,[40]);self.assertEqual(weights,[1.0])

    def test_small_upper_tail_keeps_finite_normalized_weights(self):
        counts,weights=challenge_length_weights(30,'beginner',50)
        self.assertEqual((counts[0],counts[-1]),(30,50))
        self.assertTrue(all(0<=p<=1 for p in weights));self.assertAlmostEqual(sum(weights),1)

if __name__=='__main__':unittest.main()
