from dlsite_organizer import __version__, application_user_agent


def test_v121_is_the_authoritative_release_version() -> None:
    assert __version__ == "1.2.1"
    assert application_user_agent() == "dlsite-organizer/1.2.1"
