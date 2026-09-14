from src.pipeline.schedule.scheduler import pick_least_loaded


def test_round_robin_when_all_idle() -> None:
    loads = {"A": 0, "B": 0, "C": 0}
    names = []
    rr = 0
    for _ in range(6):
        name, rr = pick_least_loaded(loads, concurrency=3, rr=rr)
        assert name is not None
        names.append(name)
        loads[name] += 1
        # 模拟传得很快，立刻空闲
        loads[name] -= 1
    assert names == ["A", "B", "C", "A", "B", "C"]


def test_prefers_least_loaded() -> None:
    loads = {"A": 2, "B": 0, "C": 1}
    name, _rr = pick_least_loaded(loads, concurrency=3, rr=0)
    assert name == "B"


def test_skips_full_workers() -> None:
    loads = {"A": 3, "B": 3, "C": 1}
    name, _rr = pick_least_loaded(loads, concurrency=3, rr=0)
    assert name == "C"
    name, _rr = pick_least_loaded({"A": 3, "B": 3, "C": 3}, concurrency=3, rr=0)
    assert name is None
