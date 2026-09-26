"""Open-licence campaigns refuse videos their licence rules do not cover.

A Creative Commons label only counts on the owner's own upload and only for
that video: channels mix licensed and standard-licence uploads (checked on
the 85 South, Neal Brennan and Brilliant Idiots channels), and anyone can set
CC on a re-upload of footage they do not own.
"""

from __future__ import annotations

import pytest

from clipper.config import CampaignConfig
from clipper.runner import RightsError, check_rights

CC = "Creative Commons Attribution license (reuse allowed)"
URL = "https://www.youtube.com/watch?v=abc123"


def campaign(**kw) -> CampaignConfig:
    return CampaignConfig.model_validate({
        "name": "cc-85south", "source_authorization": "CC BY on the owner's own uploads",
        "require_license": "Creative Commons Attribution",
        "allowed_channels": ["UC4m46pCBkMEyy8gk26WKqbA"], **kw})


def listing(license=CC, channel="The 85 South Comedy Show",
            channel_id="UC4m46pCBkMEyy8gk26WKqbA"):
    return lambda url: {"license": license, "channel": channel, "channel_id": channel_id,
                        "title": "An episode"}


def test_the_owners_cc_upload_passes():
    check_rights(URL, campaign(), probe_fn=listing())


def test_a_standard_licence_upload_is_refused():
    with pytest.raises(RightsError, match="standard YouTube licence"):
        check_rights(URL, campaign(), probe_fn=listing(license=""))


def test_a_cc_reupload_by_someone_else_is_refused():
    with pytest.raises(RightsError, match="re-upload"):
        check_rights(URL, campaign(), probe_fn=listing(channel="Random Clips", channel_id="UCx"))


def test_the_channel_may_be_named_instead_of_its_id():
    check_rights(URL, campaign(allowed_channels=["The 85 South Comedy Show"]),
                 probe_fn=listing(channel_id="UCother"))


def test_a_local_file_cannot_show_a_licence():
    with pytest.raises(RightsError, match="local file"):
        check_rights("C:/Users/marc/Downloads/ep.mp4", campaign(), probe_fn=listing())


def test_campaigns_without_licence_rules_are_not_checked():
    plain = CampaignConfig.model_validate({"name": "fx", "source_authorization": "Vyro FX brief"})

    def must_not_be_called(url):
        raise AssertionError("probed a campaign without licence rules")
    check_rights("C:/local.mov", plain, probe_fn=must_not_be_called)
