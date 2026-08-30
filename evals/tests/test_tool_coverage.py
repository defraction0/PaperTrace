"""Shape-defensive reading of the tool's own coverage audit.

The audit is about to change shape (label-level -> occurrence-level) in a
parallel workstream. A reader that parses what it recognises and skips the rest
would emit confident, wrong attributions for exactly the entries it failed to
read — so refusal here is wholesale.
"""

from evals.tool_coverage import missing_labels


def test_flat_label_list_is_accepted():
    assert missing_labels(["3", "4"]) == ({"3", "4"}, None)


def test_occurrence_objects_are_accepted_by_their_label():
    got, err = missing_labels([
        {"occurrence_id": "block_0042:118:3", "label": "3"},
        {"occurrence_id": "block_0051:12:4", "label": "4"},
    ])
    assert (got, err) == ({"3", "4"}, None)


def test_an_absent_audit_is_not_an_error_and_yields_no_labels():
    """No audit block ran. That is today's semantics and a different
    disclosure from an audit that ran and cannot be read."""
    assert missing_labels(None) == (set(), None)
    assert missing_labels([]) == (set(), None)


def test_a_non_sequence_is_refused_wholesale():
    got, err = missing_labels("3")
    assert got == set()
    assert err and "expected" in err


def test_a_mixed_collection_is_refused_wholesale():
    got, err = missing_labels(["3", {"label": "4"}])
    assert got == set()
    assert err and "mixes" in err


def test_an_occurrence_with_no_string_label_is_refused_wholesale():
    got, err = missing_labels([{"label": "3"}, {"occurrence_id": "x"}])
    assert got == set()
    assert err


def test_refusal_never_returns_a_partial_label_set():
    """The load-bearing property: a half-read audit must contribute nothing.

    `{"3"}` here would be worse than an error — it reads as 'the tool reached
    label 4', which is a claim the harness cannot support.
    """
    got, err = missing_labels(["3", {"label": "4"}, 7])
    assert got == set()
    assert err is not None
