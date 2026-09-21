"""Config validation, with the authorization gate as the headline case."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from clipper.config import (
    DEFAULT_CONFIG_PATH,
    CampaignConfig,
    CandidatesConfig,
    Config,
    QAConfig,
    RenderConfig,
    WeightsConfig,
)


class TestShippedConfig:
    def test_default_yaml_parses(self):
        """config/default.yaml must always validate -- doctor depends on it."""
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert cfg.render.width == 1080
        assert cfg.render.height == 1920

    def test_load_missing_file_falls_back_to_defaults(self, tmp_path):
        cfg = Config.load(tmp_path / "nope.yaml")
        assert cfg == Config()

    def test_unknown_key_is_rejected(self, tmp_path):
        """A typo must fail loudly instead of silently using the default."""
        path = tmp_path / "c.yaml"
        path.write_text(yaml.safe_dump({"render": {"widht": 1080}}), encoding="utf-8")
        with pytest.raises(ValidationError):
            Config.load(path)

    def test_example_campaign_parses(self):
        from clipper.paths import REPO_ROOT

        campaign = CampaignConfig.load(REPO_ROOT / "campaigns" / "example.yaml")
        assert campaign.name == "example-campaign"
        assert campaign.source_authorization


class TestAuthorizationGate:
    """Section 2: clipper refuses to run without a real source authorization."""

    def test_missing_field_is_rejected(self, tmp_path, valid_campaign_dict):
        valid_campaign_dict.pop("source_authorization")
        path = tmp_path / "c.yaml"
        path.write_text(yaml.safe_dump(valid_campaign_dict), encoding="utf-8")
        with pytest.raises(ValueError, match="source_authorization"):
            CampaignConfig.load(path)

    @pytest.mark.parametrize("value", ["", "   ", "TBD", "todo", "n/a", "none", "test", "xxx"])
    def test_placeholder_is_rejected(self, valid_campaign_dict, value):
        valid_campaign_dict["source_authorization"] = value
        with pytest.raises(ValidationError, match="source_authorization"):
            CampaignConfig.model_validate(valid_campaign_dict)

    def test_real_authorization_is_accepted_and_stripped(self, valid_campaign_dict):
        valid_campaign_dict["source_authorization"] = "  https://whop.com/campaign/abc  "
        campaign = CampaignConfig.model_validate(valid_campaign_dict)
        assert campaign.source_authorization == "https://whop.com/campaign/abc"

    def test_burned_credit_requires_text(self, valid_campaign_dict):
        valid_campaign_dict["burn_credit_in_video"] = True
        valid_campaign_dict["required_credit_text"] = ""
        with pytest.raises(ValidationError, match="required_credit_text"):
            CampaignConfig.model_validate(valid_campaign_dict)

    def test_burned_credit_with_text_is_fine(self, valid_campaign_dict):
        valid_campaign_dict["burn_credit_in_video"] = True
        valid_campaign_dict["required_credit_text"] = "Source: @creator"
        assert CampaignConfig.model_validate(valid_campaign_dict).burn_credit_in_video


class TestInternalConsistency:
    def test_min_must_be_below_max_duration(self):
        with pytest.raises(ValidationError, match="min_seconds"):
            CandidatesConfig(min_seconds=60, max_seconds=30)

    def test_target_band_must_sit_inside_bounds(self):
        with pytest.raises(ValidationError, match="target_seconds"):
            CandidatesConfig(min_seconds=20, max_seconds=55, target_seconds=(10, 45))

    def test_weights_cannot_all_be_zero(self):
        with pytest.raises(ValidationError, match="zero"):
            WeightsConfig(llm=0, heatmap=0, audio=0, text=0)

    def test_weights_need_not_sum_to_one(self):
        """They are renormalized at runtime over the available signals."""
        w = WeightsConfig(llm=1.0, heatmap=1.0, audio=1.0, text=1.0)
        assert sum(w.as_dict().values()) == 4.0

    def test_safe_area_cannot_swallow_the_frame(self):
        with pytest.raises(ValidationError, match="safe_area"):
            RenderConfig(height=1920, safe_area={"top": 1000, "bottom": 1000, "side": 90})

    def test_qa_lufs_window_must_be_ordered(self):
        with pytest.raises(ValidationError, match="min_lufs"):
            QAConfig(min_lufs=-11, max_lufs=-18)

    def test_config_is_frozen(self):
        """Config is shared across stages; accidental mutation would be a bug."""
        cfg = Config()
        with pytest.raises(ValidationError):
            cfg.render.width = 720
