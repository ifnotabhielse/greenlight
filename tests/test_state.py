from greenlight.state import Phase, is_terminal, next_step_index


def test_terminal_phases():
    assert is_terminal(Phase.PROMOTED.value)
    assert is_terminal(Phase.ROLLED_BACK.value)
    assert not is_terminal(Phase.PROGRESSING.value)
    assert not is_terminal(None)


def test_step_progression():
    steps = [5, 25, 50, 100]
    assert next_step_index(0, steps) == 1
    assert next_step_index(2, steps) == 3
    assert next_step_index(3, steps) is None
