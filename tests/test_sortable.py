from gumptionchain.sortable import SortSpec, parse_sort


def test_parse_sort_defaults_when_absent():
    spec = parse_sort({}, allowed={'a', 'b'}, default_key='b')
    assert spec.key == 'b'
    assert spec.direction == 'desc'


def test_parse_sort_rejects_unknown_key_falls_back_to_default():
    spec = parse_sort({'sort': 'evil'}, allowed={'a', 'b'}, default_key='a')
    assert spec.key == 'a'


def test_parse_sort_accepts_allowed_key_and_direction():
    spec = parse_sort(
        {'sort': 'a', 'dir': 'asc'}, allowed={'a', 'b'}, default_key='b'
    )
    assert spec.key == 'a'
    assert spec.direction == 'asc'


def test_parse_sort_rejects_bad_direction():
    spec = parse_sort(
        {'sort': 'a', 'dir': 'sideways'}, allowed={'a'}, default_key='a'
    )
    assert spec.direction == 'desc'


def test_parse_sort_honors_default_dir():
    spec = parse_sort({}, allowed={'a'}, default_key='a', default_dir='asc')
    assert spec.direction == 'asc'


def test_toggled_flips_only_the_active_column():
    spec = SortSpec(key='a', direction='desc')
    assert spec.toggled('a') == 'asc'
    assert spec.toggled('b') == 'desc'
    assert SortSpec(key='a', direction='asc').toggled('a') == 'desc'


def test_indicator_marks_only_the_active_column():
    assert SortSpec(key='a', direction='desc').indicator('a') == '▼'
    assert SortSpec(key='a', direction='asc').indicator('a') == '▲'
    assert SortSpec(key='a', direction='desc').indicator('b') == ''
