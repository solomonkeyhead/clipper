"""`clipper doctor` behaviour: it must never crash, and must run on this machine."""

from __future__ import annotations

from typer.testing import CliRunner

from clipper.cli import app
from clipper.doctor import ALL_CHECKS, Status, run_checks

runner = CliRunner()


class TestChecks:
    def test_every_check_returns_a_result(self):
        results = list(run_checks())
        assert len(results) == len(ALL_CHECKS)
        assert all(r.name and isinstance(r.status, Status) for r in results)

    def test_a_crashing_check_becomes_a_fail_not_a_traceback(self, monkeypatch):
        """doctor is what users run when things are broken; it cannot itself break."""
        def exploding_check():
            raise RuntimeError("boom")

        monkeypatch.setattr("clipper.doctor.ALL_CHECKS", (exploding_check,))
        (result,) = list(run_checks())
        assert result.status is Status.FAIL
        assert "boom" in result.detail

    def test_non_ok_results_carry_a_fix(self):
        """A warning with no remedy is just noise."""
        for r in run_checks():
            if r.status is not Status.OK:
                assert r.fix, f"{r.name} is {r.status} but offers no fix"

    def test_python_check_passes_on_the_supported_interpreter(self):
        from clipper.doctor import check_python

        assert check_python().status is Status.OK

    def test_config_check_passes_against_the_shipped_yaml(self):
        from clipper.doctor import check_config

        assert check_config().status is Status.OK

    def test_api_key_check_warns_when_the_backend_key_is_absent(self):
        """The autouse fixture clears the keys, so the gemini default must warn."""
        from clipper.doctor import check_api_keys

        result = check_api_keys()
        assert result.status is Status.WARN
        assert "GEMINI_API_KEY" in result.fix


class TestCli:
    def test_doctor_exits_zero_when_nothing_is_blocking(self):
        """Exit 0 on warnings, non-zero only on FAIL."""
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, result.output

    def test_doctor_exits_one_when_a_check_fails(self, monkeypatch):
        from clipper.doctor import Status, _result

        monkeypatch.setattr(
            "clipper.doctor.ALL_CHECKS",
            (lambda: _result("Broken", Status.FAIL, "detail", fix="do the thing"),),
        )
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "do the thing" in result.output

    def test_version_flag(self):
        from clipper import __version__

        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_unimplemented_commands_exit_two_and_name_their_phase(self):
        """Stubs must be honest about not working rather than failing obscurely."""
        for argv, phase in [
            (["transcribe", "x.mp4"], "Phase 2"),
            (["score", "abc"], "Phase 3"),
            (["render", "abc"], "Phase 4"),
            (["learn", "--performance", "p.csv"], "Phase 6"),
        ]:
            result = runner.invoke(app, argv)
            assert result.exit_code == 2, argv
            assert phase in result.output, argv
