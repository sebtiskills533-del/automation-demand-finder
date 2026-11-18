import pytest

from app.privacy import EMAIL_PLACEHOLDER, PHONE_PLACEHOLDER, strip_contact_details


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Email me at jane.doe+shop@gmail.com please", "jane.doe"),
        ("contact: JOHN_SMITH@company.co.uk", "JOHN_SMITH"),
        ("reach me jane (at) gmail (dot) com", "jane"),
        ("reach me jane [at] gmail [dot] com", "jane"),
        ("reach me jane at gmail dot com", "jane"),
    ],
)
def test_emails_are_removed(text, secret):
    cleaned = strip_contact_details(text)
    assert EMAIL_PLACEHOLDER in cleaned
    assert secret not in cleaned
    assert "@" not in cleaned


@pytest.mark.parametrize(
    "text",
    [
        "Call 555-123-4567 today",
        "Call (555) 123-4567 today",
        "UK mobile 07700 900123",
        "Whatsapp +44 7700 900123",
        "Phone: +1.555.123.4567",
        "India +91 98765 43210",
        "short intl +49 30 1234",  # + marker and 8 digits
    ],
)
def test_phone_numbers_are_removed(text):
    cleaned = strip_contact_details(text)
    assert PHONE_PLACEHOLDER in cleaned
    assert "4567" not in cleaned and "900123" not in cleaned and "43210" not in cleaned


@pytest.mark.parametrize(
    "text",
    [
        "Between 2020-2024 I sold 1500 items",
        "It costs $1,299.99 per year",
        "Order #123456 arrived",
        "Version 3.11.4 of Python",
    ],
)
def test_ordinary_numbers_are_kept(text):
    assert strip_contact_details(text) == text


def test_long_numbers_inside_urls_are_kept():
    url = "https://www.reddit.com/r/excel/comments/1234567890123/my_post/"
    assert strip_contact_details(f"See {url} thanks") == f"See {url} thanks"


def test_email_inside_url_is_still_removed():
    cleaned = strip_contact_details("mailto link https://x.com/?to=jane@gmail.com")
    assert "jane@gmail.com" not in cleaned


def test_empty_text():
    assert strip_contact_details("") == ""
    assert strip_contact_details(None) == ""  # type: ignore[arg-type]


def test_phrase_with_at_and_dot_words_is_not_an_email():
    text = "I looked at the dashboard and it is great"
    assert strip_contact_details(text) == text
