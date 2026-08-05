"""`data_hash` is the audit teeth (§7.3): it must be stable under key
reordering and change whenever `data` changes."""

from app.services.sheq_signature import compute_data_hash


def test_stable_under_key_reordering():
    a = {"a": 1, "b": 2, "c": {"x": 1, "y": 2}}
    b = {"c": {"y": 2, "x": 1}, "b": 2, "a": 1}
    assert compute_data_hash(a) == compute_data_hash(b)


def test_changes_when_a_value_changes():
    a = {"a": 1}
    b = {"a": 2}
    assert compute_data_hash(a) != compute_data_hash(b)


def test_changes_when_a_key_is_added():
    a = {"a": 1}
    b = {"a": 1, "b": 2}
    assert compute_data_hash(a) != compute_data_hash(b)


def test_deterministic_across_calls():
    data = {"a": 1, "nested": {"x": [1, 2, 3]}}
    assert compute_data_hash(data) == compute_data_hash(data)


def test_prefixed_with_sha256():
    assert compute_data_hash({"a": 1}).startswith("sha256:")


def test_empty_dict_hashes_consistently():
    assert compute_data_hash({}) == compute_data_hash({})
