"""Global trait definitions. Treat names as stable identifiers."""

# A scalar measures the strength of a tendency from 0.0 to 1.0.
# A binary represents whether a specific default behavior is enabled.
GROUPS = (
    ("Agency", "visible", "scalar", """
        courage persistence self_determination motivation initiative independence
        ambition discipline adaptability patience decisiveness resourcefulness
        conscientiousness competitiveness focus industriousness
    """),
    ("Thinking and creativity", "visible", "scalar", """
        curiosity innovativeness imagination analytical_thinking critical_thinking
        ingenuity open_mindedness attention_to_detail intellectual_humility
        practical_judgment aesthetic_sensitivity planning foresight improvisation
        observational_skill love_of_learning systems_thinking
    """),
    ("Social temperament", "visible", "scalar", """
        sociability warmth empathy compassion generosity cooperativeness
        assertiveness diplomacy tact expressiveness humor playfulness
        hospitality leadership persuasiveness protectiveness forgiveness
        loyalty reliability candor discretion
    """),
    ("Emotions and habits", "visible", "scalar", """
        optimism composure resilience enthusiasm sensitivity emotional_awareness
        tolerance_for_ambiguity routine_preference adventurousness
        orderliness frugality punctuality diligence contemplativeness
    """),
    ("Private drives", "hidden", "scalar", """
        self_interest recognition_seeking status_seeking need_for_belonging
        need_for_control approval_seeking novelty_seeking desire_for_security
        secrecy power_motivation achievement_drive perfectionism
        fear_of_failure fear_of_rejection rivalry vindictiveness
        acquisitiveness attachment_to_place
    """),
    ("Private social tendencies", "hidden", "scalar", """
        gossip envy jealousy suspicion suggestibility conformity
        impression_management strategic_flattery reciprocity
        conflict_avoidance grudge_retention emotional_guardedness
        social_comparison blame_shifting boundary_sensitivity
        willingness_to_reconcile trust_propensity
    """),
    ("Private decision tendencies", "hidden", "scalar", """
        impulsivity risk_tolerance loss_aversion delay_discounting
        uncertainty_aversion ambiguity_seeking sunk_cost_sensitivity
        confirmation_bias pattern_seeking rumination boredom_sensitivity
        reward_sensitivity threat_sensitivity habit_strength frustration_tolerance
        persistence_under_uncertainty moral_flexibility
    """),
    ("Public habits", "visible", "binary", """
        keeps_a_journal introduces_self_first volunteers_for_chores
        keeps_a_calendar celebrates_birthdays greets_neighbors
        makes_lists hosts_gatherings carries_a_memento
        prefers_written_messages shares_credit names_belongings
        asks_before_borrowing keeps_a_workbench checks_in_after_conflict
    """),
    ("Private habits", "hidden", "binary", """
        rehearses_conversations keeps_anonymous_notes conceals_worry
        has_a_lucky_ritual seeks_second_opinions_before_commitment
        tracks_favors_privately checks_locks_twice needs_solitude_after_gatherings
        revisits_old_letters avoids_asking_for_help prefers_to_apologize_privately
        drafts_messages_before_sending keeps_emergency_supplies
        keeps_a_private_wish_list quietly_tests_new_ideas
    """),
)

TRAITS = {
    name: {"group": group, "visibility": visibility, "type": kind}
    for group, visibility, kind, names in GROUPS
    for name in names.split()
}

# Version 2 birth probabilities. Keep these stable for already-issued drafts.
BINARY_PROBABILITIES = {
    "greets_neighbors": 0.80,
    "keeps_a_journal": 0.30,
    "keeps_anonymous_notes": 0.15,
    "celebrates_birthdays": 0.75,
    "hosts_gatherings": 0.25,
    "checks_locks_twice": 0.35,
    "shares_credit": 0.70,
}
