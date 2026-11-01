"""DeploySpecter: finds and reproduces data corruption from rolling migrations."""


def main() -> None:
    from .cli import main as cli_main

    cli_main()
