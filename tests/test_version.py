from dlsite_organizer import __version__, application_user_agent


def test_v131_is_the_authoritative_release_version() -> None:
    assert __version__ == "1.3.1"
    assert application_user_agent() == "dlsite-organizer/1.3.1"
