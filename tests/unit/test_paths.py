"""Data-root resolution and the cloud-sync guard."""

from __future__ import annotations

from clipper import paths


class TestDataRoot:
    def test_defaults_under_the_repo(self, monkeypatch):
        monkeypatch.delenv("CLIPPER_DATA_DIR", raising=False)
        paths.data_root.cache_clear()
        assert paths.data_root() == paths.REPO_ROOT / "data"
        paths.data_root.cache_clear()

    def test_env_override_wins(self, data_root):
        assert paths.data_root() == data_root

    def test_subdirs_hang_off_the_root(self, data_root):
        assert paths.work_dir("abc123").parent.parent == data_root
        assert paths.work_dir("abc123").name == "abc123"
        assert paths.downloads_dir().parent == data_root
        assert paths.logs_dir().parent == data_root

    def test_assets_stay_in_the_repo_not_the_data_root(self, data_root):
        """Fonts and models are per-checkout, not per-data-directory."""
        assert paths.fonts_dir().is_relative_to(paths.REPO_ROOT)
        assert not paths.fonts_dir().is_relative_to(data_root)

    def test_ensure_is_idempotent(self, tmp_path):
        target = tmp_path / "a" / "b"
        assert paths.ensure(target) == target
        assert paths.ensure(target).is_dir()


class TestSyncGuard:
    """A data root inside OneDrive means every intermediate WAV gets uploaded."""

    def test_detects_a_path_inside_a_sync_root(self, tmp_path, monkeypatch):
        sync = tmp_path / "OneDrive"
        inside = sync / "projects" / "clipper-data"
        inside.mkdir(parents=True)
        monkeypatch.setenv("OneDrive", str(sync))
        assert paths.synced_parent(inside) == sync.resolve()

    def test_ignores_a_path_outside_any_sync_root(self, tmp_path, monkeypatch):
        sync = tmp_path / "OneDrive"
        sync.mkdir()
        outside = tmp_path / "local-data"
        outside.mkdir()
        monkeypatch.setenv("OneDrive", str(sync))
        assert paths.synced_parent(outside) is None

    def test_no_sync_env_means_no_warning(self, tmp_path, monkeypatch):
        for var in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer", "Dropbox"):
            monkeypatch.delenv(var, raising=False)
        assert paths.synced_parent(tmp_path) is None

    def test_sibling_prefix_is_not_a_match(self, tmp_path, monkeypatch):
        """'C:/OneDriveOld' must not count as being inside 'C:/OneDrive'."""
        sync = tmp_path / "OneDrive"
        sync.mkdir()
        sibling = tmp_path / "OneDriveOld"
        sibling.mkdir()
        monkeypatch.setenv("OneDrive", str(sync))
        assert paths.synced_parent(sibling) is None
