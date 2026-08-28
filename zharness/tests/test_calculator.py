from zharness.tools.calculator import add


def test_add() -> None:
    assert add.invoke({"a": 123, "b": 456}) == 579
