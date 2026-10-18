from patchtrace.cli import build_parser, main


def test_parser_has_check_option():
    args = build_parser().parse_args(["--check"])
    assert args.check is True


def test_main_check_prints_health(capsys):
    assert main(["--check"]) == 0
    assert "patchtrace-demo: ok" in capsys.readouterr().out
