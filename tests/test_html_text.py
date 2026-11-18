from app.html_text import html_to_text


def test_tags_removed_and_entities_decoded():
    assert html_to_text("<p>Hello &amp; <b>welcome</b></p>") == "Hello & welcome"


def test_block_tags_become_line_breaks():
    assert html_to_text("<p>One</p><p>Two</p>") == "One\n\nTwo"
    assert html_to_text("line one<br>line two") == "line one\nline two"


def test_scripts_are_dropped():
    assert html_to_text("<p>Hi</p><script>alert('x')</script>") == "Hi"


def test_reddit_footer_is_removed():
    html = (
        '<!-- SC_OFF --><div class="md"><p>Need help automating</p></div><!-- SC_ON -->'
        " &#32; submitted by &#32; <a href='x'>/u/someone</a> [link] [comments]"
    )
    assert html_to_text(html) == "Need help automating"


def test_empty_input():
    assert html_to_text("") == ""
