import pytest

from app import store
from app.collect import collect
from app.errors import ConfigError, SourceFetchError
from app.prefilter import build_matcher, load_phrases, run_prefilter
from tests.conftest import make_post
from tests.fakes import FakeSource


def test_collect_counts_new_posts_and_skips_duplicates(conn):
    source = FakeSource("r/excel", [make_post("reddit_1"), make_post("reddit_2")])
    first = collect(conn, [source], http=None)
    second = collect(conn, [source], http=None)
    assert first.new_per_source == {"r/excel": 2}
    assert second.new_per_source == {"r/excel": 0}
    assert first.total_new == 2


def test_collect_continues_when_one_source_fails(conn):
    broken = FakeSource("r/broken", error=SourceFetchError("HTTP 503"))
    good = FakeSource("r/excel", [make_post("reddit_1")])
    report = collect(conn, [broken, good], http=None)
    assert report.errors == {"r/broken": "HTTP 503"}
    assert report.new_per_source == {"r/excel": 1}


def test_matcher_whole_words_and_flexible_spaces():
    matcher = build_matcher(["script", "copy and paste", "make.com"])
    assert matcher.search("I need a SCRIPT")
    assert matcher.search("I copy   and\npaste all day")
    assert matcher.search("works with make.com")
    assert not matcher.search("Job description here")
    assert not matcher.search("makeXcom")


def test_load_phrases_ignores_comments_and_blanks(tmp_path):
    path = tmp_path / "phrases.txt"
    path.write_text("# comment\n\nAutomate\n  zapier  \n", encoding="utf-8")
    assert load_phrases(path) == ["automate", "zapier"]


def test_load_phrases_errors(tmp_path):
    with pytest.raises(ConfigError):
        load_phrases(tmp_path / "missing.txt")
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_phrases(empty)


def test_real_phrase_file_loads():
    from app.config import CONFIG_DIR

    assert "automate" in load_phrases(CONFIG_DIR / "phrases.txt")


def test_run_prefilter_marks_posts(conn):
    store.save_post(conn, make_post("reddit_yes", title="How to automate this?", body=""))
    store.save_post(conn, make_post("reddit_no", title="My cat photo", body="So cute"))
    report = run_prefilter(conn, ["automate"])
    assert (report.checked, report.passed) == (2, 1)
    passed = {r[0]: r[1] for r in conn.execute("SELECT id, passed_filter FROM posts")}
    assert passed == {"reddit_yes": 1, "reddit_no": 0}
    # Running again checks nothing new.
    assert run_prefilter(conn, ["automate"]).checked == 0
