import random

import pytest

from core import ranking, services, tenancy
from core.models import AuditLog, Issue

pytestmark = pytest.mark.django_db


def order(org, column):
    with tenancy.tenant_context(org.tenant.id):
        return list(Issue.objects.filter(column=column).order_by("rank").values_list("title", flat=True))


@pytest.mark.django_db(False)
def test_between_sorts_strictly_between():
    assert ranking.between("A", "C") == "B"
    assert "A" < ranking.between("A", "B") < "B"
    assert ranking.between(None, None)


@pytest.mark.django_db(False)
def test_between_survives_random_insertion():
    keys = [ranking.between(None, None)]
    rng = random.Random(7)
    for _ in range(3000):
        i = rng.randrange(len(keys) + 1)
        low = keys[i - 1] if i > 0 else None
        high = keys[i] if i < len(keys) else None
        key = ranking.between(low, high)
        assert (low or "") < key and (high is None or key < high)
        keys.insert(i, key)
    assert keys == sorted(keys)


@pytest.mark.django_db(False)
def test_between_rejects_inverted_neighbours():
    with pytest.raises(ValueError):
        ranking.between("B", "A")
    with pytest.raises(ValueError):
        ranking.between("B", "B")


@pytest.mark.django_db(False)
def test_evenly_spaced_keys_are_sorted_and_leave_room():
    for n in (1, 2, 61, 62, 500, 5000):
        keys = ranking.evenly_spaced(n)
        assert keys == sorted(keys) and len(set(keys)) == n
        assert ranking.between(None, keys[0]) < keys[0]


def test_issues_are_numbered_per_project(acme):
    assert [acme.issue().number, acme.issue().number] == [1, 2]


def test_new_issue_goes_to_the_bottom(acme):
    acme.issue("first")
    acme.issue("second")
    assert order(acme, acme.columns[0]) == ["first", "second"]


def test_move_between_two_issues(acme):
    a, b = acme.issue("a"), acme.issue("b")
    c = acme.issue("c")
    resp = acme.client().post(f"/api/issues/{c.id}/move/", {"column": acme.columns[0].id, "before": a.id, "after": b.id}, format="json")
    assert resp.status_code == 200
    assert order(acme, acme.columns[0]) == ["a", "c", "b"]


def test_move_to_top_and_bottom(acme):
    a, _b, c = acme.issue("a"), acme.issue("b"), acme.issue("c")
    api = acme.client()
    api.post(f"/api/issues/{c.id}/move/", {"column": acme.columns[0].id, "after": a.id}, format="json")
    assert order(acme, acme.columns[0]) == ["c", "a", "b"]
    api.post(f"/api/issues/{a.id}/move/", {"column": acme.columns[0].id, "before": _b.id}, format="json")
    assert order(acme, acme.columns[0]) == ["c", "b", "a"]


def test_move_to_another_column(acme):
    a = acme.issue("a")
    existing = acme.issue("existing", column=acme.columns[1])
    resp = acme.client().post(f"/api/issues/{a.id}/move/", {"column": acme.columns[1].id, "before": existing.id}, format="json")
    assert resp.json()["column"] == acme.columns[1].id
    assert order(acme, acme.columns[0]) == []
    assert order(acme, acme.columns[1]) == ["existing", "a"]


def test_move_only_rewrites_the_moved_row(acme):
    issues = [acme.issue(str(i)) for i in range(5)]
    with tenancy.tenant_context(acme.tenant.id):
        before = dict(Issue.objects.values_list("id", "rank"))
    acme.client().post(f"/api/issues/{issues[4].id}/move/", {"column": acme.columns[0].id, "after": issues[0].id}, format="json")
    with tenancy.tenant_context(acme.tenant.id):
        after = dict(Issue.objects.values_list("id", "rank"))
    assert {k for k in after if after[k] != before[k]} == {issues[4].id}


def test_stale_version_gets_409_with_the_current_state(acme):
    issue = acme.issue()
    api = acme.client()
    url = f"/api/issues/{issue.id}/move/"
    assert api.post(url, {"column": acme.columns[1].id, "version": 1}, format="json").status_code == 200
    resp = api.post(url, {"column": acme.columns[2].id, "version": 1}, format="json")
    assert resp.status_code == 409
    assert resp.json()["current"]["column"] == acme.columns[1].id and resp.json()["current"]["version"] == 2


def test_stale_edit_gets_409(acme):
    issue = acme.issue()
    api = acme.client()
    assert api.patch(f"/api/issues/{issue.id}/", {"title": "new", "expected_version": 1}, format="json").json()["version"] == 2
    assert api.patch(f"/api/issues/{issue.id}/", {"title": "newer", "expected_version": 1}, format="json").status_code == 409


def test_colliding_neighbours_trigger_a_rebalance(acme):
    a, b, c = acme.issue("a"), acme.issue("b"), acme.issue("c")
    with tenancy.tenant_context(acme.tenant.id):
        Issue.objects.filter(pk=b.pk).update(rank=Issue.objects.get(pk=a.pk).rank)
        services.move_issue(None, c.id, acme.columns[0].id, a.id, b.id, None)
        ranks = list(Issue.objects.filter(column=acme.columns[0]).values_list("rank", flat=True))
    assert len(set(ranks)) == 3


def test_edit_cannot_change_column_or_project(acme):
    issue = acme.issue()
    assert acme.client().patch(f"/api/issues/{issue.id}/", {"column": acme.columns[1].id}, format="json").status_code == 400


def test_moves_and_edits_are_audited_with_before_and_after(acme):
    issue = acme.issue()
    acme.client().post(f"/api/issues/{issue.id}/move/", {"column": acme.columns[1].id}, format="json")
    with tenancy.tenant_context(acme.tenant.id):
        entry = AuditLog.objects.filter(action="issue.moved").get()
    assert entry.before["column"] == acme.columns[0].id and entry.after["column"] == acme.columns[1].id


def test_audit_log_is_tenant_scoped(acme, globex):
    globex.issue()
    actions = {e["entity_id"] for e in acme.client().get("/api/audit-log/").json()["results"]}
    assert actions == {acme.project.id}


def test_board_groups_issues_by_column_in_rank_order(acme):
    a = acme.issue("a")
    acme.issue("b")
    acme.client().post(f"/api/issues/{a.id}/move/", {"column": acme.columns[0].id}, format="json")
    board = acme.client().get(f"/api/projects/{acme.project.id}/board/").json()
    assert [c["name"] for c in board["columns"]] == services.DEFAULT_COLUMNS
    assert [i["title"] for i in board["columns"][0]["issues"]] == ["b", "a"]
    assert board["columns"][0]["issues"][0]["key"] == "APP-2"
