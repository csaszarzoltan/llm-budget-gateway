import pytest

from llm_budget_gateway import scale_suite as s


def test_storage_topology_blocks_multinode_sqlite_and_allows_transactional_shared_store():
    assert not s.StorageTopology().assess(2, "sqlite", True)["ready"]
    assert s.StorageTopology().assess(2, "postgres", True)["ready"]
    with pytest.raises(ValueError):
        s.StorageTopology().assess(1, "unknown", True)


def test_replication_quorum_computes_writability_and_rejects_impossible_availability():
    assert s.ReplicationQuorum().calculate(3, 2) == {
        "quorum": 2,
        "available": 2,
        "writable": True,
        "failure_tolerance": 1,
    }
    with pytest.raises(ValueError):
        s.ReplicationQuorum().calculate(2, 3)


def test_partition_planner_sizes_partitions_and_tenant_distribution():
    r = s.PartitionPlanner().plan(101, 1000, 300)
    assert r["partitions"] == 4 and r["tenants_per_partition"] == 26
    with pytest.raises(ValueError):
        s.PartitionPlanner().plan(0, 1, 1)


def test_consistency_policy_requires_strong_mode_for_budget_and_keys():
    assert not s.ConsistencyPolicy().decide("budget", "eventual")["permitted"]
    assert s.ConsistencyPolicy().decide("cache", "eventual")["permitted"]


def test_failover_planner_selects_first_healthy_distinct_candidate():
    r = s.FailoverPlanner().build("eu", ["eu", "us", "ap"], {"us": False, "ap": True})
    assert r["selected"] == "ap"
    with pytest.raises(ValueError):
        s.FailoverPlanner().build("eu", ["us", "us"], {"us": True})


def test_migration_readiness_fails_closed_until_every_gate_passes():
    gate = s.MigrationReadiness()
    assert not gate.decide({"backup": True})["ready"]
    assert gate.decide({x: True for x in gate.REQUIRED})["ready"]
    with pytest.raises(ValueError):
        gate.decide({"backup": "yes"})


def test_connection_pool_planner_bounds_total_connections_and_invalid_capacity():
    r = s.ConnectionPoolPlanner().plan(4, 100, 20, 0.8)
    assert r["usable"] == 64 and r["per_node"] == 16
    with pytest.raises(ValueError):
        s.ConnectionPoolPlanner().plan(2, 10, 10)


def test_tenant_shard_assignment_is_stable_private_and_validated():
    a = s.TenantShardAssignment().assign("tenant-a", 8)
    b = s.TenantShardAssignment().assign("tenant-a", 8)
    assert a == b and len(a["tenant_fingerprint"]) == 64 and "tenant-a" not in str(a)
    with pytest.raises(ValueError):
        s.TenantShardAssignment().assign("bad tenant", 8)


def test_residency_topology_supports_same_region_and_explicit_pairs_only():
    stores = [{"name": "ledger", "region": "ch"}, {"name": "cache", "region": "de"}]
    assert not s.ResidencyTopology().evaluate("ch", stores)["compliant"]
    assert s.ResidencyTopology().evaluate("ch", stores, [["ch", "de"]])["compliant"]


def test_disaster_recovery_checks_rpo_and_rto_independently():
    r = s.DisasterRecoveryObjective().evaluate(15, 60, 20, 40)
    assert r["failures"] == ["rpo"]
    assert s.DisasterRecoveryObjective().evaluate(15, 60, 10, 40)["ready"]
    with pytest.raises(ValueError):
        s.DisasterRecoveryObjective().evaluate(1, 1, float("inf"), 1)
