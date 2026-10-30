"""日数プルダウンの選択肢と表示文（Streamlit非依存の部分）."""

from src.count_select import UNSET_LABEL, count_options, format_count


def test_options_start_with_unset():
    assert count_options(range(1, 8), None) == [None, 1, 2, 3, 4, 5, 6, 7]


def test_saved_value_inside_the_range_is_not_duplicated():
    assert count_options(range(1, 8), 5) == [None, 1, 2, 3, 4, 5, 6, 7]


def test_saved_value_outside_the_range_is_kept():
    assert count_options(range(1, 15), 20) == [None, *range(1, 15), 20]


def test_labels():
    assert format_count(None, "週{}日") == UNSET_LABEL == "未設定"
    assert format_count(5, "週{}日") == "週5日"
    assert format_count(3, "{}日まで") == "3日まで"
